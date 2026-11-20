"""Tests for the step-free wording, midnight times on night journeys and named line disruptions."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pippa_agentapp_sdk import LocalRunner

import l1_clock
import l1_http
import l2_best_route
from app import App
from test_app import NOW, FakeNet, LINE_STATUS, POSTCODE, _air, _forecast, _journey, _leg, _search, _stop, call

ROUTE = {"task": "route", "from_place": "W1D 3QF", "to_place": "E14 5AB"}
NIGHT = {"task": "night", "from_place": "W1D 3QF", "to_place": "E14 5AB"}
CANARY_WHARF_FAULT = {"stationUniqueId": "940GZZLUCYF", "message": "Canary Wharf: Lift 2 out of service"}
EVENING = datetime(2026, 11, 20, 23, 0, tzinfo=timezone.utc)  # 23:00 GMT, London time equals UTC


# --- Fake network ---
@pytest.fixture
def net(monkeypatch):
    """Install a FakeNet with the default happy-path routes."""
    fake = FakeNet({
        "/Line/Mode/national-rail/": [],
        "/Disruptions/Lifts/v2": [],
        "/Line/Mode/": LINE_STATUS,
        "api.postcodes.io/postcodes/": POSTCODE,
        "air-quality-api.open-meteo.com": _air(),
        "api.open-meteo.com/v1/forecast": _forecast(),
    })
    monkeypatch.setattr(l1_http, "fetch_bytes", fake)
    return fake


def _jubilee(start: str = "2026-11-20T14:40:00", arrival: str = "2026-11-20T15:09:00", disruptions: tuple = ()) -> dict:
    """A Walk + Jubilee line journey; disruption text sits on the walking leg, as TfL sometimes sends it."""
    walk = _leg("walking", "Walk to Bond Street", "", 5)
    walk["disruptions"] = [{"description": text} for text in disruptions]
    legs = [walk, _leg("tube", "Jubilee line to Canary Wharf", "Jubilee", 20),
            _leg("bus", "135 bus to Crossharbour", "135", 4)]
    return _journey(start, arrival, 29, legs, fare_pence=280)


def _station(net, name: str, stop_id: str, properties: dict, faults: list) -> None:
    net.routes.update({
        "/StopPoint/Search/": _search(stop_id, f"{name} Underground Station"),
        f"/StopPoint/{stop_id}": _stop(f"{name} Underground Station", properties),
        "/Disruptions/Lifts/v2": faults,
    })


def _call_at(now: datetime, outcome: str, inputs: dict):
    return LocalRunner(App()).call(outcome, inputs, granted_secrets={}, now=now, request_id="t-fix").result


# --- Step-free station headlines ---
def test_step_free_yes_starts_plainly(net):
    _station(net, "Oxford Circus", "940GZZLUOXC", {"Lifts": "2", "AccessViaLift": "Yes"}, [])
    result = call("transport.live", {"task": "step_free", "place": "Oxford Circus"})
    assert result.data["headline"].startswith("Step-free: yes, Oxford Circus.")


def test_step_free_lift_out_says_call_tfl(net):
    _station(net, "Oxford Circus", "940GZZLUOXC", {"Lifts": "2", "AccessViaLift": "Yes"},
             [{"stationUniqueId": "940GZZLUOXC", "message": "Oxford Circus: Lift 3 out of service"}])
    result = call("transport.live", {"task": "step_free", "place": "Oxford Circus"})
    assert result.data["headline"] == "Step-free: no, lift out at Oxford Circus. Call TfL 0343 222 1234."


def test_step_free_no_lift(net):
    _station(net, "Morden", "940GZZLUMDN", {"AccessViaLift": "No"}, [])
    result = call("transport.live", {"task": "step_free", "place": "Morden"})
    assert result.data["headline"].startswith("Step-free: no lift at Morden.")


def test_step_free_not_stated(net):
    _station(net, "Morden", "940GZZLUMDN", {}, [])
    result = call("transport.live", {"task": "step_free", "place": "Morden"})
    assert result.data["headline"] == "Step-free: not stated for Morden; ask staff."


# --- Step-free route headlines ---
def test_step_free_route_headline(net):
    net.routes["/Journey/JourneyResults/"] = _jubilee()
    result = call("journey.plan", {**ROUTE, "preference": "step_free"})
    assert result.data["headline"].startswith("Step-free route: Jubilee line + bus 135, 29 min.")
    assert len(result.data["headline"].split()) <= 15


def test_step_free_route_names_the_lift_fault(net):
    net.routes.update({"/Journey/JourneyResults/": _jubilee(), "/Disruptions/Lifts/v2": [CANARY_WHARF_FAULT]})
    result = call("journey.plan", {**ROUTE, "preference": "step_free"})
    assert result.data["headline"] == "Step-free route, but lift out at Canary Wharf: Jubilee line + bus 135, 29 min."
    assert result.data["key_points"][0] == "Lift out at Canary Wharf"
    assert len(result.data["key_points"]) <= 3


def test_fastest_route_headline_has_no_step_free_lead(net):
    net.routes["/Journey/JourneyResults/"] = _jubilee()
    result = call("journey.plan", ROUTE)
    assert result.data["headline"] == "Jubilee line + bus 135, 29 min. Leave 14:40, arrive 15:09."


# --- Night times after dark ---
def test_half_past_twelve_at_night_means_after_midnight(net):
    assert l1_clock.parse_clock("12:30", EVENING, evening=True, night=True).strftime("%d %H:%M") == "21 00:30"
    assert l1_clock.parse_clock("12", EVENING, evening=True, night=True).strftime("%d %H:%M") == "21 00:00"
    assert l1_clock.parse_clock("1", EVENING, evening=True, night=True).strftime("%d %H:%M") == "21 01:00"


def test_noon_still_means_noon(net):
    assert l1_clock.parse_clock("noon", EVENING, night=True).strftime("%H:%M") == "12:00"
    assert l1_clock.parse_clock("12:30", NOW).strftime("%H:%M") == "12:30"
    assert l1_clock.parse_clock("12pm", EVENING, night=True).strftime("%H:%M") == "12:00"


def test_night_departure_reads_twelve_thirty_as_after_midnight(net):
    when, assumed = l2_best_route.night_departure({"depart_at": "12:30"}, EVENING)
    assert (when.strftime("%d %H:%M"), assumed) == ("21 00:30", False)


def test_night_departure_in_the_afternoon_keeps_the_day_reading(net):
    when, _ = l2_best_route.night_departure({"depart_at": "11"}, NOW)
    assert when.strftime("%H:%M") == "23:00"


def test_night_journey_asked_late_sends_half_past_midnight(net):
    net.routes["/Journey/JourneyResults/"] = _jubilee("2026-11-21T00:35:00", "2026-11-21T01:04:00")
    _call_at(EVENING, "journey.plan", {**NIGHT, "depart_at": "12:30"})
    assert any("date=20261121" in url and "time=0030" in url for url in net.urls)


# --- Naming the disrupted line ---
def test_disruption_names_the_line_in_the_text(net):
    net.routes["/Journey/JourneyResults/"] = _jubilee(disruptions=("Jubilee line: severe delays.",))
    result = call("journey.plan", ROUTE)
    assert "Jubilee line: disruption" in result.data["key_points"]
    assert "Disruption reported on this route" not in result.data["key_points"]


def test_disruption_matches_a_bus_by_its_number(net):
    net.routes["/Journey/JourneyResults/"] = _jubilee(disruptions=("Route 135 diverted.",))
    result = call("journey.plan", ROUTE)
    assert "bus 135: disruption" in result.data["key_points"]


def test_disruption_without_a_line_name_goes_to_the_first_ride(net):
    net.routes["/Journey/JourneyResults/"] = _jubilee(disruptions=("Works at the station entrance.",))
    result = call("journey.plan", ROUTE)
    assert "Jubilee line: disruption" in result.data["key_points"]
