"""Level 2: umbrella and extra-layers advice for a stretch of time out in London."""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta

import l1_clock
import l1_geo
import l1_weather

LONDON = l1_weather.LONDON
SOURCE = "Open-Meteo"
CENTRAL_LONDON = {"lat": 51.5074, "lon": -0.1278, "label": "central London"}
SNOW_CODES = {71, 73, 75, 77, 85, 86}
WET_CHANCE = 50
WET_MM = 0.3
DEFAULT_COLD_C = 5.0
EVENING_STARTS = time(18, 0)
MIN_WINDOW = timedelta(hours=3)
HOUR = timedelta(hours=1)


# --- Inputs ---
def _blank(value) -> bool:
    return value is None or str(value).strip() == ""


def _is_tonight(inputs: dict) -> bool:
    return str(inputs.get("tonight")).lower() == "true"


def _cold_limit(value) -> float:
    """The feels-like temperature below which to wrap up (default 5°C)."""
    if _blank(value):
        return DEFAULT_COLD_C
    try:
        limit = float(value)
    except (TypeError, ValueError):
        return DEFAULT_COLD_C
    return limit if math.isfinite(limit) else DEFAULT_COLD_C


def window(inputs: dict, now: datetime) -> tuple[datetime, datetime]:
    """The time out: now (or 18:00 for "tonight") until midnight, at least three hours long."""
    local_now = now.astimezone(LONDON)
    evening = _is_tonight(inputs)
    start = local_now
    if not _blank(inputs.get("from_time")):
        start = max(l1_clock.parse_clock(inputs["from_time"], now, evening=evening), local_now)
    elif evening and local_now.time() < EVENING_STARTS:
        start = local_now.replace(hour=EVENING_STARTS.hour, minute=0, second=0, microsecond=0)
    if not _blank(inputs.get("until_time")):
        end = l1_clock.parse_clock(inputs["until_time"], now, evening=True)
        return start, end if end > start else end + timedelta(days=1)
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return start, max(midnight, start + MIN_WINDOW)  # late at night, still look a few hours ahead


# --- The check (also used for departures in l3_leave_by) ---
def _is_snow(row: dict) -> bool:
    return (row.get("snow_cm") or 0) > 0 or row["code"] in SNOW_CODES


def _is_wet(row: dict) -> bool:
    return (row["rain_chance"] or 0) >= WET_CHANCE or (row["rain_mm"] or 0) >= WET_MM or _is_snow(row)


def _hhmm(moment: datetime) -> str:
    return moment.astimezone(LONDON).strftime("%H:%M")


def outlook(rows: list[dict], start: datetime, end: datetime, cold_below_c: float = DEFAULT_COLD_C) -> dict:
    """Umbrella and layers verdict for the forecast hours overlapping start..end."""
    hours = [row for row in rows if row["time"] < end and row["time"] + HOUR > start]
    wet = [row for row in hours if _is_wet(row)]
    feels = [row for row in hours if row["feels_c"] is not None]
    coldest = min(feels, key=lambda row: row["feels_c"]) if feels else None
    precip = "snow" if any(_is_snow(row) for row in wet) else "rain" if wet else "none"
    result = {
        "umbrella_needed": bool(wet),
        "precip_type": precip,
        "wear_more": bool(coldest and coldest["feels_c"] < cold_below_c),
    }
    if wet:
        result["first_wet_time"] = _hhmm(max(wet[0]["time"], start))
        result["first_wet_chance"] = round(wet[0]["rain_chance"] or 0)
    if coldest:
        result["coldest_feels_like_c"] = round(coldest["feels_c"], 1)
        result["coldest_time"] = _hhmm(max(coldest["time"], start))
    result["advice"] = advice(result)
    return result


def advice(check: dict) -> str:
    """One line: 'Take an umbrella: rain from 21:00 (70%). Wrap up: feels like 2°C by 23:00.'"""
    parts = []
    if check["umbrella_needed"]:
        chance = f" ({check['first_wet_chance']}%)" if check["first_wet_chance"] else ""
        parts.append(f"Take an umbrella: {check['precip_type']} from {check['first_wet_time']}{chance}.")
    if check["wear_more"]:
        parts.append(f"Wrap up: feels like {round(check['coldest_feels_like_c'])}°C by {check['coldest_time']}.")
    if parts:
        return " ".join(parts)
    if "coldest_feels_like_c" in check:
        return f"No umbrella needed; lowest feels-like {round(check['coldest_feels_like_c'])}°C."
    return "No umbrella needed."


# --- Entry ---
def going_out(inputs: dict, now: datetime) -> dict:
    """Whether to take an umbrella or extra layers for the time out."""
    place = inputs.get("place")
    point = CENTRAL_LONDON if _blank(place) else l1_geo.resolve(str(place))
    start, end = window(inputs, now)
    cold = _cold_limit(inputs.get("cold_below_c"))
    current_hour = now.astimezone(LONDON).replace(minute=0, second=0, microsecond=0)
    hours = min(48, math.ceil((end - current_hour) / HOUR) + 1)
    rows = l1_weather.forecast(point["lat"], point["lon"], now, hours)["hours"]
    check = outlook(rows, start, end, cold)
    return {
        "headline": check["advice"],
        "key_points": [*key_points(check), f"Checked {_hhmm(start)}-{_hhmm(end)}, {point['label']}"],
        "source": SOURCE,
        "umbrella_needed": check["umbrella_needed"],
        "wear_more": check["wear_more"],
    }


def key_points(check: dict) -> list[str]:
    """Short umbrella and layers points: 'Umbrella: rain from 19:00', 'Feels like 2°C: wrap up'."""
    points = []
    if check["umbrella_needed"]:
        points.append(f"Umbrella: {check['precip_type']} from {check['first_wet_time']}")
    if check["wear_more"]:
        points.append(f"Feels like {round(check['coldest_feels_like_c'])}°C: wrap up")
    return points
