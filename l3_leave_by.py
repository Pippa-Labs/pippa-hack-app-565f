"""Level 3: when to leave to arrive on time, plus weather, sunset and the way home after dark."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta

import l1_geo
import l1_tfl
import l1_weather
import l2_crime_watch
import l2_going_out
import l2_walk_now
from l1_http import NeedsInput, NotFound, refuse
from l2_best_route import (SOURCE, clean, clock_time, extras, hhmm, is_evening, line_problems, night_route,
                           ride_legs, route_between, route_words, vehicle_label)
from l2_get_help import DISCREET_LINE
from pippa_agentapp_sdk import AgentAppError

DEFAULT_BUFFER_MINUTES = 10
MAX_BUFFER_MINUTES = 60
FORECAST_HOURS = 36
TIME_AT_DESTINATION = timedelta(hours=2)
WALK_DETOUR = 1.3  # streets are longer than a straight line
WALK_KMH = 4.8
RETURN_BUDGET_SECONDS = 12
MAX_POINTS = 3
HOUR = timedelta(hours=1)


# --- Inputs ---
def buffer_minutes(inputs: dict) -> int:
    """Spare minutes wanted, default 10, clamped to 0-60."""
    text = clean(inputs.get("buffer_minutes"))
    try:
        value = int(float(text)) if text else DEFAULT_BUFFER_MINUTES
    except (ValueError, OverflowError):
        value = DEFAULT_BUFFER_MINUTES
    return max(0, min(MAX_BUFFER_MINUTES, value))


def cold_limit(inputs: dict) -> float:
    value = inputs.get("cold_below_c")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else l2_going_out.DEFAULT_COLD_C


# --- Side checks (each is optional: a failure just leaves it out) ---
def optional(job, default=None):
    """Result of a side job, or the default if it was skipped or its service failed."""
    if job is None:
        return default
    try:
        return job.result()
    except AgentAppError:
        return default


def after(job, fn, *args):
    """Run fn on another job's result, so dependent lookups can share one pool."""
    return fn(job.result(), *args)


def outbound(start_job, end_job, preference: str, arrive_at: datetime) -> dict:
    """The best journey arriving by arrive_at, once both places are resolved."""
    return route_between(start_job.result(), end_job.result(), preference, when=arrive_at, arriving=True)[0]


def forecast_at(point: dict, now: datetime) -> dict:
    return l1_weather.forecast(point["lat"], point["lon"], now, FORECAST_HOURS)


def lift_notes(faults: list[dict], journey: dict) -> list[str]:
    """Lift faults at stations named in the journey's legs."""
    summaries = " ".join(leg["summary"] for leg in journey["legs"]).lower()
    stations = (fault["message"].split(":")[0].strip() for fault in faults)
    return list(dict.fromkeys(f"Lift fault at {name}" for name in stations if name and name.lower() in summaries))


def rows_between(rows: list[dict], start: datetime, end: datetime) -> list[dict]:
    return [row for row in rows if row["time"] < end and row["time"] + HOUR > start]


def weather_check(start_rows: list[dict], end_rows: list[dict], leave: datetime, arrival: datetime,
                  cold: float) -> dict | None:
    """One going-out verdict for the journey (start forecast) and two hours at the destination."""
    rows = rows_between(start_rows or end_rows, leave, arrival) + rows_between(end_rows, arrival,
                                                                               arrival + TIME_AT_DESTINATION)
    if not rows:
        return None
    return l2_going_out.outlook(sorted(rows, key=lambda row: row["time"]), leave, arrival + TIME_AT_DESTINATION, cold)


# --- Sunset ---
def sunset_on(weather: dict, day: datetime) -> datetime:
    """Sunset moved to the deadline's date (it shifts by a minute or two a day)."""
    sunset = weather["sunset"]
    return sunset + timedelta(days=(day.astimezone(sunset.tzinfo).date() - sunset.date()).days)


def sunset_plan(sunset: datetime, end_point: dict, to_text: str, deadline: datetime, now: datetime) -> tuple[str, str | None]:
    """The sunset line and, when it fits before the deadline, a short key point."""
    spot, km = l2_walk_now.nearest(end_point, l2_walk_now.SUNSET_SPOTS)
    walk = max(1, round(km * WALK_DETOUR / WALK_KMH * 60))
    if now >= sunset:
        return f"Sunset was at {hhmm(sunset)}, so it has passed.", None
    if sunset + timedelta(minutes=walk) > deadline:
        return f"Sunset is at {hhmm(sunset)}, too late to fit in before {hhmm(deadline)}.", None
    line = (f"Sunset {hhmm(sunset)}: catch it at {spot.name} ({walk} min walk from {to_text}), "
            f"then arrive by {hhmm(deadline)}.")
    return line, f"Sunset {hhmm(sunset)} at {spot.name}"


# --- The way home after dark (phase 2) ---
def crime_line(found: dict) -> str:
    """'September 2026: 812 recorded crimes near Marble Arch, 95 near Brixton; not a safety rating.'"""
    counts = [f"{place['total']:,} near {place['label']}" for place in found["places"]]
    counts[0] = counts[0].replace(" near ", " recorded crimes near ", 1)
    return f"{l2_crime_watch.month_name(found['month'])}: {', '.join(counts)}; not a safety rating."


def way_home(end_point: dict, to_text: str, return_to: str, return_at: datetime, now: datetime) -> dict:
    """Late return route and recorded crime at both ends, within the budget; slow parts are dropped."""
    pool = ThreadPoolExecutor(max_workers=2)
    route_job = pool.submit(night_route, to_text, return_to, return_at, now)
    crime_job = pool.submit(l2_crime_watch.recorded_near, [end_point, return_to])
    wait([route_job, crime_job], timeout=RETURN_BUDGET_SECONDS)
    pool.shutdown(wait=False, cancel_futures=True)  # the evening plan must answer even if a service hangs
    route, crime = settled(route_job), settled(crime_job)
    return {"route": route, "crime_note": crime_line(crime) if crime else None}


def settled(job):
    """A finished job's result, or None if it is still running, was cancelled or failed."""
    if not job.done() or job.cancelled() or job.exception() is not None:
        return None
    return job.result()


def return_lines(route: dict, return_to: str, assumed: bool) -> list[str]:
    journey = route["journey"]
    lines = [f"{route_words(journey)}, leave {hhmm(journey['start'])}, home {hhmm(journey['arrival'])}"]
    if assumed:
        lines.append(f"Assumes heading to {return_to} at {hhmm(journey['start'])}")
    return (lines + route["notes"])[:MAX_POINTS]


# --- Entry ---
def leave_by(inputs: dict, now: datetime) -> dict:
    """Latest time to leave to arrive by arrive_by, with weather, sunset and the way home."""
    to_text, from_text = clean(inputs.get("to_place")), clean(inputs.get("from_place"))
    if not to_text:
        raise refuse(NeedsInput, "Where do you need to be, and by what time?", "to_place missing")
    if not clean(inputs.get("arrive_by")):
        raise refuse(NeedsInput, "What time do you need to be there? For example 18:00 or 6pm.", "arrive_by missing")
    evening = is_evening(inputs)
    deadline = clock_time(inputs["arrive_by"], now, evening=evening)
    preference = clean(inputs.get("preference")) or "fastest"

    # Phase 1: places, outbound route, line status and weather, chained in one pool
    with ThreadPoolExecutor(max_workers=7) as pool:
        end_job = pool.submit(l1_geo.resolve, to_text)
        end_weather = pool.submit(after, end_job, forecast_at, now)
        start_job = journey_job = start_weather = statuses = lifts = None
        if from_text:
            start_job = pool.submit(l1_geo.resolve, from_text)
            start_weather = pool.submit(after, start_job, forecast_at, now)
            statuses = pool.submit(l1_tfl.line_statuses)
            lifts = pool.submit(l1_tfl.lift_faults) if preference == "step_free" else None
            journey_job = pool.submit(outbound, start_job, end_job, preference,
                                      deadline - timedelta(minutes=buffer_minutes(inputs)))
        end_point = end_job.result()
        journey = journey_job.result() if journey_job else None
        end_wx, start_wx = optional(end_weather), optional(start_weather)
        problems = line_problems(optional(statuses, []), journey) if journey else []
        lift = lift_notes(optional(lifts, []), journey) if journey else []

    if journey and journey["start"] < now:
        raise refuse(NotFound, f"Arriving by {hhmm(deadline)} is no longer possible from here; try a later time.",
                     "leave time already past")
    if journey and journey["arrival"] > deadline:
        raise refuse(NotFound, f"TfL has no journey that arrives by {hhmm(deadline)}; try a later time.",
                     f"best arrival {journey['arrival']} after deadline")

    arrival = journey["arrival"] if journey else deadline
    leave = journey["start"] if journey else deadline
    result = {"source": f"{SOURCE}; Open-Meteo", **(extras(journey) if journey else {})}
    points = [*problems, *lift]

    check = weather_check((start_wx or {}).get("hours", []), (end_wx or {}).get("hours", []), leave, arrival,
                          cold_limit(inputs))
    if check:
        result.update(umbrella_needed=check["umbrella_needed"], wear_more=check["wear_more"])
        points += l2_going_out.key_points(check)

    if end_wx:
        sunset = sunset_on(end_wx, deadline)
        result["sunset_plan"], sunset_point = sunset_plan(sunset, end_point, to_text, deadline, now)
        points += [sunset_point] if sunset_point else []
        return_time = clean(inputs.get("return_time"))
        return_at = clock_time(return_time, now, evening=True) if return_time else arrival + TIME_AT_DESTINATION
        return_at = return_at if return_at > arrival else return_at + timedelta(days=1)
        result["after_dark"] = arrival > sunset or return_at > sunset
        if result["after_dark"]:
            points += after_dark(result, end_point, to_text, clean(inputs.get("return_to")), return_at,
                                 not return_time, sunset, now)

    if journey:
        headline = (f"Leave {hhmm(journey['start'])} for {to_text} by {hhmm(deadline)}: "
                    f"{route_words(journey)}, {journey['duration_minutes']} min.")
    elif check:
        first = check["advice"].split(". ")[0].rstrip(".")
        headline = f"For {to_text} by {hhmm(deadline)}, {first[:1].lower()}{first[1:]}."
    else:
        headline = f"Reach {to_text} by {hhmm(deadline)}; tell me where you start for a route."
    return {"headline": headline, "key_points": points[:MAX_POINTS] or ["No delays or weather worries"], **result}


def after_dark(result: dict, end_point: dict, to_text: str, return_to: str, return_at: datetime,
               assumed: bool, sunset: datetime, now: datetime) -> list[str]:
    """Fill the after-dark fields and return their key points."""
    result["help_note"] = DISCREET_LINE
    if not return_to:
        return [f"Dark after {hhmm(sunset)}: plan your way back"]
    home = way_home(end_point, to_text, return_to, return_at, now)
    if home["crime_note"]:
        result["crime_note"] = home["crime_note"]
    if not home["route"]:
        return [f"Dark after {hhmm(sunset)}: plan your way back"]
    result["return_route"] = return_lines(home["route"], return_to, assumed)
    journey = home["route"]["journey"]
    return [f"Home: leave {hhmm(journey['start'])}, {vehicle_label(ride_legs(journey)[0])}"
            if ride_legs(journey) else f"Home: walk, leave {hhmm(journey['start'])}"]
