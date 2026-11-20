"""Level 2: discreet ways to get help, with optional local police and station details."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime

import l1_geo
import l1_police
import l2_step_free
from pippa_agentapp_sdk import AgentAppError

HEADLINE = "In danger, call 999; press 55 if you can't speak."
FIXED_POINTS = ("On a train, text BTP on 61016", 'In a bar, ask staff for "Angela"')
NON_EMERGENCY = "Non-emergency: call 101"
DISCREET_LINE = "In danger call 999 (55 if you can't speak); text BTP 61016; ask for Angela"
POLICE_SOURCE = "data.police.uk"
FALLBACK_SOURCE = "UK emergency services"
LOCAL_BUDGET_SECONDS = 12


# --- Optional lookups (any failure just means the point is left out) ---
def _station_point(place: str) -> str | None:
    try:
        access = l2_step_free.station_access(place)
    except AgentAppError:
        return None
    count = sum(int(n) for n in re.findall(r"\d+", access["help_points"]))  # "12 on platforms, 0 in ticket halls"
    return f"{access['name']}: {count} help points" if count else None


def _police_point(place: str) -> str | None:
    try:
        point = l1_geo.resolve(place)
        located = l1_police.locate_neighbourhood(point["lat"], point["lon"])
        team = l1_police.neighbourhood_team(located["force"], located["neighbourhood"])
    except AgentAppError:
        return None
    return f"Police: {team['name']}"


def _local_points(place: str) -> list[str]:
    """Station and police points that arrive within the budget; slow ones are dropped."""
    pool = ThreadPoolExecutor(max_workers=2)
    jobs = [pool.submit(_station_point, place), pool.submit(_police_point, place)]
    wait(jobs, timeout=LOCAL_BUDGET_SECONDS)
    pool.shutdown(wait=False, cancel_futures=True)  # help must answer even if a service hangs
    return [point for point in map(_settled, jobs) if point]


def _settled(job) -> str | None:
    """A finished job's point, or None if it is still running, was cancelled or failed."""
    if not job.done() or job.cancelled() or job.exception() is not None:
        return None
    return job.result()


# --- Entry: get_help ---
def get_help(inputs: dict, now: datetime) -> dict:
    """999 first, then BTP and Angela, then the nearest local help (or 101)."""
    place = str((inputs or {}).get("place") or "").strip()
    local = _local_points(place) if place else []
    result = {
        "headline": HEADLINE,
        "key_points": [*FIXED_POINTS, (local or [NON_EMERGENCY])[0]],
        "source": POLICE_SOURCE if local else FALLBACK_SOURCE,
    }
    if place:
        result["place"] = place
    return result
