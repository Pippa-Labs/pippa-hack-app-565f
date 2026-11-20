"""Places outside Greater London are refused with a plain sentence."""

from __future__ import annotations

import pytest

import l1_http
from test_app import FakeNet, call


# --- Fake network ---
@pytest.fixture
def net(monkeypatch):
    """Postcodes in Manchester and South Kensington, nothing else."""
    fake = FakeNet({
        "api.postcodes.io/postcodes/M11AE": {"result": {"latitude": 53.4794, "longitude": -2.2453, "postcode": "M1 1AE"}},
        "api.postcodes.io/postcodes/": {"result": {"latitude": 51.4993, "longitude": -0.1792, "postcode": "SW7 2AZ"}},
        "api.postcodes.io/places": {"result": []},
        "geocoding-api.open-meteo.com": {},
        "/StopPoint/Search/": {"matches": [{"id": "910GEDINBUR", "name": "Edinburgh Rail Station",
                                            "lat": 55.952, "lon": -3.189}]},
        "data.police.uk/api/crime-last-updated": {"date": "2026-09-01"},
        "air-quality-api.open-meteo.com": {"hourly": {"time": []}},
        "api.open-meteo.com/v1/forecast": {},
    })
    monkeypatch.setattr(l1_http, "fetch_bytes", fake)
    return fake


# --- Coverage ---
def test_a_postcode_outside_london_is_refused(net):
    with pytest.raises(l1_http.NotFound) as failure:
        call("outdoors.walk", {"task": "weather", "place": "M1 1AE"})
    assert failure.value.user_message == "I only cover London, and M1 1AE is outside it."


def test_a_station_outside_london_is_refused(net):
    with pytest.raises(l1_http.NotFound) as failure:
        call("safety.help", {"task": "crime", "place": "Edinburgh"})
    assert failure.value.user_message == "I only cover London, and Edinburgh is outside it."


def test_coordinates_outside_london_are_refused(net):
    with pytest.raises(l1_http.NotFound):
        call("outdoors.walk", {"task": "weather", "place": "55.95,-3.19"})
