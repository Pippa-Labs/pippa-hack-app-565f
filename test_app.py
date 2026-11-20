"""Tests for London Companion, run through LocalRunner with the network stubbed."""

from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest
from pippa_agentapp_sdk import LocalRunner, ServiceUnavailableError

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
    assert {o.id for o in app.manifest.outcomes} == {"transport.live", "outdoors.walk"}


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
