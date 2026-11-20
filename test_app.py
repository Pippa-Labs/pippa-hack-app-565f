"""Tests for Pocket Londoner, run through LocalRunner with the network stubbed."""

from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest
from pippa_agentapp_sdk import LocalRunner, RateLimitedError, ServiceUnavailableError

import l1_clock
import l1_http
from app import App

# 14:30 GMT on a Friday in November: London time equals UTC, before sunset.
NOW = datetime(2026, 11, 20, 14, 30, tzinfo=timezone.utc)


# --- Canned API payloads ---
def _forecast(rain_chance: int = 10, rain_mm: float = 0.0) -> dict:
    """Two days of Open-Meteo hourly columns from midnight London time."""
    start = datetime(2026, 11, 20, 0, 0)
    times = [(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(48)]
    column = lambda value: [value] * 48  # noqa: E731
    return {
        "hourly": {
            "time": times,
            "temperature_2m": column(10.2), "apparent_temperature": column(8.0),
            "precipitation_probability": column(rain_chance), "precipitation": column(rain_mm),
            "wind_speed_10m": column(12.0), "wind_gusts_10m": column(25.0),
            "uv_index": column(0.5), "weather_code": column(2), "is_day": column(1),
        },
        "daily": {
            "sunrise": ["2026-11-20T07:26", "2026-11-21T07:28"],
            "sunset": ["2026-11-20T16:04", "2026-11-21T16:03"],
        },
    }


def _forecast_from(hour: int, **later) -> dict:
    """A forecast that changes from `hour` on, e.g. _forecast_from(21, precipitation_probability=70)."""
    forecast = _forecast()
    hourly = forecast["hourly"]
    hourly.setdefault("snowfall", [0.0] * 48)
    for field, value in later.items():
        hourly[field] = hourly[field][:hour] + [value] * (48 - hour)
    return forecast


def _air() -> dict:
    start = datetime(2026, 11, 20, 0, 0)
    times = [(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(48)]
    return {"hourly": {"time": times, "european_aqi": [28] * 48, "pm2_5": [4.2] * 48,
                       "nitrogen_dioxide": [16.0] * 48}}


LINE_STATUS = [
    {"name": "Central", "lineStatuses": [{"statusSeverity": 9, "statusSeverityDescription": "Minor Delays",
                                          "reason": "Central Line: Minor delays due to a signal failure. More later."}]},
    {"name": "Victoria", "lineStatuses": [{"statusSeverity": 10, "statusSeverityDescription": "Good Service"}]},
]

POSTCODE = {"result": {"latitude": 51.5136, "longitude": -0.1365, "postcode": "W1D 3QF"}}


def _search(stop_id: str, name: str) -> dict:
    """A TfL StopPoint/Search answer with one match."""
    return {"matches": [{"id": stop_id, "name": name, "lat": 51.515, "lon": -0.141}]}


def _stop(name: str, properties: dict, children: tuple = ()) -> dict:
    """A TfL StopPoint answer with Facility properties and child stop ids."""
    return {
        "commonName": name,
        "additionalProperties": [{"category": "Facility", "key": k, "value": v} for k, v in properties.items()],
        "children": [{"id": child} for child in children],
    }


def _arrival(line: str, destination: str, seconds: int, platform: str = "") -> dict:
    return {"lineName": line, "destinationName": destination, "timeToStation": seconds, "platformName": platform}


def _leg(mode: str, summary: str, line: str, minutes: int) -> dict:
    return {"mode": {"id": mode}, "instruction": {"summary": summary},
            "routeOptions": [{"name": line}], "duration": minutes}


def _journey(start: str, arrival: str, minutes: int, legs: list, fare_pence: int | None = None) -> dict:
    """A TfL JourneyResults answer with one journey; times are London wall-clock text."""
    journey = {"duration": minutes, "startDateTime": start, "arrivalDateTime": arrival, "legs": legs}
    if fare_pence is not None:
        journey["fare"] = {"totalCost": fare_pence}
    return {"journeys": [journey]}


def _fare_sections(peak: str, off_peak: str) -> list:
    tickets = [
        {"passengerType": "Adult", "ticketType": {"type": "Pay as you go"}, "ticketTime": {"type": time}, "cost": cost}
        for time, cost in (("Peak", peak), ("Off Peak", off_peak))
    ]
    return [{"rows": [{"ticketsAvailable": tickets}]}]


def _crimes(*categories: str) -> list:
    return [{"category": category} for category in categories]


# --- Fake network ---
class FakeNet:
    """Answers by URL substring; a value that is an exception is raised instead."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.urls: list[str] = []

    def __call__(self, url: str, timeout: float = 6.0) -> bytes:
        self.urls.append(url)
        for fragment, answer in self.routes.items():
            if fragment in url:
                if isinstance(answer, BaseException):
                    raise answer
                return json.dumps(answer).encode()
        raise AssertionError(f"unexpected request {url}")


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.com", code, "error", {}, None)


@pytest.fixture
def net(monkeypatch):
    """Install a FakeNet with the default happy-path routes."""
    fake = FakeNet({
        "/Line/Mode/national-rail/": [],
        "/Line/Mode/": LINE_STATUS,
        "api.postcodes.io/postcodes/": POSTCODE,
        "air-quality-api.open-meteo.com": _air(),
        "api.open-meteo.com/v1/forecast": _forecast(),
    })
    monkeypatch.setattr(l1_http, "fetch_bytes", fake)
    return fake


def call(outcome: str, inputs: dict, *, explicit: bool = False):
    """Run one outcome the way the runtime does."""
    return LocalRunner(App()).call(
        outcome, inputs, granted_secrets={}, now=NOW, request_id="test-1", explicit=explicit,
    ).result


# --- Manifest ---
def test_the_manifest_is_valid():
    app = App()
    assert app.manifest.app_id == "hack.app_565f"
    assert {o.id for o in app.manifest.outcomes} == {
        "journey.plan", "transport.live", "transportation.fares", "outdoors.walk", "safety.help",
    }


# --- transport.live ---
def test_line_status_lists_only_lines_with_problems(net):
    result = call("transport.live", {"task": "status"})
    assert result.data["task"] == "status"
    assert result.data["headline"].startswith("1 of 2 lines")
    assert result.data["key_points"] == ["Central: Minor Delays"]
    assert result.data["source"] == "Powered by TfL Open Data"


def test_one_named_line(net):
    result = call("transport.live", {"task": "status", "line": "Victoria line"})
    assert result.data["headline"] == "The Victoria line has good service."


def test_tfl_blocked_is_an_honest_error(net):
    net.routes["/Line/Mode/"] = http_error(403)
    with pytest.raises(l1_http.Blocked) as failure:
        call("transport.live", {"task": "status"})
    assert failure.value.user_message == "TfL live data is unavailable right now."


def test_tfl_timeout_is_retryable(net):
    net.routes["/Line/Mode/"] = TimeoutError("slow")
    with pytest.raises(ServiceUnavailableError):
        call("transport.live", {"task": "status"})


# --- outdoors.walk ---
def test_weather_watch_for_a_postcode(net):
    result = call("outdoors.walk", {"task": "weather", "place": "W1D 3QF"})
    assert result.data["temperature_c"] == 10.2
    assert "W1D 3QF" in result.data["headline"]
    assert "Sunset 16:04" in result.data["key_points"]


def test_walk_now_says_go_in_dry_weather(net):
    result = call("outdoors.walk", {"task": "walk", "minutes": 30})
    assert result.data["verdict"] == "go"
    assert "central London" in result.data["headline"]


def test_walk_now_says_wait_in_heavy_rain(net):
    net.routes["api.open-meteo.com/v1/forecast"] = _forecast(rain_chance=90, rain_mm=3.0)
    result = call("outdoors.walk", {"task": "walk", "minutes": 30})
    assert result.data["verdict"] == "wait"


def test_air_quality_failure_still_answers(net):
    net.routes["air-quality-api.open-meteo.com"] = http_error(500)
    result = call("outdoors.walk", {"task": "weather"})
    assert not any("Air quality" in point for point in result.data["key_points"])


def test_unknown_postcode_is_refused(net):
    net.routes["api.postcodes.io/postcodes/"] = http_error(404)
    with pytest.raises(l1_http.NotFound):
        call("outdoors.walk", {"task": "weather", "place": "ZZ9 9ZZ"})


def test_picked_by_name_with_no_task_still_answers(net):
    result = call("outdoors.walk", {}, explicit=True)
    assert result.data["task"] == "weather"


# --- transport.live: departures ---
def test_departures_from_a_station_expand_the_hub(net):
    net.routes.update({
        "/StopPoint/Search/": _search("HUBOXC", "Oxford Circus Underground Station"),
        "/StopPoint/HUBOXC": _stop("Oxford Circus Underground Station", {}, ("940GZZLUOXC", "490G00001")),
        "/StopPoint/940GZZLUOXC/Arrivals": [
            _arrival("Central", "Ealing Broadway Underground Station", 300, "Westbound - Platform 1"),
            _arrival("Victoria", "Brixton Underground Station", 120, "Southbound - Platform 4"),
        ],
    })
    result = call("transport.live", {"task": "departures", "place": "Oxford Circus"})
    assert result.data["place"] == "Oxford Circus"
    assert result.data["headline"] == "Next from Oxford Circus: Victoria to Brixton in 2 min."
    assert result.data["key_points"] == ["Central to Ealing Broadway in 5 min"]


def test_departures_fall_back_to_nearby_bus_stops(net):
    net.routes.update({
        "/StopPoint/Search/": {"matches": []},
        "/StopPoint?": {"stopPoints": [
            {"naptanId": "490G00001", "commonName": "Wardour Street", "indicator": "Stop A", "distance": 120},
        ]},
        "/StopPoint/490G00001/Arrivals": [_arrival("38", "Victoria", 240)],
    })
    result = call("transport.live", {"task": "departures", "place": "W1D 3QF"})
    assert result.data["place"] == "bus stops near W1D 3QF"
    assert result.data["headline"] == "Next from bus stops near W1D 3QF: 38 to Victoria in 4 min."


def test_departures_with_no_predictions_say_so(net):
    net.routes.update({
        "/StopPoint/Search/": _search("940GZZLUOXC", "Oxford Circus Underground Station"),
        "/StopPoint/940GZZLUOXC/Arrivals": [],
    })
    result = call("transport.live", {"task": "departures", "place": "Oxford Circus"})
    assert result.data["headline"].startswith("No departures predicted")


def test_departures_without_a_place_need_input(net):
    with pytest.raises(l1_http.NeedsInput):
        call("transport.live", {"task": "departures"})


# --- transport.live: bikes ---
def test_bike_docks_are_totalled(net):
    def dock(name: str, bikes: str, ebikes: str, empty: str) -> dict:
        props = [{"key": "NbBikes", "value": bikes}, {"key": "NbEBikes", "value": ebikes},
                 {"key": "NbEmptyDocks", "value": empty}]
        return {"commonName": name, "additionalProperties": props, "lat": 51.52, "lon": -0.11}

    net.routes.update({
        "/BikePoint/Search": [{"id": "BikePoints_1"}, {"id": "BikePoints_2"}],
        "/BikePoint/BikePoints_1": dock("River Street , Clerkenwell", "8", "2", "10"),
        "/BikePoint/BikePoints_2": dock("Exmouth Market, Clerkenwell", "4", "0", "5"),
    })
    result = call("transport.live", {"task": "bikes", "place": "Clerkenwell"})
    assert result.data["headline"] == "12 bikes and 15 free docks at 2 docks near Clerkenwell."
    assert result.data["key_points"][0] == "River Street: 8 bikes"


def test_no_bike_docks_is_not_found(net):
    net.routes["/BikePoint/Search"] = []
    with pytest.raises(l1_http.NotFound):
        call("transport.live", {"task": "bikes", "place": "Narnia"})


def test_bikes_without_a_place_need_input(net):
    with pytest.raises(l1_http.NeedsInput):
        call("transport.live", {"task": "bikes"})


# --- transport.live: step-free ---
def _step_free_routes(net, faults: list) -> None:
    net.routes.update({
        "/StopPoint/Search/": _search("940GZZLUOXC", "Oxford Circus Underground Station"),
        "/StopPoint/940GZZLUOXC": _stop("Oxford Circus Underground Station", {"Lifts": "2", "AccessViaLift": "Yes"}),
        "/Disruptions/Lifts/v2": faults,
    })


def test_step_free_mentions_a_lift_fault(net):
    _step_free_routes(net, [{"stationUniqueId": "940GZZLUOXC", "message": "Oxford Circus: Lift 3 out of service"}])
    result = call("transport.live", {"task": "step_free", "place": "Oxford Circus"})
    assert result.data["place"] == "Oxford Circus"
    assert "out of service" in result.data["headline"]
    assert "1 lift fault(s) reported now" in result.data["key_points"]


def test_step_free_ignores_faults_at_other_stations(net):
    _step_free_routes(net, [{"stationUniqueId": "940GZZLUBXN", "message": "Brixton: Lift 1 out of service"}])
    result = call("transport.live", {"task": "step_free", "place": "Oxford Circus"})
    assert result.data["headline"] == "Oxford Circus is step-free via lift; no lift faults reported."


def test_step_free_without_a_place_needs_input(net):
    with pytest.raises(l1_http.NeedsInput):
        call("transport.live", {"task": "step_free"})


# --- transportation.fares ---
def test_best_ticket_for_a_week_in_zones_1_to_3(net):
    result = call("transportation.fares", {"task": "best_ticket", "zones": "1-3", "stay_days": 7})
    assert result.data["cost_gbp"] == 52.5
    assert result.data["best_option"] == "Pay as you go with contactless"
    assert "at most £52.50" in result.data["headline"]


def test_best_ticket_adds_the_oyster_fee_when_there_is_no_card(net):
    result = call("transportation.fares", {"task": "best_ticket", "zones": "1-3", "card": "none"})
    assert result.data["cost_gbp"] == 59.5
    assert result.data["best_option"] == "Pay as you go with Oyster"


def test_best_ticket_for_buses_only(net):
    result = call("transportation.fares", {"task": "best_ticket", "bus_only": True})
    assert result.data["cost_gbp"] == 26.1
    assert "on buses" in result.data["headline"]


def test_best_ticket_with_unknown_zones_needs_input(net):
    with pytest.raises(l1_http.NeedsInput) as failure:
        call("transportation.fares", {"task": "best_ticket", "zones": "zone 9"})
    assert "1-2, 1-3, 1-4 or 1-6" in failure.value.user_message


def test_single_fare_expands_a_hub_station(net):
    net.routes.update({
        "/StopPoint/Search/Oxford": _search("HUBOXC", "Oxford Circus Underground Station"),
        "/StopPoint/Search/Brixton": _search("940GZZLUBXN", "Brixton Underground Station"),
        "/StopPoint/HUBOXC": _stop("Oxford Circus Underground Station", {}, ("940GZZLUOXC",)),
        "/FareTo/": _fare_sections("2.90", "1.90"),
    })
    result = call("transportation.fares", {"from_place": "Oxford Circus", "to_place": "Brixton"})
    assert result.data["task"] == "single_fare"
    assert result.data["cost_gbp"] == 1.9
    assert result.data["best_option"] == "Pay as you go (Off Peak)"
    assert result.data["headline"] == "£1.90 off-peak with contactless, Oxford Circus to Brixton."
    assert "Peak £2.90" in result.data["key_points"]
    assert any("/StopPoint/940GZZLUOXC/FareTo/940GZZLUBXN" in url for url in net.urls)


def test_single_fare_can_ask_for_the_peak_price(net):
    net.routes.update({
        "/StopPoint/Search/": _search("940GZZLUOXC", "Oxford Circus Underground Station"),
        "/FareTo/": _fare_sections("2.90", "1.90"),
    })
    result = call("transportation.fares",
                  {"task": "single_fare", "from_place": "Oxford", "to_place": "Brixton", "peak": True})
    assert result.data["cost_gbp"] == 2.9


def test_single_fare_without_a_destination_needs_input(net):
    with pytest.raises(l1_http.NeedsInput):
        call("transportation.fares", {"task": "single_fare", "from_place": "Oxford Circus"})


def test_single_fare_with_no_published_fare_is_not_found(net):
    net.routes.update({
        "/StopPoint/Search/": _search("940GZZLUOXC", "Oxford Circus Underground Station"),
        "/FareTo/": [],
    })
    with pytest.raises(l1_http.NotFound):
        call("transportation.fares", {"task": "single_fare", "from_place": "A", "to_place": "B"})


def test_airport_fare_for_heathrow_without_a_start(net):
    result = call("transportation.fares", {"task": "airport", "airport": "heathrow"})
    assert result.data["cost_gbp"] == 15.5
    assert result.data["best_option"] == "Elizabeth line from zone 1"
    assert result.data["source"] == "TfL fares 2026"


def test_airport_fare_uses_a_live_fare_from_a_named_start(net):
    net.routes.update({
        "/StopPoint/Search/Oxford": _search("940GZZLUOXC", "Oxford Circus Underground Station"),
        "/StopPoint/Search/Heathrow": _search("940GZZLUHR3", "Heathrow Terminals 2 & 3"),
        "/FareTo/": _fare_sections("6.40", "3.50"),
    })
    result = call("transportation.fares",
                  {"airport": "heathrow", "from_place": "Oxford Circus"})
    assert result.data["cost_gbp"] == 6.4
    assert result.data["best_option"] == "Pay as you go"
    assert result.data["headline"] == "£6.40 with contactless from Oxford Circus to Heathrow."


def test_airport_fare_falls_back_to_the_table_when_the_live_fare_fails(net):
    net.routes.update({
        "/StopPoint/Search/": _search("940GZZLUOXC", "Oxford Circus Underground Station"),
        "/FareTo/": http_error(404),
    })
    result = call("transportation.fares", {"airport": "LHR", "from_place": "Oxford Circus"})
    assert result.data["cost_gbp"] == 15.5


def test_unknown_airport_needs_input(net):
    with pytest.raises(l1_http.NeedsInput):
        call("transportation.fares", {"task": "airport", "airport": "Narnia"})


# --- journey.plan: route ---
def _victoria_journey(start: str = "2026-11-20T14:40:00", arrival: str = "2026-11-20T15:05:00") -> dict:
    legs = [_leg("walking", "Walk to Oxford Circus", "", 5),
            _leg("tube", "Victoria line to Victoria", "Victoria", 15),
            _leg("walking", "Walk to destination", "", 5)]
    return _journey(start, arrival, 25, legs, fare_pence=280)


def test_route_between_two_postcodes(net):
    net.routes["/Journey/JourneyResults/"] = _victoria_journey()
    result = call("journey.plan", {"from_place": "W1D 3QF", "to_place": "SW1A 1AA"})
    assert result.data["task"] == "route"
    assert result.data["headline"] == "Victoria line, 25 min. Leave 14:40, arrive 15:05."
    assert (result.data["leave_at"], result.data["arrive_at"], result.data["duration_minutes"]) == ("14:40", "15:05", 25)
    assert "£2.80 with contactless" in result.data["key_points"]


def test_route_sends_the_requested_departure_time(net):
    net.routes["/Journey/JourneyResults/"] = _victoria_journey()
    call("journey.plan", {"task": "route", "from_place": "W1D 3QF", "to_place": "SW1A 1AA", "depart_at": "18:30"})
    assert any("time=1830" in url and "timeIs=Departing" in url for url in net.urls)


def test_route_with_a_bad_clock_time_needs_input(net):
    with pytest.raises(l1_http.NeedsInput) as failure:
        call("journey.plan", {"task": "route", "from_place": "W1D 3QF", "to_place": "SW1A 1AA",
                              "depart_at": "teatime-ish"})
    assert "18:30" in failure.value.user_message


def test_route_without_places_needs_input(net):
    with pytest.raises(l1_http.NeedsInput):
        call("journey.plan", {"task": "route", "from_place": "W1D 3QF"})


def test_ambiguous_journey_answer_is_not_found(net):
    net.routes["/Journey/JourneyResults/"] = http_error(300)
    with pytest.raises(l1_http.NotFound) as failure:
        call("journey.plan", {"task": "route", "from_place": "W1D 3QF", "to_place": "SW1A 1AA"})
    assert failure.value.user_message == "TfL found no route between those two places."


# --- journey.plan: leave_by ---
def test_leave_by_works_back_from_the_arrival_time(net):
    legs = [_leg("tube", "Central line to Bank", "Central", 25)]
    net.routes["/Journey/JourneyResults/"] = _journey("2026-11-20T15:20:00", "2026-11-20T15:45:00", 25, legs)
    result = call("journey.plan", {"from_place": "W1D 3QF", "to_place": "SW1A 1AA", "arrive_by": "16:00"})
    assert result.data["task"] == "leave_by"
    assert result.data["headline"] == "Leave 15:20 for SW1A 1AA by 16:00: Central line, 25 min."
    assert (result.data["leave_at"], result.data["arrive_at"]) == ("15:20", "15:45")
    assert result.data["key_points"][0] == "Central: Minor Delays"
    assert any("date=20261120" in url and "time=1550" in url and "timeIs=Arriving" in url for url in net.urls)


def test_leave_by_in_the_past_is_no_longer_possible(net):
    net.routes["/Journey/JourneyResults/"] = _victoria_journey("2026-11-20T14:00:00", "2026-11-20T14:25:00")
    with pytest.raises(l1_http.NotFound) as failure:
        call("journey.plan", {"task": "leave_by", "from_place": "W1D 3QF", "to_place": "SW1A 1AA",
                              "arrive_by": "14:40"})
    assert "no longer possible" in failure.value.user_message


def test_leave_by_still_answers_when_side_checks_fail(net):
    net.routes.update({
        "/Journey/JourneyResults/": _victoria_journey("2026-11-20T15:20:00", "2026-11-20T15:45:00"),
        "/Line/Mode/": http_error(403),
        "api.open-meteo.com/v1/forecast": http_error(500),
    })
    result = call("journey.plan", {"task": "leave_by", "from_place": "W1D 3QF", "to_place": "SW1A 1AA",
                                   "arrive_by": "16:00"})
    assert result.data["leave_at"] == "15:20"


def test_leave_by_without_an_arrival_time_needs_input(net):
    with pytest.raises(l1_http.NeedsInput):
        call("journey.plan", {"task": "leave_by", "from_place": "W1D 3QF", "to_place": "SW1A 1AA"})


# --- journey.plan: night ---
def test_night_journey_uses_a_night_bus(net):
    legs = [_leg("walking", "Walk to Trafalgar Square", "", 4),
            _leg("bus", "N89 bus to Erith", "N89", 36)]
    net.routes["/Journey/JourneyResults/"] = _journey("2026-11-20T23:40:00", "2026-11-21T00:20:00", 40, legs)
    result = call("journey.plan", {"task": "night", "from_place": "W1D 3QF", "to_place": "SW1A 1AA",
                                   "depart_at": "23:30"})
    assert "night bus N89" in result.data["headline"]
    assert "Night buses run every night" in result.data["key_points"]
    assert (result.data["leave_at"], result.data["arrive_at"]) == ("23:40", "00:20")
    assert any("mode=" in url and "bus" in url for url in net.urls)


def test_night_journey_without_a_time_assumes_half_past_eleven(net):
    net.routes["/Journey/JourneyResults/"] = _victoria_journey("2026-11-20T23:40:00", "2026-11-21T00:05:00")
    result = call("journey.plan", {"task": "night", "from_place": "W1D 3QF", "to_place": "SW1A 1AA"})
    assert "Assumes leaving at 23:30" in result.data["key_points"]
    assert any("time=2330" in url for url in net.urls)


def test_night_tube_note_after_half_past_midnight(net):
    net.routes["/Journey/JourneyResults/"] = _victoria_journey("2026-11-21T00:45:00", "2026-11-21T01:10:00")
    result = call("journey.plan", {"task": "night", "from_place": "W1D 3QF", "to_place": "SW1A 1AA",
                                   "depart_at": "00:45"})
    assert any(point.startswith("Night Tube runs") for point in result.data["key_points"])


def test_night_journey_without_places_needs_input(net):
    with pytest.raises(l1_http.NeedsInput):
        call("journey.plan", {"task": "night"})


# --- safety.help ---
HELP_FACTS = ("999", "55", "61016", "Angela", "101")
POLICE_ROUTES = {
    "locate-neighbourhood": {"force": "metropolitan", "neighbourhood": "E05013806"},
    "api/metropolitan/E05013806": {"name": "Soho Neighbourhood Team", "url_force": "https://www.met.police.uk/soho",
                                   "contact_details": {"telephone": "101", "email": "x@example.com"}},
}


def _texts(data: dict) -> list[str]:
    """Every string in an answer, including the items of its lists."""
    texts = []
    for value in data.values():
        texts += value if isinstance(value, list) else [value] if isinstance(value, str) else []
    return texts


def assert_never_calls_anything_safe(data: dict) -> None:
    """The red line: no part of an answer may judge a place safe or unsafe."""
    for text in _texts(data):
        assert "safe" not in text.lower().replace("safety rating", ""), text


def assert_concise(data: dict) -> None:
    """Headline of at most 15 words; at most 3 key points of under 8 words each."""
    assert len(data["headline"].split()) <= 15, data["headline"]
    assert 1 <= len(data["key_points"]) <= 3, data["key_points"]
    for point in data["key_points"]:
        assert len(point.split()) < 8, point


def test_help_without_a_place_gives_the_four_fixed_options(net):
    result = call("safety.help", {"task": "help"})
    points = result.data["key_points"]
    assert len(points) == 3
    for needle in HELP_FACTS:
        assert any(needle in text for text in [result.data["headline"], *points]), needle
    assert "999" in result.data["headline"] and "55" in result.data["headline"]
    assert "61016" in points[0] and "Angela" in points[1]
    assert net.urls == []
    assert_never_calls_anything_safe(result.data)


def test_help_with_a_place_adds_police_and_station_facts_after_the_fixed_ones(net):
    net.routes.update(POLICE_ROUTES)
    net.routes.update({
        "/StopPoint/Search/": _search("940GZZLUOXC", "Oxford Circus Underground Station"),
        "/StopPoint/940GZZLUOXC": _stop("Oxford Circus Underground Station", {"Lifts": "2", "Help Points": "12"}),
        "/Disruptions/Lifts/v2": [],
    })
    result = call("safety.help", {"place": "W1D 3QF"})
    points = result.data["key_points"]
    assert "999" in result.data["headline"]
    assert points[2] == "Oxford Circus: 12 help points"
    assert not any("example.com" in text for text in _texts(result.data))


def test_help_still_answers_when_every_lookup_fails(net):
    net.routes.update({
        "locate-neighbourhood": http_error(500),
        "/StopPoint/Search/": http_error(403),
    })
    result = call("safety.help", {"task": "help", "place": "W1D 3QF"})
    assert result.data["key_points"][2] == "Non-emergency: call 101"
    assert "999" in result.data["headline"]


# --- safety.help: crime ---
def _crime_routes(net, crimes: list) -> None:
    net.routes.update({
        "crime-last-updated": {"date": "2026-09-01"},
        "data.police.uk/api/crimes-street": crimes,
    })


def test_crime_counts_state_the_data_month_and_are_not_a_rating(net):
    _crime_routes(net, _crimes("violent-crime", "violent-crime", "violent-crime", "shoplifting", "shoplifting", "robbery"))
    result = call("safety.help", {"task": "crime", "place": "W1D 3QF"})
    assert result.data["data_month"] == "2026-09"
    assert result.data["headline"] == "6 crimes recorded near W1D 3QF in September 2026; not a safety rating."
    assert result.data["key_points"][0] == "Violence and sexual offences: 3"
    assert "Recorded incidents, not a safety rating" in result.data["key_points"]
    assert_never_calls_anything_safe(result.data)


def test_crime_with_no_recorded_incidents_is_still_not_a_rating(net):
    _crime_routes(net, [])
    result = call("safety.help", {"task": "crime", "place": "W1D 3QF"})
    assert result.data["headline"].startswith("No crimes recorded near W1D 3QF in September 2026")
    assert_never_calls_anything_safe(result.data)


def test_crime_without_a_place_needs_input(net):
    with pytest.raises(l1_http.NeedsInput):
        call("safety.help", {"task": "crime"})


def test_crime_rate_limit_is_retryable(net):
    _crime_routes(net, http_error(429))
    with pytest.raises(RateLimitedError) as failure:
        call("safety.help", {"task": "crime", "place": "W1D 3QF"})
    assert failure.value.retryable


# --- Picked by name with nothing extracted ---
def test_picked_by_name_with_empty_inputs_picks_a_default_task(net):
    expected = {
        "transportation.fares": "best_ticket",
        "transport.live": "status",
        "outdoors.walk": "weather",
        "safety.help": "help",
    }
    for outcome, task in expected.items():
        assert call(outcome, {}, explicit=True).data["task"] == task, outcome


def test_picked_by_name_journey_with_empty_inputs_asks_for_places(net):
    with pytest.raises(l1_http.NeedsInput):
        call("journey.plan", {}, explicit=True)


# --- outdoors.walk: going out ---
def going_out(net, forecast: dict, **inputs):
    net.routes["api.open-meteo.com/v1/forecast"] = forecast
    return call("outdoors.walk", {"task": "going_out", **inputs})


def test_going_out_rain_later_tonight_means_an_umbrella(net):
    result = going_out(net, _forecast_from(21, precipitation_probability=70))
    assert result.data["umbrella_needed"] is True
    assert result.data["key_points"][0] == "Umbrella: rain from 21:00"
    assert result.data["wear_more"] is False
    assert result.data["headline"] == "Take an umbrella: rain from 21:00 (70%)."


def test_going_out_snow_is_named_as_snow(net):
    result = going_out(net, _forecast_from(20, snowfall=0.4, weather_code=73, precipitation_probability=60,
                                           apparent_temperature=-1.0))
    assert "Umbrella: snow from 20:00" in result.data["key_points"]
    assert result.data["umbrella_needed"] is True and result.data["wear_more"] is True
    assert result.data["headline"] == "Take an umbrella: snow from 20:00 (60%). Wrap up: feels like -1°C by 20:00."


def test_going_out_cold_only_means_more_layers(net):
    result = going_out(net, _forecast_from(22, apparent_temperature=2.0))
    assert result.data["umbrella_needed"] is False
    assert result.data["wear_more"] is True
    assert result.data["key_points"][0] == "Feels like 2°C: wrap up"
    assert result.data["headline"] == "Wrap up: feels like 2°C by 22:00."


def test_going_out_dry_and_mild(net):
    result = going_out(net, _forecast_from(0))
    assert (result.data["umbrella_needed"], result.data["wear_more"]) == (False, False)
    assert result.data["headline"] == "No umbrella needed; lowest feels-like 8°C."


def test_going_out_tonight_ignores_rain_before_six(net):
    rainy_afternoon = _forecast_from(15, precipitation_probability=90)
    rainy_afternoon["hourly"]["precipitation_probability"][17:] = [10] * 31
    result = going_out(net, rainy_afternoon, tonight=True)
    assert result.data["key_points"][0] == "Checked 18:00-00:00, central London"
    assert result.data["umbrella_needed"] is False


def test_going_out_respects_a_custom_cold_threshold(net):
    result = going_out(net, _forecast_from(0), cold_below_c=10)
    assert result.data["wear_more"] is True


def test_going_out_is_chosen_when_the_task_is_blank_but_tonight_is_set(net):
    net.routes["api.open-meteo.com/v1/forecast"] = _forecast_from(0)
    assert call("outdoors.walk", {"tonight": True}).data["task"] == "going_out"


def test_leave_by_uses_the_same_umbrella_rule(net):
    net.routes["api.open-meteo.com/v1/forecast"] = _forecast_from(15, precipitation_probability=80)
    legs = [_leg("tube", "Central line to Bank", "Central", 25)]
    net.routes["/Journey/JourneyResults/"] = _journey("2026-11-20T15:20:00", "2026-11-20T15:45:00", 25, legs)
    result = call("journey.plan", {"from_place": "W1D 3QF", "to_place": "SW1A 1AA", "arrive_by": "16:00"})
    assert "Umbrella: rain from 15:20" in result.data["key_points"]


# --- Place names: fallbacks after postcodes.io ---
def test_a_landmark_resolves_through_open_meteo(net):
    net.routes.update({
        "api.postcodes.io/places": {"result": []},
        "geocoding-api.open-meteo.com": {"results": [
            {"name": "Marble Arch", "latitude": 51.51414, "longitude": -0.15823, "admin2": "Greater London"}]},
    })
    result = call("outdoors.walk", {"task": "weather", "place": "marble arch"})
    assert "Marble Arch" in result.data["headline"]


def test_a_station_name_resolves_through_tfl(net):
    net.routes.update({
        "api.postcodes.io/places": {"result": []},
        "geocoding-api.open-meteo.com": {},
        "/StopPoint/Search/": _search("940GZZLUOXC", "Oxford Circus Underground Station"),
    })
    result = call("outdoors.walk", {"task": "weather", "place": "oxford circus"})
    assert "oxford circus" in result.data["headline"]


def test_gibberish_is_still_a_friendly_refusal(net):
    net.routes.update({
        "api.postcodes.io/places": {"result": []},
        "geocoding-api.open-meteo.com": {},
        "/StopPoint/Search/": {"matches": []},
        "nominatim.openstreetmap.org": [],
    })
    with pytest.raises(l1_http.NotFound) as failure:
        call("outdoors.walk", {"task": "weather", "place": "xqzvbw"})
    assert failure.value.user_message == "I could not find xqzvbw in London."


# --- Forgiving clock times ---
def test_clock_times_are_read_the_way_people_type_them():
    cases = [
        ("6", True, "18:00"), ("6pm", False, "18:00"), ("6:30pm", False, "18:30"), ("1830", False, "18:30"),
        ("half six", True, "18:30"), ("quarter past 7", False, "19:15"), ("quarter to 8", False, "19:45"),
        ("noon", False, "12:00"), ("midnight", False, "00:00"), ("6.30", False, "18:30"),
    ]
    for text, evening, expected in cases:
        assert l1_clock.parse_clock(text, NOW, evening=evening).strftime("%H:%M") == expected, text


def test_an_unreadable_time_needs_input():
    with pytest.raises(l1_http.NeedsInput) as failure:
        l1_clock.parse_clock("after lunchish", NOW)
    assert "18:30" in failure.value.user_message


def test_a_bare_hour_for_dinner_means_the_evening(net):
    net.routes["/Journey/JourneyResults/"] = _victoria_journey("2026-11-20T18:00:00", "2026-11-20T18:25:00")
    call("journey.plan", {"task": "route", "from_place": "W1D 3QF", "to_place": "SW1A 1AA", "depart_at": "6",
                          "occasion": "dinner"})
    assert any("time=1800" in url for url in net.urls)


# --- journey.plan: an evening out (leave_by with sunset and the way home) ---
MARBLE_ARCH = {"results": [{"name": "Marble Arch", "latitude": 51.51414, "longitude": -0.15823,
                            "admin2": "Greater London"}]}


def _evening(net, forecast: dict | None = None, outbound: tuple = ("2026-11-20T17:20:00", "2026-11-20T17:45:00"),
             **inputs):
    """Dinner at Marble Arch by 6, from W1D 3QF unless the inputs say otherwise."""
    legs = [_leg("tube", "Central line to Marble Arch", "Central", 25)]
    net.routes.update({
        "api.postcodes.io/places": {"result": []},
        "geocoding-api.open-meteo.com": MARBLE_ARCH,
        "/Journey/JourneyResults/": _journey(*outbound, 25, legs),
        "api.open-meteo.com/v1/forecast": forecast or _forecast_from(0),
    })
    asked = {"task": "leave_by", "from_place": "W1D 3QF", "to_place": "Marble Arch", "arrive_by": "6",
             "occasion": "dinner", **inputs}
    data = call("journey.plan", asked).data
    assert_never_calls_anything_safe(data)
    assert_concise(data)
    return data


def test_dinner_with_a_sunset_that_fits(net):
    data = _evening(net)
    assert data["headline"] == "Leave 17:20 for Marble Arch by 18:00: Central line, 25 min."
    assert data["sunset_plan"] == ("Sunset 16:04: catch it at Hyde Park (18 min walk from Marble Arch), "
                                   "then arrive by 18:00.")
    assert "Sunset 16:04 at Hyde Park" in data["key_points"]
    assert (data["umbrella_needed"], data["wear_more"]) == (False, False)


def test_a_sunset_too_late_to_fit_is_said_plainly(net):
    data = _evening(net, outbound=("2026-11-20T15:30:00", "2026-11-20T15:55:00"), arrive_by="16:10")
    assert data["sunset_plan"] == "Sunset is at 16:04, too late to fit in before 16:10."
    assert not any(point.startswith("Sunset") for point in data["key_points"])


def test_an_evening_with_rain_and_cold(net):
    data = _evening(net, _forecast_from(17, precipitation_probability=70, apparent_temperature=2.0))
    assert (data["umbrella_needed"], data["wear_more"]) == (True, True)
    assert "Umbrella: rain from 17:20" in data["key_points"]
    assert "Feels like 2°C: wrap up" in data["key_points"]


def test_after_dark_with_a_way_home(net):
    home = [_leg("walking", "Walk to Oxford Street", "", 4), _leg("bus", "N3 bus to Bromley", "N3", 36)]
    net.routes.update({
        "timeIs=Departing": _journey("2026-11-20T19:50:00", "2026-11-20T20:30:00", 40, home),
        "crime-last-updated": {"date": "2026-09-01"},
        "data.police.uk/api/crimes-street": _crimes("violent-crime", "shoplifting"),
    })
    data = _evening(net, return_to="SW2 1AA")
    assert data["after_dark"] is True
    assert data["return_route"][0] == "night bus N3, leave 19:50, home 20:30"
    assert "Night buses run every night" in data["return_route"]
    assert "September 2026" in data["crime_note"] and "not a safety rating" in data["crime_note"]
    assert "999" in data["help_note"] and "55" in data["help_note"]
    assert "Home: leave 19:50, night bus N3" in data["key_points"]


def test_after_dark_without_a_way_home(net):
    data = _evening(net)
    assert data["after_dark"] is True
    assert "Dark after 16:04: plan your way back" in data["key_points"]
    assert "999" in data["help_note"]
    assert "return_route" not in data and "crime_note" not in data


def test_only_a_destination_still_gives_weather_and_sunset(net):
    data = _evening(net, from_place="")
    assert data["headline"] == "For Marble Arch by 18:00, no umbrella needed; lowest feels-like 8°C."
    assert data["sunset_plan"].startswith("Sunset 16:04: catch it at Hyde Park")
    assert "leave_at" not in data
    assert not any("/Journey/" in url for url in net.urls)


# --- Every outcome answers in one short line and up to 3 short points ---
def test_fares_answer_is_concise(net):
    assert_concise(call("transportation.fares", {"task": "best_ticket", "zones": "1-3", "card": "none"}).data)


def test_journey_answer_is_concise(net):
    net.routes["/Journey/JourneyResults/"] = _victoria_journey()
    assert_concise(call("journey.plan", {"from_place": "W1D 3QF", "to_place": "SW1A 1AA"}).data)


def test_live_answer_is_concise(net):
    assert_concise(call("transport.live", {"task": "status"}).data)


def test_walk_answer_is_concise(net):
    assert_concise(call("outdoors.walk", {"task": "walk", "minutes": 30}).data)


def test_safety_answer_is_concise(net):
    data = call("safety.help", {"task": "help"}).data
    assert_concise(data)
    assert_never_calls_anything_safe(data)


# --- transport.live: National Rail ---
SOUTHERN_DELAYED = [{"name": "Southern", "lineStatuses": [
    {"statusSeverity": 9, "statusSeverityDescription": "Minor Delays",
     "reason": "Southern: Minor delays at Clapham Junction. See https://www.nationalrail.co.uk/x/"}]}]


def test_national_rail_operator_by_name(net):
    net.routes["/Line/Mode/national-rail/"] = SOUTHERN_DELAYED
    result = call("transport.live", {"task": "status", "line": "Southern"})
    assert result.data["headline"] == "Southern has minor delays."
    assert not any("://" in point for point in result.data["key_points"])


def test_national_rail_problems_add_one_point(net):
    net.routes["/Line/Mode/national-rail/"] = SOUTHERN_DELAYED
    result = call("transport.live", {"task": "status"})
    assert result.data["key_points"][-1] == "National Rail: 1 disrupted"
    assert len(result.data["key_points"]) <= 3


def test_national_rail_failure_is_ignored(net):
    net.routes["/Line/Mode/national-rail/"] = http_error(500)
    assert call("transport.live", {"task": "status"}).data["headline"].startswith("1 of 2 lines")
