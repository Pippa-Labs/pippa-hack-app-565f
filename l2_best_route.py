"""Level 2: plan the best TfL journey between two places the person named."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import l1_geo
import l1_tfl
from l1_http import NeedsInput, refuse

LONDON = l1_tfl.LONDON
CLOCK = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
STALE_AFTER = timedelta(hours=1)
NEEDS_PLACES = "Tell me where you are starting from and where you are going."
TITLES = {
    "fastest": "Fastest way",
    "fewest_changes": "Way with fewest changes",
    "least_walking": "Way with least walking",
    "step_free": "Step-free way",
}


# --- Inputs ---
def clean(value) -> str:
    """Text of an optional input; missing, None and blank all become an empty string."""
    return str(value).strip() if value is not None else ""


def place_names(inputs: dict) -> tuple[str, str]:
    """The two required place names, or NeedsInput."""
    start, end = clean(inputs.get("from_place")), clean(inputs.get("to_place"))
    if not start or not end:
        raise refuse(NeedsInput, NEEDS_PLACES, "from_place or to_place missing")
    return start, end


def clock_time(text, now: datetime) -> datetime:
    """HH:MM on now's London date, or tomorrow if it passed more than an hour ago."""
    match = CLOCK.match(clean(text))
    if not match:
        raise refuse(NeedsInput, "Give the time as HH:MM, for example 18:30.", f"bad clock time {text!r}")
    local_now = now.astimezone(LONDON)
    moment = local_now.replace(hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0)
    return moment + timedelta(days=1) if local_now - moment > STALE_AFTER else moment


# --- Planning ---
def plan(inputs: dict, now: datetime, *, when: datetime | None = None, arriving: bool = False,
         modes: str | None = None) -> dict:
    """Resolve both places and return the best journey plus every option and the start point."""
    start_text, end_text = place_names(inputs)
    preference = clean(inputs.get("preference")) or "fastest"
    with ThreadPoolExecutor(max_workers=2) as pool:
        start_job, end_job = pool.submit(l1_geo.resolve, start_text), pool.submit(l1_geo.resolve, end_text)
        start, end = start_job.result(), end_job.result()
    journeys = l1_tfl.journey(
        f"{start['lat']},{start['lon']}", f"{end['lat']},{end['lon']}", when=when, arriving=arriving,
        preference="fastest" if preference == "step_free" else preference, modes=modes,
        step_free=preference == "step_free",
    )
    return {"journey": journeys[0], "journeys": journeys, "from": start_text, "to": end_text,
            "start_point": start, "preference": preference}


# --- Describing a journey ---
def hhmm(moment: datetime) -> str:
    return moment.astimezone(LONDON).strftime("%H:%M")


def as_of(now: datetime) -> str:
    """ISO London time to the minute."""
    return now.astimezone(LONDON).strftime("%Y-%m-%dT%H:%M")


def vehicle_label(leg: dict) -> str:
    """Short name of a leg's vehicle, such as 'Victoria line' or 'night bus N68'."""
    if leg["mode"] == "bus" and leg["line"].startswith("N"):
        return f"night bus {leg['line']}"
    if leg["mode"] == "bus":
        return f"bus {leg['line']}"
    return leg["summary"].split(" to ")[0] if leg["line"] else leg["summary"]


def ride_legs(journey: dict) -> list[dict]:
    """Legs other than walking."""
    return [leg for leg in journey["legs"] if leg["mode"] != "walking"]


def leg_fact(leg: dict) -> str:
    if leg["mode"] == "walking":
        return f"Walk {leg['minutes']} min"
    return f"{leg['summary']}, {leg['minutes']} min"


def leg_facts(journey: dict) -> list[str]:
    """One fact per leg, skipping walks of a minute or less."""
    return [leg_fact(leg) for leg in journey["legs"] if leg["mode"] != "walking" or leg["minutes"] > 1]


def route_words(journey: dict) -> str:
    """The vehicles used, in order, joined for a headline."""
    return ", then ".join(vehicle_label(leg) for leg in ride_legs(journey)) or "walking"


def extras(journey: dict) -> dict:
    return {"duration_minutes": journey["duration_minutes"], "leave_at": hhmm(journey["start"]),
            "arrive_at": hhmm(journey["arrival"])}


# --- Entry ---
def best_route(inputs: dict, now: datetime) -> dict:
    """Best journey between two places, optionally leaving at depart_at."""
    depart_at = clean(inputs.get("depart_at"))
    when = clock_time(depart_at, now) if depart_at else None
    found = plan(inputs, now, when=when)
    journey = found["journey"]
    title = TITLES.get(found["preference"], TITLES["fastest"])
    facts = leg_facts(journey)
    if journey["fare_gbp"] is not None:
        facts.append(f"Pay-as-you-go fare: £{journey['fare_gbp']:.2f}")
    facts.extend(journey["disruptions"][:2])
    return {
        "headline": (f"{title} from {found['from']} to {found['to']}: {route_words(journey)}, "
                     f"{journey['duration_minutes']} minutes, arriving {hhmm(journey['arrival'])}."),
        "facts": facts[:6],
        "source": l1_tfl.SOURCE,
        "as_of": as_of(now),
        **extras(journey),
    }
