"""Tests for the weather answer on a chosen day (outdoors.walk with `day`)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from pippa_agentapp_sdk import ServiceUnavailableError

import l1_http
from test_app import NOW, POSTCODE, FakeNet, _air, _forecast, call, http_error

DAYS = 7
WEEK_START = datetime(2026, 11, 20, 0, 0)  # midnight London time on NOW's date (a Friday)


# --- Canned 7-day forecast ---
def _week(rain_from: datetime | None = None, temp_c: float = 9.0) -> dict:
    """Seven days of Open-Meteo hourly columns; rain at 80% from `rain_from` on, if given."""
    moments = [WEEK_START + timedelta(hours=h) for h in range(DAYS * 24)]
    wet = [rain_from is not None and moment >= rain_from for moment in moments]
    column = lambda value: [value] * len(moments)  # noqa: E731
    days = [WEEK_START + timedelta(days=d) for d in range(DAYS)]
    return {
        "hourly": {
            "time": [moment.strftime("%Y-%m-%dT%H:%M") for moment in moments],
            "temperature_2m": column(temp_c), "apparent_temperature": column(temp_c - 2),
            "precipitation_probability": [80 if w else 10 for w in wet],
            "precipitation": [1.0 if w else 0.0 for w in wet], "snowfall": column(0.0),
            "wind_speed_10m": column(12.0), "wind_gusts_10m": column(25.0),
            "uv_index": column(0.5), "weather_code": [63 if w else 2 for w in wet], "is_day": column(1),
        },
        "daily": {
            "sunrise": [day.replace(hour=7, minute=30).strftime("%Y-%m-%dT%H:%M") for day in days],
            "sunset": [day.replace(hour=16, minute=3).strftime("%Y-%m-%dT%H:%M") for day in days],
        },
    }


@pytest.fixture
def net(monkeypatch):
    """Install a FakeNet answering with a dry week."""
    fake = FakeNet({
        "api.postcodes.io/postcodes/": POSTCODE,
        "air-quality-api.open-meteo.com": _air(),
        "api.open-meteo.com/v1/forecast": _week(),
    })
    monkeypatch.setattr(l1_http, "fetch_bytes", fake)
    return fake


def _asked_air(net: FakeNet) -> bool:
    return any("air-quality-api" in url for url in net.urls)


# --- A chosen day ---
def test_tomorrow_names_the_weekday_and_when_rain_starts(net):
    net.routes["api.open-meteo.com/v1/forecast"] = _week(rain_from=datetime(2026, 11, 21, 14, 0))
    data = call("outdoors.walk", {"task": "weather", "day": "tomorrow"}).data
    assert data["headline"] == "Saturday in central London: rain likely from 14:00, 9°C max."
    assert data["key_points"] == ["Rain chance up to 80% at 14:00", "Sunset 16:03"]
    assert len(data["headline"].split()) <= 15
    assert any("forecast_days=2" in url for url in net.urls)
    assert not _asked_air(net)


def test_a_weekday_means_the_next_one(net):
    data = call("outdoors.walk", {"day": "Tuesday", "place": "W1D 3QF"}).data
    assert data["task"] == "weather"
    assert data["headline"] == "Tuesday in W1D 3QF: dry, 9°C max."
    assert data["temperature_c"] == 9.0
    assert any("forecast_days=5" in url for url in net.urls)


def test_other_ways_of_naming_a_day(net):
    for day, weekday in (("in 3 days", "Monday"), ((NOW + timedelta(days=5)).date().isoformat(), "Wednesday"), ("sun", "Sunday")):
        headline = call("outdoors.walk", {"task": "weather", "day": day}).data["headline"]
        assert headline.startswith(f"{weekday} in central London:"), (day, headline)


def test_more_than_six_days_ahead_is_refused(net):
    for day in ("2026-11-27", "in 9 days", "2026-11-19", "next month"):
        with pytest.raises(l1_http.NeedsInput) as refusal:
            call("outdoors.walk", {"task": "weather", "day": day})
        assert refusal.value.user_message == "I can forecast up to 6 days ahead."
    assert net.urls == []


def test_forecast_outage_on_a_day_is_retryable(net):
    net.routes["api.open-meteo.com/v1/forecast"] = http_error(500)
    with pytest.raises(ServiceUnavailableError):
        call("outdoors.walk", {"task": "weather", "day": "saturday"})


# --- No day, or today ---
def test_no_day_states_the_window(net):
    net.routes["api.open-meteo.com/v1/forecast"] = _forecast()
    for day in ("", "today", "friday"):
        data = call("outdoors.walk", {"task": "weather", "day": day}).data
        assert data["headline"] == "Next 12 hours: partly cloudy, 10°C in central London; no big change."
        assert "Air quality fair" in data["key_points"]
    assert _asked_air(net)


def test_walk_now_ignores_the_day(net):
    net.routes["api.open-meteo.com/v1/forecast"] = _forecast()
    data = call("outdoors.walk", {"task": "walk", "minutes": 30, "day": "saturday"}).data
    assert data["verdict"] == "go"
    assert any("forecast_days=2" in url for url in net.urls)
