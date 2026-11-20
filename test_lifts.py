"""Tests for lift faults on routes and the step-free headline, with the network stubbed."""

from __future__ import annotations

import pytest

import l1_http
from test_app import FakeNet, LINE_STATUS, POSTCODE, _air, _forecast, _journey, _leg, _search, _stop, call, http_error

ROUTE = {"task": "route", "from_place": "W1D 3QF", "to_place": "E14 5AB"}
CANARY_WHARF_FAULT = {"stationUniqueId": "940GZZLUCYF", "message": "Canary Wharf: Lift 2 out of service"}
MORDEN_FAULT = {"stationUniqueId": "940GZZLUMDN", "message": "Morden: Lift 1 out of service"}


# --- Fake network ---
@pytest.fixture
def net(monkeypatch):
    """A FakeNet with a Jubilee line journey to Canary Wharf and no lift faults."""
    legs = [_leg("walking", "Walk to Bond Street", "", 5),
            _leg("tube", "Jubilee line to Canary Wharf", "Jubilee", 20),
            _leg("walking", "Walk to destination", "", 4)]
    fake = FakeNet({
        "/Disruptions/Lifts/v2": [],
        "/Journey/JourneyResults/": _journey("2026-11-20T14:40:00", "2026-11-20T15:09:00", 29, legs),
        "/Line/Mode/national-rail/": [],
        "/Line/Mode/": LINE_STATUS,
        "api.postcodes.io/postcodes/": POSTCODE,
        "air-quality-api.open-meteo.com": _air(),
        "api.open-meteo.com/v1/forecast": _forecast(),
    })
    monkeypatch.setattr(l1_http, "fetch_bytes", fake)
    return fake


# --- Lift faults on a route ---
def test_route_flags_a_lift_out_on_the_route(net):
    net.routes["/Disruptions/Lifts/v2"] = [CANARY_WHARF_FAULT]
    result = call("journey.plan", ROUTE)
    assert "Lift out at Canary Wharf" in result.data["key_points"]


def test_step_free_route_puts_the_lift_fault_first(net):
    net.routes["/Disruptions/Lifts/v2"] = [CANARY_WHARF_FAULT]
    result = call("journey.plan", {**ROUTE, "preference": "step_free"})
    assert result.data["key_points"][0] == "Lift out at Canary Wharf"
    assert result.data["headline"] == "Step-free route, but lift out at Canary Wharf: Jubilee line, 29 min."


def test_lift_fault_elsewhere_is_not_mentioned(net):
    net.routes["/Disruptions/Lifts/v2"] = [MORDEN_FAULT]
    result = call("journey.plan", ROUTE)
    assert not any("Lift" in point for point in result.data["key_points"])


def test_lift_feed_failing_still_answers(net):
    net.routes["/Disruptions/Lifts/v2"] = http_error(500)
    result = call("journey.plan", ROUTE)
    assert result.data["duration_minutes"] == 29
    assert not any("Lift" in point for point in result.data["key_points"])


# --- Step-free headline ---
def test_step_free_no_headline_ends_with_tfl_advice(net):
    net.routes.update({
        "/StopPoint/Search/": _search("940GZZLUMDN", "Morden Underground Station"),
        "/StopPoint/940GZZLUMDN": _stop("Morden Underground Station", {"AccessViaLift": "No"}),
    })
    result = call("transport.live", {"task": "step_free", "place": "Morden"})
    assert result.data["headline"] == "Step-free: no lift at Morden. Call TfL 0343 222 1234."


def test_step_free_yes_headline_stays_short(net):
    net.routes.update({
        "/StopPoint/Search/": _search("940GZZLUOXC", "Oxford Circus Underground Station"),
        "/StopPoint/940GZZLUOXC": _stop("Oxford Circus Underground Station", {"Lifts": "2", "AccessViaLift": "Yes"}),
    })
    result = call("transport.live", {"task": "step_free", "place": "Oxford Circus"})
    assert result.data["headline"] == "Step-free: yes, Oxford Circus. No lift faults reported."
