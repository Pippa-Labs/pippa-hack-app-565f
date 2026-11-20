"""Level 2: live TfL lookups shaped into answers (line status, departures, bike docks)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import l1_geo
import l1_tfl
from l1_http import NeedsInput, NotFound, refuse
from pippa_agentapp_sdk import ServiceUnavailableError

LONDON = ZoneInfo("Europe/London")
GOOD_SERVICE = 10
MAX_FACTS = 6
MAX_STOPS = 2


def _text(inputs: dict, name: str) -> str:
    """A string input, with None and blanks treated as absent."""
    return (inputs.get(name) or "").strip()


def _as_of(now: datetime) -> str:
    """The read time as ISO London time, to the minute."""
    return now.astimezone(LONDON).replace(second=0, microsecond=0).isoformat()


# --- Line status ---
def _reason(line: dict) -> str:
    """The reason without TfL's repeated "<Line> Line: " prefix."""
    return line["reason"].removeprefix(f"{line['line']} Line: ").strip()


def _line_name(name: str) -> str:
    """'Central' as 'the Central line'; names that already say it stay as they are."""
    return f"the {name}" if name.lower().endswith(("line", "dlr")) else f"the {name} line"


def line_status(inputs: dict, now: datetime) -> dict:
    """Current status of every line, or of the one line named."""
    lines = l1_tfl.line_statuses()
    if not lines:
        raise ServiceUnavailableError("TfL line status came back empty")
    wanted = _text(inputs, "line").lower().removesuffix(" line")
    if wanted:
        lines = [line for line in lines if wanted in line["line"].lower()]
        if not lines:
            raise refuse(NotFound, f"I could not find a line called {_text(inputs, 'line')}.", f"no line {wanted!r}")
    problems = [line for line in lines if line["severity"] != GOOD_SERVICE]
    if wanted and len(lines) == 1:
        line = lines[0]
        name = _line_name(line["line"])
        headline = f"{name[0].upper()}{name[1:]} has {line['status'].lower()}."
        if line["severity"] == GOOD_SERVICE:
            facts = [f"No problems reported on {_line_name(line['line'])}."]
        else:
            facts = [_reason(line) or f"TfL reports {line['status'].lower()} but gives no reason."]
    elif problems:
        rest = "; the rest have good service" if len(problems) < len(lines) else ""
        headline = f"{len(problems)} of {len(lines)} lines have problems right now{rest}."
        facts = [f"{line['line']}: {line['status']}. {_reason(line)}".strip() for line in problems]
    else:
        headline = "Good service on every Tube, Overground, Elizabeth line and DLR line."
        facts = [f"{len(lines)} lines checked, none with delays."]
    return {
        "headline": headline,
        "facts": facts[:MAX_FACTS],
        "source": l1_tfl.SOURCE,
        "as_of": _as_of(now),
    }


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
        rows = [row for batch in pool.map(l1_tfl.arrivals, stop_ids, [MAX_FACTS] * len(stop_ids)) for row in batch]
    unique = {(row["line"], row["destination"], row["minutes"]): row for row in rows}
    return sorted(unique.values(), key=lambda row: row["minutes"])[:MAX_FACTS]


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
        headline = f"No departures are predicted from {label} right now."
        facts = ["TfL has no live predictions for this stop at the moment."]
    else:
        first = rows[0]
        headline = f"Next from {label}: {first['line']} to {_strip_station(first['destination'])} {'due now' if first['minutes'] <= 0 else 'in ' + _minutes(first['minutes'])}."
        facts = [
            f"{row['line']} to {_strip_station(row['destination'])}: {_minutes(row['minutes'])}"
            + (f" ({row['platform']})" if row["platform"] else "")
            for row in rows
        ]
    return {"headline": headline, "facts": facts, "source": l1_tfl.SOURCE, "as_of": _as_of(now), "place": label}


def _minutes(value: int) -> str:
    """Minutes until a departure, in words."""
    return "due now" if value <= 0 else f"{value} min"


# --- Santander Cycles docks ---
def bike_docks(inputs: dict, now: datetime) -> dict:
    """Bikes, e-bikes and free docks at the hire docks matching a place."""
    place = _text(inputs, "place")
    if not place:
        raise refuse(NeedsInput, "Where? Tell me a street or area and I will check the bike docks there.", "no place")
    docks = l1_tfl.bike_points(place)
    bikes = sum(dock["bikes"] for dock in docks)
    free = sum(dock["empty_docks"] for dock in docks)
    facts = [
        f"{' '.join(dock['name'].replace(' ,', ',').split())}: {dock['bikes'] - dock['ebikes']} bikes, "
        f"{dock['ebikes']} e-bikes, {dock['empty_docks']} free docks"
        for dock in docks
    ]
    return {
        "headline": f"{bikes} hire bikes and {free} free docks across {len(docks)} docks matching {place}.",
        "facts": facts[:MAX_FACTS],
        "source": l1_tfl.SOURCE,
        "as_of": _as_of(now),
        "place": place,
    }
