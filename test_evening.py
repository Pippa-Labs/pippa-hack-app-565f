"""Tests for the way home after dark: help points on the route and recorded crime near its walks."""

from __future__ import annotations

import pytest

import l1_http
from test_app import (LINE_STATUS, MARBLE_ARCH, POSTCODE, FakeNet, _air, _crimes, _forecast, _forecast_from,
                      _journey, _leg, _search, _stop, assert_concise, assert_never_calls_anything_safe, call,
                      http_error)

HOME_LEGS = [
    _leg("walking", "Walk to Victoria Underground Station", "", 6),
    _leg("tube", "Victoria line to Brixton", "Victoria", 16),
    _leg("walking", "Walk to SW2 1AA", "", 5),
]


# --- Fake network ---
@pytest.fixture
def net(monkeypatch):
    """A FakeNet with the default routes, as in test_app."""
    fake = FakeNet({
        "/Line/Mode/national-rail/": [],
        "/Line/Mode/": LINE_STATUS,
        "api.postcodes.io/postcodes/": POSTCODE,
        "air-quality-api.open-meteo.com": _air(),
        "api.open-meteo.com/v1/forecast": _forecast(),
    })
    monkeypatch.setattr(l1_http, "fetch_bytes", fake)
    return fake


def _evening_home(net, **routes) -> dict:
    """Dinner at Marble Arch by 6, then home to SW2 1AA by the Victoria line."""
    net.routes.update({
        "timeIs=Departing": _journey("2026-11-20T19:50:00", "2026-11-20T20:25:00", 35, HOME_LEGS),
        "/StopPoint/Search/Victoria": _search("940GZZLUVIC", "Victoria Underground Station"),
        "/StopPoint/Search/Brixton": _search("940GZZLUBXN", "Brixton Underground Station"),
        "/StopPoint/940GZZLUVIC": _stop("Victoria Underground Station",
                                        {"Help Points": "8 on platforms, 4 in ticket halls"}),
        "/StopPoint/940GZZLUBXN": _stop("Brixton Underground Station", {"Help Points": "0"}),
        "/Disruptions/Lifts/v2": [],
        "crime-last-updated": {"date": "2026-09-01"},
        "data.police.uk/api/crimes-street": _crimes("violent-crime", "shoplifting"),
        **routes,
        "api.postcodes.io/places": {"result": []},
        "geocoding-api.open-meteo.com": MARBLE_ARCH,
        "/Journey/JourneyResults/": _journey("2026-11-20T17:20:00", "2026-11-20T17:45:00", 25,
                                             [_leg("tube", "Central line to Marble Arch", "Central", 25)]),
        "api.open-meteo.com/v1/forecast": _forecast_from(0),
    })
    data = call("journey.plan", {"task": "leave_by", "from_place": "W1D 3QF", "to_place": "Marble Arch",
                                 "arrive_by": "6", "occasion": "dinner", "return_to": "SW2 1AA"}).data
    assert_never_calls_anything_safe(data)
    assert_concise(data)
    return data


# --- Help points ---
def test_help_points_on_the_way_home_skip_a_station_with_none(net):
    data = _evening_home(net)
    assert data["return_route"][0] == "Victoria line, leave 19:50, home 20:25"
    assert "Help points: Victoria (12)" in data["return_route"]
    assert len(data["return_route"]) <= 3
    assert not any("Brixton (" in line for line in data["return_route"])


# --- Crime near the walks ---
def test_crime_near_the_walks_names_the_month_once_per_place(net):
    data = _evening_home(net)
    assert data["crime_note"] == ("Marble Arch: 2 recorded (September 2026); "
                                  "W1D 3QF: 2 recorded (September 2026); not a safety rating.")
    assert data["crime_note"].count("not a safety rating") == 1
    assert sum("crimes-street" in url for url in net.urls) == 2


def test_the_way_home_still_answers_when_the_police_fail(net):
    data = _evening_home(net, **{"crime-last-updated": http_error(500)})
    assert "crime_note" not in data
    assert "Help points: Victoria (12)" in data["return_route"]
    assert "999" in data["help_note"]
