"""Tests for London Companion, run through LocalRunner with the network stubbed."""

from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest
from pippa_agentapp_sdk import LocalRunner, RateLimitedError, ServiceUnavailableError

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
    assert result.data["facts"] == ["Central: Minor Delays. Minor delays due to a signal failure."]
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
    assert "Sunset is at 16:04." in result.data["facts"]
    assert result.data["as_of"] == "2026-11-20T14:00:00+00:00"


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
    assert not any("Air quality" in fact for fact in result.data["facts"])


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
    assert result.data["facts"][0] == "Victoria to Brixton: 2 min (Southbound - Platform 4)"
    assert len(result.data["facts"]) == 2


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
    assert result.data["headline"].startswith("No departures are predicted")


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
    assert result.data["headline"] == "12 hire bikes and 15 free docks across 2 docks matching Clerkenwell."
    assert result.data["facts"][0] == "River Street, Clerkenwell: 6 bikes, 2 e-bikes, 10 free docks"


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
    assert "1 lift fault(s) reported right now." in result.data["facts"]


def test_step_free_ignores_faults_at_other_stations(net):
    _step_free_routes(net, [{"stationUniqueId": "940GZZLUBXN", "message": "Brixton: Lift 1 out of service"}])
    result = call("transport.live", {"task": "step_free", "place": "Oxford Circus"})
    assert result.data["headline"] == "Oxford Circus has step-free access via lift, and no lift faults are reported right now."


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
    assert "peak £2.90 and off-peak £1.90" in result.data["headline"]
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
    assert result.data["as_of"] == "2026-11"


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
    assert result.data["facts"][0].startswith("Pay as you go from Oxford Circus Underground Station is £6.40")


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
    assert result.data["headline"] == "Fastest way from W1D 3QF to SW1A 1AA: Victoria line, 25 minutes, arriving 15:05."
    assert (result.data["leave_at"], result.data["arrive_at"], result.data["duration_minutes"]) == ("14:40", "15:05", 25)
    assert "Pay-as-you-go fare: £2.80" in result.data["facts"]


def test_route_sends_the_requested_departure_time(net):
    net.routes["/Journey/JourneyResults/"] = _victoria_journey()
    call("journey.plan", {"task": "route", "from_place": "W1D 3QF", "to_place": "SW1A 1AA", "depart_at": "18:30"})
    assert any("time=1830" in url and "timeIs=Departing" in url for url in net.urls)


def test_route_with_a_bad_clock_time_needs_input(net):
    with pytest.raises(l1_http.NeedsInput):
        call("journey.plan", {"task": "route", "from_place": "W1D 3QF", "to_place": "SW1A 1AA", "depart_at": "7pm"})


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
    assert result.data["headline"] == ("Leave W1D 3QF by 15:20 to reach SW1A 1AA by 16:00 "
                                      "(25-minute journey, 15 minutes spare).")
    assert (result.data["leave_at"], result.data["arrive_at"]) == ("15:20", "15:45")
    assert "Central: Minor Delays" in result.data["facts"]
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
    assert "Night buses run every night." in result.data["facts"]
    assert (result.data["leave_at"], result.data["arrive_at"]) == ("23:40", "00:20")
    assert any("mode=" in url and "bus" in url for url in net.urls)


def test_night_journey_without_a_time_assumes_half_past_eleven(net):
    net.routes["/Journey/JourneyResults/"] = _victoria_journey("2026-11-20T23:40:00", "2026-11-21T00:05:00")
    result = call("journey.plan", {"task": "night", "from_place": "W1D 3QF", "to_place": "SW1A 1AA"})
    assert "No time given, so this assumes leaving at 23:30 tonight." in result.data["facts"]
    assert any("time=2330" in url for url in net.urls)


def test_night_tube_note_after_half_past_midnight(net):
    net.routes["/Journey/JourneyResults/"] = _victoria_journey("2026-11-21T00:45:00", "2026-11-21T01:10:00")
    result = call("journey.plan", {"task": "night", "from_place": "W1D 3QF", "to_place": "SW1A 1AA",
                                   "depart_at": "00:45"})
    assert any(fact.startswith("The Night Tube runs") for fact in result.data["facts"])


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


def assert_never_calls_anything_safe(data: dict) -> None:
    """The red line: no headline or fact may judge a place safe or unsafe."""
    for text in [data["headline"], *data["facts"]]:
        assert "safe" not in text.lower().replace("safety rating", ""), text


def test_help_without_a_place_gives_the_four_fixed_options(net):
    result = call("safety.help", {"task": "help"})
    facts = result.data["facts"]
    assert len(facts) == 4
    for needle in HELP_FACTS:
        assert any(needle in fact for fact in facts), needle
    assert "999" in facts[0] and "55" in facts[0]
    assert "61016" in facts[1] and "Angela" in facts[2]
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
    facts = result.data["facts"]
    assert "999" in facts[0]
    assert facts[4] == "Local police team: Soho Neighbourhood Team (Metropolitan Police) — https://www.met.police.uk/soho"
    assert facts[5] == "Station: Oxford Circus — help points: 12; lifts: 2 lifts"
    assert not any("example.com" in fact for fact in facts)


def test_help_still_answers_when_every_lookup_fails(net):
    net.routes.update({
        "locate-neighbourhood": http_error(500),
        "/StopPoint/Search/": http_error(403),
    })
    result = call("safety.help", {"task": "help", "place": "W1D 3QF"})
    assert len(result.data["facts"]) == 4
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
    assert result.data["headline"] == "In September 2026, 6 crimes were recorded within about a mile of W1D 3QF."
    assert result.data["facts"][0] == "Violence and sexual offences: 3"
    assert "These are recorded incidents, not a safety rating." in result.data["facts"]
    assert_never_calls_anything_safe(result.data)


def test_crime_with_no_recorded_incidents_is_still_not_a_rating(net):
    _crime_routes(net, [])
    result = call("safety.help", {"task": "crime", "place": "W1D 3QF"})
    assert result.data["headline"].startswith("No crimes were recorded in September 2026")
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
