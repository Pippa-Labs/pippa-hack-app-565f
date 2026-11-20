"""Level 2: weather watch and walk-now advice for a London spot."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import NamedTuple

import l1_geo
import l1_weather

CENTRAL_LONDON = {"lat": 51.5074, "lon": -0.1278, "label": "central London"}
LONDON = l1_weather.LONDON
THUNDER_CODES = {95, 96, 99}
BAD_AIR = {"very poor", "extremely poor"}
PARK_RADIUS_KM = 8.0
SOURCE = "Open-Meteo"
MAX_POINTS = 3


# --- Curated London places: big parks for walks, viewpoints for sunsets ---
class Place(NamedTuple):
    name: str
    lat: float
    lon: float
    park: bool
    sunset: bool


PLACES = (
    Place("Hyde Park", 51.5050, -0.1650, park=True, sunset=True),
    Place("Regent's Park", 51.5313, -0.1570, park=True, sunset=False),
    Place("Victoria Park", 51.5363, -0.0396, park=True, sunset=False),
    Place("Hampstead Heath", 51.5608, -0.1650, park=True, sunset=False),
    Place("Battersea Park", 51.4793, -0.1568, park=True, sunset=False),
    Place("Greenwich Park", 51.4769, 0.0005, park=True, sunset=True),
    Place("Clapham Common", 51.4560, -0.1480, park=True, sunset=False),
    Place("Richmond Park", 51.4420, -0.2750, park=True, sunset=False),
    Place("Brockwell Park", 51.4500, -0.1050, park=True, sunset=False),
    Place("Finsbury Park", 51.5703, -0.1058, park=True, sunset=False),
    Place("Primrose Hill", 51.5394, -0.1609, park=False, sunset=True),
    Place("Parliament Hill", 51.5600, -0.1590, park=False, sunset=True),
    Place("Waterloo Bridge", 51.5085, -0.1166, park=False, sunset=True),
)
PARKS = tuple(place for place in PLACES if place.park)
SUNSET_SPOTS = tuple(place for place in PLACES if place.sunset)


def nearest(point: dict, places: tuple[Place, ...]) -> tuple[Place, float]:
    """The closest of `places` to a point, with its distance in km."""
    return min(((place, l1_geo.distance_km(point["lat"], point["lon"], place.lat, place.lon)) for place in places),
               key=lambda pair: pair[1])


# --- Small helpers ---
def _blank(value) -> bool:
    return value is None or value == ""


def _clamp_int(value, low: int, high: int, default: int) -> int:
    """Whole number from user input, kept within low..high."""
    if _blank(value):
        return default
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default
    return max(low, min(high, number))


def _point(inputs: dict) -> dict:
    """Central London when no place is given, else the resolved place."""
    place = inputs.get("place")
    if _blank(place) or not str(place).strip():
        return CENTRAL_LONDON
    return l1_geo.resolve(str(place))


def _clock(moment: datetime) -> str:
    return moment.astimezone(LONDON).strftime("%H:%M")


def _top(values: list) -> float | None:
    """Largest value that is not None."""
    known = [value for value in values if value is not None]
    return max(known) if known else None


def _is_wet(row: dict) -> bool:
    return (row["rain_chance"] or 0) >= 50 or (row["rain_mm"] or 0) >= 0.5


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _now_point(row: dict) -> str:
    """'Now partly cloudy, 10°C'."""
    temperature = f", {round(row['temp_c'])}°C" if row["temp_c"] is not None else ""
    return f"Now {row['description']}{temperature}"


def _with_temperature(result: dict, row: dict) -> dict:
    if row["temp_c"] is not None:
        result["temperature_c"] = round(row["temp_c"], 1)
    return result


# --- Weather watch: first notable change ---
def _first_change(rows: list[dict]) -> str | None:
    """Describe the first rain, gust or UV change after the current hour."""
    now_wet = _is_wet(rows[0])
    for row in rows[1:]:
        if not now_wet and _is_wet(row):
            return f"rain likely from {_clock(row['time'])}"
        if now_wet and not _is_wet(row):
            return f"drier from {_clock(row['time'])}"
        if (row["gust_kmh"] or 0) >= 50:
            return f"strong gusts from {_clock(row['time'])}"
        if (row["uv"] or 0) >= 6:
            return f"high UV from {_clock(row['time'])}"
    return None


def _rain_point(rows: list[dict]) -> str | None:
    peak = _top([row["rain_chance"] for row in rows])
    if peak is None:
        return None
    peak_row = next(row for row in rows if row["rain_chance"] == peak)
    return f"Rain chance up to {round(peak)}% at {_clock(peak_row['time'])}"


def _sunset_point(weather: dict, now: datetime) -> str:
    was = " (passed)" if now.astimezone(LONDON) > weather["sunset"] else ""
    return f"Sunset {_clock(weather['sunset'])}{was}"


def _air_point(air: dict) -> str | None:
    return f"Air quality {air['band']}" if air.get("band") else None


def _present(points: list) -> list[str]:
    return [point for point in points if point]


# --- Weather watch entry ---
def weather_watch(inputs: dict, now: datetime) -> dict:
    """Conditions now, the first notable change and the main numbers for a window."""
    point = _point(inputs)
    hours = _clamp_int(inputs.get("hours"), 1, 24, 12)
    weather, air = l1_weather.forecast_and_air(point["lat"], point["lon"], hours, now)
    rows = weather["hours"]
    first = rows[0]
    temperature = f", {round(first['temp_c'])}°C" if first["temp_c"] is not None else ""
    change = _first_change(rows) or f"no big change for {hours} h"
    result = {
        "headline": f"{_sentence(first['description'])}{temperature} in {point['label']}; {change}.",
        "key_points": _present([_rain_point(rows), _sunset_point(weather, now), _air_point(air)])[:MAX_POINTS],
        "source": SOURCE,
    }
    return _with_temperature(result, first)


# --- Walk rating ---
def _rate_hour(row: dict) -> tuple[int, list[str]]:
    """Rate one hour: 0 go, 1 go with care, 2 wait, with short reasons."""
    chance, rain_mm, gust = row["rain_chance"] or 0, row["rain_mm"] or 0, row["gust_kmh"] or 0
    level, reasons = 0, []
    if chance >= 70 or rain_mm >= 1:
        level, reasons = 2, [f"rain likely at {_clock(row['time'])} ({round(chance)}%)"]
    elif chance >= 40:
        level, reasons = 1, [f"{round(chance)}% chance of rain at {_clock(row['time'])}"]
    if gust >= 60:
        level, reasons = 2, reasons + [f"gusts to {round(gust)} km/h"]
    elif gust >= 40:
        level, reasons = max(level, 1), reasons + [f"gusts to {round(gust)} km/h"]
    if row["code"] in THUNDER_CODES:
        level, reasons = 2, reasons + ["thunderstorms forecast"]
    if row["feels_c"] is not None and row["feels_c"] < 3:
        level, reasons = max(level, 1), reasons + [f"feels like {round(row['feels_c'])}°C"]
    return level, reasons


def _rate_walk(rows: list[dict], air: dict, start: datetime, ends: datetime, weather: dict) -> tuple[int, list[str]]:
    """Worst rating across the walk's hours, plus air and daylight."""
    level, reasons = 0, []
    for row in rows:
        row_level, row_reasons = _rate_hour(row)
        level = max(level, row_level)
        reasons += [reason for reason in row_reasons if reason not in reasons]
    if air.get("band") in BAD_AIR:
        level, reasons = 2, reasons + [f"air quality {air['band']}"]
    elif air.get("band") == "poor":
        level, reasons = max(level, 1), reasons + ["air quality poor"]
    if start >= weather["sunset"] or start < weather["sunrise"]:
        level, reasons = max(level, 1), reasons + ["already dark"]
    elif ends > weather["sunset"]:
        level, reasons = max(level, 1), reasons + [f"dark after {_clock(weather['sunset'])}"]
    return level, reasons


def _next_better_hour(rows: list[dict], start: datetime) -> dict | None:
    """First later hour that rates a clean go."""
    return next((row for row in rows if row["time"] > start and _rate_hour(row)[0] == 0), None)


def _park_point(point: dict) -> str | None:
    """'Nearest park: Hyde Park, 1.2 km' within PARK_RADIUS_KM."""
    park, km = nearest(point, PARKS)
    return f"Nearest park: {park.name}, {km:.1f} km" if km <= PARK_RADIUS_KM else None


# --- Walk now entry ---
VERDICTS = ("go", "go with care", "wait")


def walk_now(inputs: dict, now: datetime) -> dict:
    """Say whether now is a good time for a walk of the given length."""
    point = _point(inputs)
    minutes = _clamp_int(inputs.get("minutes"), 5, 240, 30)
    weather, air = l1_weather.forecast_and_air(point["lat"], point["lon"], 12, now)
    all_rows = weather["hours"]
    local_now = now.astimezone(LONDON)
    ends = local_now + timedelta(minutes=minutes)
    window = [r for r in all_rows if r["time"] < ends and r["time"] + timedelta(hours=1) > local_now]
    level, reasons = _rate_walk(window, air, local_now, ends, weather)
    first = window[0]
    if level == 0:
        headline = f"Go: {first['description']} near {point['label']}, fine for {minutes} min."
        extra = _park_point(point)
    else:
        headline = f"{_sentence(VERDICTS[level])}: {reasons[0]} near {point['label']}."
        better = _next_better_hour(all_rows, local_now) if level == 2 else None
        extra = f"Better from {_clock(better['time'])}" if better else _park_point(point)
    points = _present([_now_point(first), extra, *(_sentence(reason) for reason in reasons[1:])])
    result = {
        "headline": headline,
        "key_points": points[:MAX_POINTS],
        "source": SOURCE,
        "verdict": VERDICTS[level],
    }
    return _with_temperature(result, first)
