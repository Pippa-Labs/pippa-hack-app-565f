"""Hourly forecast and air quality for a London point from Open-Meteo (no key)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import l1_http
from pippa_agentapp_sdk import AgentAppError

ATTRIBUTION = "Weather: Open-Meteo (CC BY 4.0)"
LONDON = ZoneInfo("Europe/London")
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
SERVICE = "Open-Meteo"
MAX_FORECAST_DAYS = 7
DAY_START_HOUR, DAY_END_HOUR = 8, 22
TOO_FAR = "I can forecast up to 6 days ahead."

# --- WMO weather codes in plain words ---
WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "heavy showers",
    85: "light snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}

HOURLY_FIELDS = (
    "temperature_2m,apparent_temperature,precipitation_probability,precipitation,"
    "snowfall,wind_speed_10m,wind_gusts_10m,uv_index,weather_code,is_day"
)

# European AQI upper bounds and their band names
NO_AIR_READING = {"aqi": None, "band": None, "pm2_5": None, "no2": None}
AQI_BANDS = ((20, "good"), (40, "fair"), (60, "moderate"), (80, "poor"), (100, "very poor"))


# --- Time helpers ---
def _local(text: str) -> datetime:
    """Attach London time to Open-Meteo's naive local timestamp."""
    return datetime.fromisoformat(text).replace(tzinfo=LONDON)


def _hour_index(times: list[datetime], now: datetime) -> int:
    """Index of the hour containing now."""
    current = now.astimezone(LONDON).replace(minute=0, second=0, microsecond=0)
    for index, moment in enumerate(times):
        if moment == current:
            return index
    raise l1_http.refuse(l1_http.NotFound, "The weather service had no data for this hour.", f"hour {current} not in response")


def _pick(values: list, index: int):
    """Value at index, or None when the list is short or null there."""
    return values[index] if index < len(values) else None


# --- Forecast ---
def _fetch_forecast(lat: float, lon: float, days: int) -> tuple[dict, dict]:
    """Open-Meteo hourly and daily columns for `days` London days from today."""
    query = urlencode({
        "latitude": lat, "longitude": lon, "hourly": HOURLY_FIELDS,
        "daily": "sunrise,sunset", "timezone": "Europe/London",
        "forecast_days": max(1, min(MAX_FORECAST_DAYS, days)),
    })
    data = l1_http.get_json(f"{FORECAST_URL}?{query}", SERVICE)
    hourly = data.get("hourly") if isinstance(data, dict) else None
    daily = data.get("daily") if isinstance(data, dict) else None
    if not hourly or not hourly.get("time") or not daily or not daily.get("sunrise"):
        raise l1_http.refuse(l1_http.NotFound, "The weather service had no forecast for that spot.", "empty forecast")
    return hourly, daily


def _row(hourly: dict, times: list[datetime], i: int) -> dict:
    """One hour of Open-Meteo columns as a dict."""
    code = _pick(hourly["weather_code"], i)
    return {
        "time": times[i],
        "temp_c": _pick(hourly["temperature_2m"], i),
        "feels_c": _pick(hourly["apparent_temperature"], i),
        "rain_chance": _pick(hourly["precipitation_probability"], i),
        "rain_mm": _pick(hourly["precipitation"], i),
        "snow_cm": _pick(hourly.get("snowfall", []), i),
        "wind_kmh": _pick(hourly["wind_speed_10m"], i),
        "gust_kmh": _pick(hourly["wind_gusts_10m"], i),
        "uv": _pick(hourly["uv_index"], i),
        "code": code,
        "description": WEATHER_CODES.get(code, "unsettled weather"),
    }


def forecast(lat: float, lon: float, now: datetime, hours: int = 12, forecast_days: int = 2) -> dict:
    """Hourly weather from the current hour, plus sunrise/sunset (next_sunrise after dark)."""
    hourly, daily = _fetch_forecast(lat, lon, forecast_days)
    times = [_local(t) for t in hourly["time"]]
    start = _hour_index(times, now)
    result = {"hours": [_row(hourly, times, i) for i in range(start, min(start + hours, len(times)))]}
    result.update(_sun_times(daily, now))
    return result


def day_forecast(lat: float, lon: float, day: date, now: datetime) -> dict:
    """Daytime hours (08:00-22:00) and sun times for one London date, today up to 6 days ahead."""
    ahead = (day - now.astimezone(LONDON).date()).days
    if not 0 <= ahead < MAX_FORECAST_DAYS:
        raise l1_http.refuse(l1_http.NeedsInput, TOO_FAR, f"{day} is {ahead} days ahead")
    hourly, daily = _fetch_forecast(lat, lon, ahead + 1)
    times = [_local(t) for t in hourly["time"]]
    rows = [_row(hourly, times, i) for i, moment in enumerate(times)
            if moment.date() == day and DAY_START_HOUR <= moment.hour < DAY_END_HOUR]
    if not rows:
        raise l1_http.refuse(l1_http.NotFound, "The weather service had no forecast for that day.", f"{day} missing")
    sunrise, sunset = _sun_for(daily, day)
    return {"hours": rows, "sunrise": sunrise, "sunset": sunset}


def _sun_by_date(daily: dict) -> dict[date, tuple[datetime, datetime]]:
    """(sunrise, sunset) keyed by London date."""
    return {_local(rise).date(): (_local(rise), _local(set_)) for rise, set_ in zip(daily["sunrise"], daily["sunset"])}


def _sun_for(daily: dict, day: date) -> tuple[datetime, datetime]:
    """Sunrise and sunset on one London date."""
    sun = _sun_by_date(daily).get(day)
    if sun is None:
        raise l1_http.refuse(l1_http.NotFound, "The weather service had no sun times for that day.", f"{day} missing from daily")
    return sun


def _sun_times(daily: dict, now: datetime) -> dict:
    """Sunrise and sunset for now's London date; next_sunrise once the sun has set."""
    local_now = now.astimezone(LONDON)
    sunrise, sunset = _sun_for(daily, local_now.date())
    sun = {"sunrise": sunrise, "sunset": sunset}
    tomorrow = _sun_by_date(daily).get(local_now.date() + timedelta(days=1))
    if local_now > sunset and tomorrow:
        sun["next_sunrise"] = tomorrow[0]
    return sun


# --- Air quality ---
def air_quality(lat: float, lon: float, now: datetime) -> dict:
    """Current-hour {"aqi", "band", "pm2_5", "no2"}; any value may be None."""
    query = urlencode({
        "latitude": lat, "longitude": lon, "timezone": "Europe/London",
        "hourly": "pm2_5,nitrogen_dioxide,european_aqi", "forecast_days": 2,
    })
    data = l1_http.get_json(f"{AIR_URL}?{query}", SERVICE)
    hourly = data.get("hourly") if isinstance(data, dict) else None
    if not hourly or not hourly.get("time"):
        raise l1_http.refuse(l1_http.NotFound, "The air quality service had no data for that spot.", "empty air quality")
    index = _hour_index([_local(t) for t in hourly["time"]], now)
    aqi = _pick(hourly.get("european_aqi", []), index)
    return {
        "aqi": None if aqi is None else round(aqi),
        "band": _aqi_band(aqi),
        "pm2_5": _pick(hourly.get("pm2_5", []), index),
        "no2": _pick(hourly.get("nitrogen_dioxide", []), index),
    }


def _aqi_band(aqi: float | None) -> str | None:
    """European AQI band name, or None when there is no reading."""
    if aqi is None:
        return None
    return next((name for limit, name in AQI_BANDS if aqi <= limit), "extremely poor")


# --- Both together ---
def forecast_and_air(lat: float, lon: float, hours: int, now: datetime) -> tuple[dict, dict]:
    """Fetch forecast and air quality in parallel; air quality is optional."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        weather = pool.submit(forecast, lat, lon, now, hours)
        air = pool.submit(air_quality, lat, lon, now)
        try:
            air_now = air.result()
        except AgentAppError:
            air_now = dict(NO_AIR_READING)
        return weather.result(), air_now
