"""Tests for the fare budget verdict and the Student Oyster monthly saving."""

from __future__ import annotations

import pytest

import l1_http
from test_app import FakeNet, _fare_sections, _search, call, http_error

FARES = "transportation.fares"


# --- Fake network ---
@pytest.fixture
def net(monkeypatch):
    """A FakeNet that finds any station and prices it at £2.90 peak, £1.90 off-peak."""
    fake = FakeNet({
        "/StopPoint/Search/": _search("940GZZLUOXC", "Oxford Circus Underground Station"),
        "/FareTo/": _fare_sections("2.90", "1.90"),
    })
    monkeypatch.setattr(l1_http, "fetch_bytes", fake)
    return fake


def _single(budget: float, **extra):
    inputs = {"task": "single_fare", "from_place": "Oxford Circus", "to_place": "Brixton", "budget_gbp": budget}
    return call(FARES, {**inputs, **extra}).data


def _words(text: str) -> int:
    return len(text.split())


# --- Single fare budget ---
def test_single_fare_under_budget(net):
    data = _single(3)
    assert data["headline"] == "£1.90 off-peak with contactless. Within your £3."
    assert data["cost_gbp"] == 1.9
    assert _words(data["headline"]) <= 15


def test_single_fare_over_budget(net):
    data = _single(2.8, peak=True)
    assert data["headline"] == "£2.90 peak with contactless: 10p over your £2.80."
    assert len(data["key_points"]) <= 3
    assert all(_words(point) < 8 for point in data["key_points"])


def test_single_fare_exactly_on_budget(net):
    data = _single(1.9)
    assert data["headline"] == "£1.90 off-peak with contactless. Exactly your £1.90 budget."


def test_single_fare_without_budget_keeps_the_stations(net):
    net.routes = {"/StopPoint/Search/Brixton": _search("940GZZLUBXN", "Brixton Underground Station"), **net.routes}
    data = call(FARES, {"task": "single_fare", "from_place": "Oxford Circus", "to_place": "Brixton"}).data
    assert data["headline"] == "£1.90 off-peak with contactless, Oxford Circus to Brixton."


# --- Airport budget ---
def test_airport_table_fare_over_budget(net):
    data = call(FARES, {"task": "airport", "airport": "heathrow", "budget_gbp": 10}).data
    assert data["headline"] == "Elizabeth line from zone 1 to Heathrow: £15.50. £5.50 over your £10."
    assert _words(data["headline"]) <= 15


def test_airport_live_fare_within_budget(net):
    data = call(FARES, {"task": "airport", "airport": "heathrow", "from_place": "Oxford Circus",
                        "budget_gbp": 5}).data
    assert data["headline"] == "£2.90 with contactless to Heathrow. Within your £5."


def test_airport_fare_when_tfl_fails_still_gives_a_verdict(net):
    net.routes["/FareTo/"] = http_error(503)
    data = call(FARES, {"task": "airport", "airport": "heathrow", "from_place": "Oxford Circus",
                        "budget_gbp": 15.5}).data
    assert data["headline"].endswith("Exactly your £15.50 budget.")


# --- Student monthly saving ---
def test_student_month_shows_the_saving_against_an_adult_monthly(net):
    data = call(FARES, {"task": "best_ticket", "zones": "1-2", "stay_days": 28, "student": True}).data
    # Derived monthly 1-2 = round(4470 x 3.84) = 17165p; student = 12016p; saving 5149p.
    assert "Student Oyster saves about £51.49 a month" in data["key_points"]
    assert len(data["key_points"]) <= 3


def test_student_short_stay_has_no_monthly_saving(net):
    data = call(FARES, {"task": "best_ticket", "zones": "1-2", "stay_days": 7, "student": True}).data
    assert not any("saves" in point for point in data["key_points"])
