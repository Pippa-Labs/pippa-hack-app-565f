"""Level 3: when to leave to arrive somewhere on time, with line and weather checks."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import l1_tfl
import l1_weather
from l1_http import NeedsInput, NotFound, refuse
from l2_best_route import LONDON, as_of, clean, clock_time, extras, hhmm, leg_facts, plan, ride_legs
from pippa_agentapp_sdk import AgentAppError

DEFAULT_BUFFER_MINUTES = 10
MAX_BUFFER_MINUTES = 60
UMBRELLA_CHANCE = 60
FORECAST_HOURS = 36
GOOD_SERVICE = 10


# --- Inputs ---
def buffer_minutes(inputs: dict) -> int:
    """Spare minutes wanted, default 10, clamped to 0-60."""
    text = clean(inputs.get("buffer_minutes"))
    try:
        value = int(float(text)) if text else DEFAULT_BUFFER_MINUTES
    except (ValueError, OverflowError):
        value = DEFAULT_BUFFER_MINUTES
    return max(0, min(MAX_BUFFER_MINUTES, value))


# --- Side checks (each is optional: a failure just leaves its fact out) ---
def line_problems(statuses: list[dict], journey: dict) -> list[str]:
    """Disruption notes for the tube and rail lines this journey uses."""
    used = " ".join(f"{leg['line']} {leg['summary']}".lower() for leg in ride_legs(journey) if leg["mode"] != "bus")
    return [f"{status['line']}: {status['status']}" for status in statuses
            if status["severity"] != GOOD_SERVICE and status["line"].lower() in used]


def lift_notes(faults: list[dict], journey: dict) -> list[str]:
    """Lift faults at stations named in the journey's legs."""
    summaries = " ".join(leg["summary"] for leg in journey["legs"]).lower()
    notes = []
    for fault in faults:
        station = fault["message"].split(":")[0].strip()
        if station and station.lower() in summaries:
            notes.append(f"Lift fault at {station}")
    return list(dict.fromkeys(notes))


def umbrella_note(point: dict, leave: datetime, now: datetime) -> list[str]:
    """An umbrella warning when rain is likely around the leave time."""
    hours = l1_weather.forecast(point["lat"], point["lon"], now, FORECAST_HOURS)["hours"]
    target = leave.astimezone(LONDON).replace(minute=0, second=0, microsecond=0)
    row = next((hour for hour in hours if hour["time"] == target), None)
    if row and (row["rain_chance"] or 0) >= UMBRELLA_CHANCE:
        return [f"Take an umbrella: rain likely around {hhmm(leave)}"]
    return []


def optional(job) -> list:
    """Result of a side check, or an empty list if the service failed."""
    try:
        return job.result()
    except AgentAppError:
        return []


# --- Entry ---
def leave_by(inputs: dict, now: datetime) -> dict:
    """Latest time to leave to arrive by arrive_by, allowing a buffer."""
    if not clean(inputs.get("arrive_by")):
        raise refuse(NeedsInput, "Tell me what time you need to arrive, as HH:MM.", "arrive_by missing")
    deadline = clock_time(inputs["arrive_by"], now)
    spare = buffer_minutes(inputs)
    step_free = clean(inputs.get("preference")) == "step_free"
    with ThreadPoolExecutor(max_workers=3) as pool:
        statuses = pool.submit(l1_tfl.line_statuses)
        lifts = pool.submit(l1_tfl.lift_faults) if step_free else None
        found = plan(inputs, now, when=deadline - timedelta(minutes=spare), arriving=True)
        journey = found["journey"]
        weather = pool.submit(umbrella_note, found["start_point"], journey["start"], now)
        problems = line_problems(optional(statuses), journey)
        lift = lift_notes(optional(lifts), journey) if lifts else []
        rain = optional(weather)
    if journey["start"] < now:
        raise refuse(NotFound, f"Arriving by {hhmm(deadline)} is no longer possible from here; try a later time.",
                     "leave time already past")
    margin = round((deadline - journey["arrival"]).total_seconds() / 60)
    if margin < 0:
        raise refuse(NotFound, f"TfL has no journey that arrives by {hhmm(deadline)}; try a later time.",
                     f"best arrival {journey['arrival']} after deadline")
    facts = [f"Arrive about {hhmm(journey['arrival'])}", *leg_facts(journey), *problems, *lift, *rain]
    return {
        "headline": (f"Leave {found['from']} by {hhmm(journey['start'])} to reach {found['to']} by "
                     f"{hhmm(deadline)} ({journey['duration_minutes']}-minute journey, {margin} minutes spare)."),
        "facts": facts[:6],
        "source": f"{l1_tfl.SOURCE}; {l1_weather.ATTRIBUTION}",
        "as_of": as_of(now),
        **extras(journey),
    }
