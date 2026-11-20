"""Level 2: weather watch and walk-now advice for a London spot."""

from __future__ import annotations

from datetime import datetime, timedelta

import l1_geo
import l1_weather

CENTRAL_LONDON = {"lat": 51.5074, "lon": -0.1278, "label": "central London"}
LONDON = l1_weather.LONDON
THUNDER_CODES = {95, 96, 99}
BAD_AIR = {"very poor", "extremely poor"}
PARK_RADIUS_KM = 8.0

# --- Curated London parks ---
PARKS = (
    ("Hyde Park", 51.5073, -0.1657),
    ("Regent's Park", 51.5313, -0.1570),
    ("Victoria Park", 51.5363, -0.0396),
    ("Hampstead Heath", 51.5608, -0.1650),
    ("Battersea Park", 51.4793, -0.1568),
    ("Greenwich Park", 51.4769, 0.0005),
    ("Clapham Common", 51.4560, -0.1480),
    ("Richmond Park", 51.4420, -0.2750),
    ("Brockwell Park", 51.4500, -0.1050),
    ("Finsbury Park", 51.5703, -0.1058),
)

# --- Small helpers ---
def _blank(value) -> bool:
    return value is None or value == ""


def _clamp_int(value, low: int, high: int, default: int) -> int:
    """Whole number from user input, kept within low..high."""
    if _blank(value):
        return default
    try:
        number = int(float(value))
    except (TypeError, ValueError):
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


def _rain_fact(rows: list[dict]) -> str:
    chances = [row["rain_chance"] for row in rows]
    peak = _top(chances)
    if peak is None:
        return "No rain forecast available."
    peak_row = next(row for row in rows if row["rain_chance"] == peak)
    return f"Rain chance peaks at {round(peak)}% around {_clock(peak_row['time'])}."


def _wind_fact(rows: list[dict]) -> str | None:
    wind, gust = _top([r["wind_kmh"] for r in rows]), _top([r["gust_kmh"] for r in rows])
    if wind is None and gust is None:
        return None
    parts = []
    if wind is not None:
        parts.append(f"wind up to {round(wind)} km/h")
    if gust is not None:
        parts.append(f"gusts up to {round(gust)} km/h")
    return _sentence(", ".join(parts)) + "."


def _air_fact(air: dict) -> str | None:
    return f"Air quality is {air['band']}." if air.get("band") else None


def _sunset_fact(weather: dict, now: datetime) -> str:
    sunset = weather["sunset"]
    if now.astimezone(LONDON) > sunset:
        return f"Sunset was at {_clock(sunset)}."
    return f"Sunset is at {_clock(sunset)}."


def _present(facts: list) -> list[str]:
    return [fact for fact in facts if fact]


# --- Weather watch entry ---
def weather_watch(inputs: dict, now: datetime) -> dict:
    """Conditions now, the first notable change and the main numbers for a window."""
    point = _point(inputs)
    hours = _clamp_int(inputs.get("hours"), 1, 24, 12)
    weather, air = l1_weather.forecast_and_air(point["lat"], point["lon"], hours, now)
    rows = weather["hours"]
    first = rows[0]
    change = _first_change(rows)
    outlook = change if change else f"no big changes expected for {hours} hours"
    temperature = f" and {round(first['temp_c'])}°C" if first["temp_c"] is not None else ""
    headline = f"{_sentence(first['description'])}{temperature} in {point['label']} now; {outlook}."
    facts = _present([_rain_fact(rows), _wind_fact(rows), _air_fact(air), _sunset_fact(weather, now)])
    result = {
        "headline": headline,
        "facts": facts,
        "source": l1_weather.ATTRIBUTION,
        "as_of": first["time"].isoformat(),
    }
    if first["temp_c"] is not None:
        result["temperature_c"] = round(first["temp_c"], 1)
    return result


# --- Walk rating ---
def _rate_hour(row: dict) -> tuple[int, list[str]]:
    """Rate one hour: 0 go, 1 go with care, 2 wait, with plain reasons."""
    chance, rain_mm, gust = row["rain_chance"] or 0, row["rain_mm"] or 0, row["gust_kmh"] or 0
    level, reasons = 0, []
    if chance >= 70 or rain_mm >= 1:
        level, reasons = 2, [f"rain is likely around {_clock(row['time'])} ({round(chance)}% chance)"]
    elif chance >= 40:
        level, reasons = 1, [f"a {round(chance)}% chance of rain around {_clock(row['time'])}"]
    if gust >= 60:
        level, reasons = 2, reasons + [f"gusts up to {round(gust)} km/h"]
    elif gust >= 40:
        level, reasons = max(level, 1), reasons + [f"gusts up to {round(gust)} km/h"]
    if row["code"] in THUNDER_CODES:
        level, reasons = 2, reasons + ["thunderstorms are forecast"]
    if row["feels_c"] is not None and row["feels_c"] < 3:
        level, reasons = max(level, 1), reasons + [f"it feels like {round(row['feels_c'])}°C"]
    return level, reasons


def _rate_walk(rows: list[dict], air: dict, ends: datetime, sunset: datetime) -> tuple[int, list[str]]:
    """Worst rating across the walk's hours, plus air and daylight."""
    level, reasons = 0, []
    for row in rows:
        row_level, row_reasons = _rate_hour(row)
        level = max(level, row_level)
        reasons += [reason for reason in row_reasons if reason not in reasons]
    if air.get("band") in BAD_AIR:
        level, reasons = 2, reasons + [f"air quality is {air['band']}"]
    elif air.get("band") == "poor":
        level, reasons = max(level, 1), reasons + ["air quality is poor"]
    if ends > sunset:
        level, reasons = max(level, 1), reasons + [f"it will be dark after {_clock(sunset)}"]
    return level, reasons


def _next_better_hour(rows: list[dict], start: datetime) -> dict | None:
    """First later hour that rates a clean go."""
    return next((row for row in rows if row["time"] > start and _rate_hour(row)[0] == 0), None)


def _nearest_park(point: dict) -> tuple[str, float] | None:
    """Closest curated park within PARK_RADIUS_KM."""
    nearest = min(
        ((name, l1_geo.distance_km(point["lat"], point["lon"], lat, lon)) for name, lat, lon in PARKS),
        key=lambda pair: pair[1],
    )
    return nearest if nearest[1] <= PARK_RADIUS_KM else None


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
    level, reasons = _rate_walk(window, air, ends, weather["sunset"])
    verdict = VERDICTS[level]

    first = window[0]
    facts = _present([
        f"Now: {first['description']}, {round(first['temp_c'])}°C." if first["temp_c"] is not None else None,
        _wind_fact(window),
        _air_fact(air),
    ])
    if level == 2:
        better = _next_better_hour(all_rows, local_now)
        facts.append(f"A better time starts around {_clock(better['time'])}." if better
                     else "No better hour shows up in the next 12 hours.")
    else:
        park = _nearest_park(point)
        if park:
            facts.append(f"Nearest big park: {park[0]}, about {park[1]:.1f} km away.")
    facts = facts[:6]

    reason_text = "; ".join(reasons)
    if level == 0:
        headline = f"Good for a {minutes}-minute walk near {point['label']} now: {first['description']}."
    elif level == 1:
        headline = f"Go with care on a {minutes}-minute walk near {point['label']}: {reason_text}."
    else:
        headline = f"Better to wait before a {minutes}-minute walk near {point['label']}: {reason_text}."
    result = {
        "headline": headline,
        "facts": facts,
        "source": l1_weather.ATTRIBUTION,
        "as_of": all_rows[0]["time"].isoformat(),
        "verdict": verdict,
    }
    if first["temp_c"] is not None:
        result["temperature_c"] = round(first["temp_c"], 1)
    return result
