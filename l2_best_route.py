"""Level 2: plan the best TfL journey between two places, including late-night routes."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time

import l1_clock
import l1_geo
import l1_tfl
from l1_http import NeedsInput, refuse
from pippa_agentapp_sdk import AgentAppError

LONDON = l1_tfl.LONDON
SOURCE = l1_tfl.SOURCE  # TfL requires this exact attribution
NEEDS_PLACES = "Tell me where you are starting from and where you are going."
EVENING_WORDS = re.compile(
    r"\b(dinner|supper|drinks|evening|tonight|theatre|show|concert|party|after work)\b", re.IGNORECASE)
MAX_VEHICLES = 3
GOOD_SERVICE = 10
NIGHT_MODES = "tube,bus,overground,elizabeth-line,dlr,walking"
NIGHT_STARTS = time(23, 0)
NIGHT_ENDS = time(5, 0)
DEFAULT_NIGHT_DEPARTURE = time(23, 30)
NIGHT_TUBE_AFTER = time(0, 30)
NIGHT_BUS_NOTE = "Night buses run every night"
NIGHT_TUBE_NOTE = "Night Tube runs Friday and Saturday nights"
LIFT_OUT = "Lift out at {}"


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


def is_evening(inputs: dict) -> bool:
    """True when the occasion (dinner, drinks, theatre...) or `tonight` says a bare hour means pm."""
    return inputs.get("tonight") is True or bool(EVENING_WORDS.search(clean(inputs.get("occasion"))))


def clock_time(text, now: datetime, *, evening: bool = False) -> datetime:
    """The next London time the person meant ("6", "half six", "18:30")."""
    return l1_clock.parse_clock(clean(text), now, evening=evening)


# --- Lift faults on a route ---
def _lift_faults() -> list[dict]:
    """Current lift faults, or none if the feed fails: the route matters more than the lift check."""
    try:
        return l1_tfl.lift_faults()
    except AgentAppError:  # an optional check must not hide the route
        return []


def lift_notes(faults: list[dict], journey: dict) -> list[str]:
    """At most one point naming the stations in the journey's legs that have a lift out."""
    summaries = " ".join(leg["summary"] for leg in journey["legs"]).lower()
    stations = (fault["message"].split(":")[0].strip() for fault in faults)
    names = list(dict.fromkeys(name for name in stations if name and name.lower() in summaries))
    return [LIFT_OUT.format(" and ".join(names))] if names else []


# --- Planning ---
def route_between(start: dict, end: dict, preference: str = "fastest", *, when: datetime | None = None,
                  arriving: bool = False, modes: str | None = None) -> list[dict]:
    """TfL journeys between two resolved points."""
    return l1_tfl.journey(
        f"{start['lat']},{start['lon']}", f"{end['lat']},{end['lon']}", when=when, arriving=arriving,
        preference="fastest" if preference == "step_free" else preference, modes=modes,
        step_free=preference == "step_free",
    )


def plan(inputs: dict, now: datetime, *, when: datetime | None = None, arriving: bool = False,
         modes: str | None = None) -> dict:
    """Resolve both places and return the best journey, every option, both points and current lift faults."""
    start_text, end_text = place_names(inputs)
    preference = clean(inputs.get("preference")) or "fastest"
    with ThreadPoolExecutor(max_workers=3) as pool:
        faults_job = pool.submit(_lift_faults)
        start_job, end_job = pool.submit(l1_geo.resolve, start_text), pool.submit(l1_geo.resolve, end_text)
        start, end = start_job.result(), end_job.result()
        journeys = route_between(start, end, preference, when=when, arriving=arriving, modes=modes)
        faults = faults_job.result()
    return {"journey": journeys[0], "journeys": journeys, "from": start_text, "to": end_text,
            "start_point": start, "end_point": end, "preference": preference, "lift_faults": faults}


# --- Describing a journey ---
def hhmm(moment: datetime) -> str:
    return moment.astimezone(LONDON).strftime("%H:%M")


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


def route_words(journey: dict) -> str:
    """The vehicles used, in order: 'Victoria line + bus 38'."""
    labels = list(dict.fromkeys(vehicle_label(leg) for leg in ride_legs(journey)))
    return " + ".join(labels[:MAX_VEHICLES]) or "Walk"


def extras(journey: dict) -> dict:
    return {"duration_minutes": journey["duration_minutes"], "leave_at": hhmm(journey["start"]),
            "arrive_at": hhmm(journey["arrival"])}


def times_point(journey: dict) -> str:
    return f"Leave {hhmm(journey['start'])}, arrive {hhmm(journey['arrival'])}"


def line_problems(statuses: list[dict], journey: dict) -> list[str]:
    """'Central: Minor Delays' for each tube or rail line this journey uses that is not running well."""
    used = " ".join(f"{leg['line']} {leg['summary']}".lower() for leg in ride_legs(journey) if leg["mode"] != "bus")
    return [f"{status['line']}: {status['status']}" for status in statuses
            if status["severity"] != GOOD_SERVICE and status["line"].lower() in used]


# --- Entry: best route ---
def best_route(inputs: dict, now: datetime) -> dict:
    """Best journey between two places, optionally leaving at depart_at."""
    depart_at = clean(inputs.get("depart_at"))
    when = clock_time(depart_at, now, evening=is_evening(inputs)) if depart_at else None
    found = plan(inputs, now, when=when)
    journey = found["journey"]
    points = [times_point(journey)]
    if journey["fare_gbp"] is not None:
        points.append(f"£{journey['fare_gbp']:.2f} with contactless")
    if journey["disruptions"]:
        points.append("Disruption reported on this route")
    lifts = lift_notes(found["lift_faults"], journey)
    points = lifts + points if found["preference"] == "step_free" else points + lifts
    return {
        "headline": f"{route_words(journey)}, {journey['duration_minutes']} min. {times_point(journey)}.",
        "key_points": points,
        "source": SOURCE,
        **extras(journey),
    }


# --- Late-night routes (used by night_journey and the evening plan) ---
def is_night(moment: datetime) -> bool:
    local = moment.astimezone(LONDON).time()
    return local >= NIGHT_STARTS or local < NIGHT_ENDS


def night_departure(inputs: dict, now: datetime) -> tuple[datetime, bool]:
    """When to leave, and whether 23:30 tonight was assumed."""
    if clean(inputs.get("depart_at")):
        return clock_time(inputs["depart_at"], now, evening=True), False
    if is_night(now):
        return now, False
    tonight = now.astimezone(LONDON).replace(hour=DEFAULT_NIGHT_DEPARTURE.hour,
                                             minute=DEFAULT_NIGHT_DEPARTURE.minute, second=0, microsecond=0)
    return tonight, True


def uses_night_bus(journey: dict) -> bool:
    return any(leg["mode"] == "bus" and leg["line"].startswith("N") for leg in journey["legs"])


def uses_night_tube(journey: dict) -> bool:
    """A tube leg on a journey starting between 00:30 and 05:00."""
    start = journey["start"].astimezone(LONDON).time()
    return any(leg["mode"] == "tube" for leg in journey["legs"]) and NIGHT_TUBE_AFTER <= start < NIGHT_ENDS


def choose_night(journeys: list[dict]) -> dict:
    """The shortest journey using night services, else the shortest overall."""
    night = [j for j in journeys if uses_night_bus(j) or uses_night_tube(j)]
    return min(night or journeys, key=lambda j: j["duration_minutes"])


def _statuses() -> list[dict]:
    try:
        return l1_tfl.line_statuses()
    except AgentAppError:
        return []  # a missing status check should not hide the route


def night_route(frm_text: str, to_text: str, when: datetime, now: datetime) -> dict:
    """Late route between two place names: {journey, notes, extras}; notes are night-service and line notes."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        statuses = pool.submit(_statuses)
        found = plan({"from_place": frm_text, "to_place": to_text}, now, when=when, modes=NIGHT_MODES)
        journey = choose_night(found["journeys"])
        problems = line_problems(statuses.result(), journey)
    notes = [NIGHT_BUS_NOTE] if uses_night_bus(journey) else []
    notes += [NIGHT_TUBE_NOTE] if uses_night_tube(journey) else []
    notes += lift_notes(found["lift_faults"], journey)
    return {"journey": journey, "notes": notes + problems, "extras": extras(journey)}
