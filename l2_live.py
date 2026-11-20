"""Level 2: live TfL lookups shaped into answers (line status, departures, bike docks)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import l1_geo
import l1_tfl
from l1_http import NeedsInput, NotFound, refuse
from pippa_agentapp_sdk import AgentAppError, ServiceUnavailableError

SOURCE = l1_tfl.SOURCE  # TfL requires this exact attribution
GOOD_SERVICE = 10
MAX_POINTS = 3
MAX_ARRIVALS = 6
MAX_STOPS = 2


def _text(inputs: dict, name: str) -> str:
    """A string input, with None and blanks treated as absent."""
    return (inputs.get(name) or "").strip()


# --- Line status ---
def _reason(line: dict) -> str:
    """The reason without TfL's repeated "<Line> Line: " prefix."""
    return line["reason"].removeprefix(f"{line['line']} Line: ").strip()


def _line_name(name: str) -> str:
    """'Central' as 'the Central line'; names that already say it stay as they are."""
    return f"the {name}" if name.lower().endswith(("line", "dlr")) else f"the {name} line"


def _tfl_and_rail_statuses() -> tuple[list[dict], list[dict]]:
    """TfL lines and National Rail operators in parallel; National Rail is optional."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        tfl = pool.submit(l1_tfl.line_statuses)
        rail = pool.submit(l1_tfl.line_statuses, "national-rail")
        try:
            rail_lines = rail.result()
        except AgentAppError:
            rail_lines = []
        return tfl.result(), rail_lines


def line_status(inputs: dict, now: datetime) -> dict:
    """Current status of every line, or of the one line named."""
    lines, rail = _tfl_and_rail_statuses()
    if not lines:
        raise ServiceUnavailableError("TfL line status came back empty")
    wanted = _text(inputs, "line").lower().removesuffix(" line")
    if wanted:
        lines = ([line for line in lines if wanted in line["line"].lower()]
                 or [line for line in rail if wanted in line["line"].lower()])
        if not lines:
            raise refuse(NotFound, f"I could not find a line called {_text(inputs, 'line')}.", f"no line {wanted!r}")
    problems = [line for line in lines if line["severity"] != GOOD_SERVICE]
    if wanted and len(lines) == 1:
        line = lines[0]
        name = line["line"] if line in rail else _line_name(line["line"])
        headline = f"{name[0].upper()}{name[1:]} has {line['status'].lower()}."
        points = [] if line["severity"] == GOOD_SERVICE else [_short_reason(line)]
    elif problems:
        headline = f"{len(problems)} of {len(lines)} lines have problems; the rest run well."
        if len(problems) == len(lines):
            headline = f"All {len(lines)} lines have problems."
        points = [f"{line['line']}: {line['status']}" for line in problems]
    else:
        headline = "Good service on every Tube, Overground, Elizabeth and DLR line."
        points = []
    rail_problems = [line for line in rail if line["severity"] != GOOD_SERVICE]
    if not wanted and rail_problems:
        points = points[:MAX_POINTS - 1] + [f"National Rail: {len(rail_problems)} disrupted"]
    return {"headline": headline, "key_points": points[:MAX_POINTS] or ["No delays reported"], "source": SOURCE}


def _short_reason(line: dict) -> str:
    """The first few words of TfL's reason, or the status alone."""
    words = [word for word in _reason(line).rstrip(".").split() if "://" not in word]
    return " ".join(words[:7]) if words else line["status"]


# --- Next departures ---
def _strip_station(name: str) -> str:
    """Drop TfL's ' Underground Station' style suffixes and odd spacing."""
    name = " ".join(name.split())
    for suffix in (" Underground Station", " Rail Station", " DLR Station", " Station"):
        name = name.removesuffix(suffix)
    return name


def _merged_arrivals(stop_ids: list[str]) -> list[dict]:
    """Arrivals at several stops at once, soonest first; a bus seen from two stops counts once."""
    with ThreadPoolExecutor(max_workers=len(stop_ids)) as pool:
        rows = [row for batch in pool.map(l1_tfl.arrivals, stop_ids, [MAX_ARRIVALS] * len(stop_ids)) for row in batch]
    unique = {(row["line"], row["destination"], row["minutes"]): row for row in rows}
    return sorted(unique.values(), key=lambda row: row["minutes"])[:MAX_ARRIVALS]


def _station_departures(name: str) -> tuple[str, list[dict]]:
    """Departures from a rail station; hub ids carry no arrivals, so use their rail children."""
    station = l1_tfl.find_station(name)
    stop_ids = [station["id"]]
    if not station["id"].startswith(("940G", "910G")):
        stop_ids = l1_tfl.station_facilities(station["id"])["station_ids"][1:1 + MAX_STOPS] or stop_ids
    return _strip_station(station["name"]), _merged_arrivals(stop_ids)


def _bus_departures(name: str) -> tuple[str, list[dict]]:
    """Departures from the bus stops nearest a place."""
    point = l1_geo.resolve(name)
    stops = l1_tfl.stops_near(point["lat"], point["lon"])[:MAX_STOPS]
    if not stops:
        raise refuse(NotFound, f"I could not find a bus stop near {name}.", f"no stops near {name!r}")
    return f"bus stops near {point['label']}", _merged_arrivals([stop["id"] for stop in stops])


def next_departures(inputs: dict, now: datetime) -> dict:
    """Next trains from a named station, or next buses from the stops nearest a place."""
    place = _text(inputs, "place")
    if not place:
        raise refuse(NeedsInput, "Which station or stop? Tell me its name and I will look up the next departures.", "no place")
    try:
        label, rows = _station_departures(place)
    except NotFound:
        label, rows = _bus_departures(place)
    if not rows:
        return {"headline": f"No departures predicted from {label} right now.",
                "key_points": ["TfL has no live predictions here"], "source": SOURCE, "place": label}
    first = rows[0]
    return {
        "headline": f"Next from {label}: {_departure(first)}.",
        "key_points": [_departure(row) for row in rows[1:1 + MAX_POINTS]] or ["No later departures predicted"],
        "source": SOURCE,
        "place": label,
    }


def _departure(row: dict) -> str:
    """'Victoria to Brixton in 2 min'."""
    when = "due now" if row["minutes"] <= 0 else f"in {row['minutes']} min"
    return f"{row['line']} to {_strip_station(row['destination'])} {when}"


# --- Santander Cycles docks ---
def bike_docks(inputs: dict, now: datetime) -> dict:
    """Bikes, e-bikes and free docks at the hire docks matching a place."""
    place = _text(inputs, "place")
    if not place:
        raise refuse(NeedsInput, "Where? Tell me a street or area and I will check the bike docks there.", "no place")
    docks = l1_tfl.bike_points(place)
    bikes = sum(dock["bikes"] for dock in docks)
    free = sum(dock["empty_docks"] for dock in docks)
    busiest = sorted(docks, key=lambda dock: -dock["bikes"])[:MAX_POINTS]
    return {
        "headline": f"{bikes} bikes and {free} free docks at {len(docks)} docks near {place}.",
        "key_points": [f"{_dock_name(dock)}: {dock['bikes']} bikes" for dock in busiest],
        "source": SOURCE,
        "place": place,
    }


def _dock_name(dock: dict) -> str:
    """The street part of a dock name: 'River Street , Clerkenwell' becomes 'River Street'."""
    return " ".join(dock["name"].split(",")[0].split())
