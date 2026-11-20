"""Level 3: a late-night route using night buses, the Night Tube and other night-time services."""

from __future__ import annotations

from datetime import datetime, time

import l1_tfl
from l2_best_route import (LONDON, as_of, clean, clock_time, extras, hhmm, leg_facts, plan, ride_legs,
                           route_words)
from pippa_agentapp_sdk import AgentAppError

NIGHT_MODES = "tube,bus,overground,elizabeth-line,dlr,walking"
NIGHT_STARTS = time(23, 0)
NIGHT_ENDS = time(5, 0)
DEFAULT_DEPARTURE = time(23, 30)
NIGHT_TUBE_AFTER = time(0, 30)
GOOD_SERVICE = 10
NIGHT_TUBE_NOTE = ("The Night Tube runs on Friday and Saturday nights on the Victoria, Jubilee and "
                   "most of the Central, Northern and Piccadilly lines.")
NIGHT_BUS_NOTE = "Night buses run every night."


# --- Departure time ---
def is_night(moment: datetime) -> bool:
    local = moment.astimezone(LONDON).time()
    return local >= NIGHT_STARTS or local < NIGHT_ENDS


def departure(inputs: dict, now: datetime) -> tuple[datetime, str | None]:
    """When to leave, and a fact saying so when the time was assumed."""
    if clean(inputs.get("depart_at")):
        return clock_time(inputs["depart_at"], now), None
    if is_night(now):
        return now, None
    tonight = now.astimezone(LONDON).replace(hour=DEFAULT_DEPARTURE.hour, minute=DEFAULT_DEPARTURE.minute,
                                             second=0, microsecond=0)
    return tonight, "No time given, so this assumes leaving at 23:30 tonight."


# --- Night services ---
def uses_night_bus(journey: dict) -> bool:
    return any(leg["mode"] == "bus" and leg["line"].startswith("N") for leg in journey["legs"])


def uses_night_tube(journey: dict) -> bool:
    """A tube leg on a journey starting between 00:30 and 05:00."""
    start = journey["start"].astimezone(LONDON).time()
    return any(leg["mode"] == "tube" for leg in journey["legs"]) and NIGHT_TUBE_AFTER <= start < NIGHT_ENDS


def choose(journeys: list[dict]) -> dict:
    """The shortest journey using night services, else the shortest overall."""
    night = [j for j in journeys if uses_night_bus(j) or uses_night_tube(j)]
    return min(night or journeys, key=lambda j: j["duration_minutes"])


def line_problems(journey: dict) -> list[str]:
    """Disruption notes for the tube and rail lines used."""
    used = " ".join(f"{leg['line']} {leg['summary']}".lower() for leg in ride_legs(journey) if leg["mode"] != "bus")
    try:
        statuses = l1_tfl.line_statuses()
    except AgentAppError:
        return []  # a missing status check should not hide the route
    return [f"{s['line']}: {s['status']}" for s in statuses
            if s["severity"] != GOOD_SERVICE and s["line"].lower() in used]


# --- Entry ---
def night_journey(inputs: dict, now: datetime) -> dict:
    """A late-night route between two places."""
    leave, assumption = departure(inputs, now)
    found = plan(inputs, now, when=leave, modes=NIGHT_MODES)
    journey = choose(found["journeys"])
    facts = [assumption] if assumption else []
    facts += leg_facts(journey)
    if uses_night_bus(journey):
        facts.append(NIGHT_BUS_NOTE)
    if uses_night_tube(journey):
        facts.append(NIGHT_TUBE_NOTE)
    facts += line_problems(journey) + journey["disruptions"][:1]
    return {
        "headline": (f"Late-night route from {found['from']} to {found['to']}: {route_words(journey)}, about "
                     f"{journey['duration_minutes']} minutes, leaving {hhmm(journey['start'])}."),
        "facts": facts[:6],
        "source": l1_tfl.SOURCE,
        "as_of": as_of(now),
        **extras(journey),
    }
