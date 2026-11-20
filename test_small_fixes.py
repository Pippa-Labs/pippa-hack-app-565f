"""Tests for bus route numbers, Travelcard estimate labels and the police contact point."""

from __future__ import annotations

import pytest

import l1_http
import l2_get_help
import l2_live
from l1_http import NeedsInput
from test_app import (
    LINE_STATUS, POLICE_ROUTES, POSTCODE, FakeNet, _air, _arrival, _forecast, assert_concise, call, http_error,
)


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


# --- Bus route numbers ---
BUS_STOPS = {"/StopPoint?": {"stopPoints": [
    {"naptanId": "490G00001", "commonName": "Wardour Street", "indicator": "Stop A", "distance": 120},
    {"naptanId": "490G00002", "commonName": "Oxford Street", "indicator": "Stop B", "distance": 200},
]}}
BUS_ARRIVALS = {
    "/StopPoint/490G00001/Arrivals": [_arrival("390", "Archway", 60), _arrival("73", "Stoke Newington", 240)],
    "/StopPoint/490G00002/Arrivals": [_arrival("73", "Oxford Circus", 420), _arrival("N29", "Enfield", 90)],
}


def test_bus_route_numbers_are_recognised(net):
    for text, route in (("73", "73"), ("the 73", "73"), ("bus 73", "73"), ("N29", "N29"), ("the N1", "N1")):
        assert l2_live._bus_route(text) == route, text
    for text in ("Oxford Circus", "W1D 3QF", "N1", "SE1"):
        assert l2_live._bus_route(text) == "", text


def test_a_route_with_a_stop_shows_only_that_route(net):
    net.routes.update({**BUS_STOPS, **BUS_ARRIVALS})
    data = call("transport.live", {"task": "departures", "place": "W1D 3QF", "line": "the 73"}).data
    assert data["headline"] == "Next from 73 stops near W1D 3QF: 73 to Stoke Newington in 4 min."
    assert data["key_points"] == ["73 to Oxford Circus in 7 min"]
    assert not any("/StopPoint/Search/" in url for url in net.urls)
    assert_concise(data)


def test_a_route_asked_as_a_line_status_gives_its_arrivals(net):
    net.routes.update({**BUS_STOPS, **BUS_ARRIVALS})
    data = call("transport.live", {"task": "status", "place": "W1D 3QF", "line": "73"}).data
    assert data["headline"].startswith("Next from 73 stops near W1D 3QF")


def test_a_route_with_no_buses_due_says_so(net):
    net.routes.update({**BUS_STOPS, "/Arrivals": [_arrival("390", "Archway", 60)]})
    data = call("transport.live", {"task": "departures", "place": "W1D 3QF", "line": "73"}).data
    assert data["headline"] == "No 73 buses predicted near W1D 3QF right now."


def test_a_route_without_a_stop_needs_input(net):
    for inputs in ({"place": "the 73"}, {"place": "bus 73", "line": "73"}, {"line": "73"}):
        with pytest.raises(NeedsInput) as caught:
            call("transport.live", {"task": "departures", **inputs})
        assert caught.value.user_message == "Which stop? Tell me the street or stop for the 73.", inputs
    assert net.urls == []


def test_a_night_route_without_a_stop_names_the_route(net):
    with pytest.raises(NeedsInput) as caught:
        call("transport.live", {"task": "departures", "place": "N29"})
    assert caught.value.user_message == "Which stop? Tell me the street or stop for the N29."


# --- Travelcard estimate ---
def test_the_monthly_travelcard_is_labelled_an_estimate(net):
    data = call("transportation.fares", {"task": "best_ticket", "stay_days": 28, "zones": "1-2"}).data
    estimate = "about £172 (estimate)"
    assert data["headline"] == f"Monthly Travelcard: {estimate} for 28 days in zones 1-2."
    assert data["best_option"] == f"Monthly Travelcard, {estimate}"
    assert f"Monthly Travelcard {estimate}" in data["key_points"]
    assert data["cost_gbp"] == 172.0
    assert_concise(data)


def test_a_published_price_is_not_an_estimate(net):
    data = call("transportation.fares", {"task": "best_ticket", "stay_days": 7, "zones": "1-3"}).data
    assert "estimate" not in " ".join([data["headline"], data["best_option"], *data["key_points"]])


# --- Police contact ---
def test_help_names_the_local_team_with_101(net):
    net.routes.update(POLICE_ROUTES)
    data = call("safety.help", {"task": "help", "place": "W1D 3QF"}).data
    assert "999" in data["headline"]
    assert data["key_points"][2] == "Police: Soho Neighbourhood Team, call 101"
    assert data["source"] == "data.police.uk"
    assert_concise(data)


def test_help_keeps_101_when_the_team_lookup_fails(net):
    net.routes.update({"locate-neighbourhood": http_error(500)})
    data = call("safety.help", {"task": "help", "place": "W1D 3QF"}).data
    assert data["key_points"][2] == "Non-emergency police: 101"
    assert len(data["key_points"]) == 3
    assert "999" in data["headline"]


def test_a_long_team_name_is_shortened_or_left_out(net):
    assert l2_get_help._short_team("Camden Town Safer Neighbourhood Team") == "Camden Town"
    assert l2_get_help._short_team("West End and St James Safer Neighbourhood Team") == ""
