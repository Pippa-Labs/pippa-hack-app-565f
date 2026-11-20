"""Level 2: discreet ways to get help, with optional local police and station details."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime

import l1_geo
import l1_police
import l1_tfl
import l2_step_free
from pippa_agentapp_sdk import AgentAppError

FIXED_OPTIONS = [
    "In an emergency call 999. If you cannot speak, call 999 from a mobile, stay on the line and press 55 when asked (the Silent Solution).",
    "On a train or at a station, text British Transport Police on 61016.",
    'In a bar or venue, ask staff for "Angela" to get discreet help.',
    "For non-emergencies call 101.",
]
POLICE_SOURCE = "data.police.uk"
FALLBACK_SOURCE = "UK emergency services guidance"
MAX_FACTS = 6
LOCAL_BUDGET_SECONDS = 12


# --- Optional lookups (any failure just means the fact is left out) ---
def _police_fact(place: str) -> str | None:
    try:
        point = l1_geo.resolve(place)
        located = l1_police.locate_neighbourhood(point["lat"], point["lon"])
        team = l1_police.neighbourhood_team(located["force"], located["neighbourhood"])
    except AgentAppError:
        return None
    force = "City of London Police" if located["force"] == l1_police.CITY_OF_LONDON else "Metropolitan Police"
    link = f" — {team['url']}" if team["url"] else ""
    return f"Local police team: {team['name']} ({force}){link}"


def _station_fact(place: str) -> str | None:
    try:
        access = l2_step_free.station_access(place)
    except AgentAppError:
        return None
    parts = []
    if access["help_points"]:
        parts.append(f"help points: {access['help_points']}")
    if access["lifts"]:
        parts.append(f"lifts: {access['lifts']}" + (" (fault reported)" if access["faults"] else ""))
    detail = f" — {'; '.join(parts)}" if parts else ""
    return f"Station: {access['name']}{detail}"


def _local_facts(place: str) -> list[str]:
    """Police and station facts that arrive within the budget; slow ones are dropped."""
    pool = ThreadPoolExecutor(max_workers=2)
    jobs = [pool.submit(_police_fact, place), pool.submit(_station_fact, place)]
    wait(jobs, timeout=LOCAL_BUDGET_SECONDS)
    pool.shutdown(wait=False, cancel_futures=True)  # help must answer even if a service hangs
    return [fact for fact in map(_settled, jobs) if fact]


def _settled(job) -> str | None:
    """A finished job's fact, or None if it is still running, was cancelled or failed."""
    if not job.done() or job.cancelled() or job.exception() is not None:
        return None
    return job.result()


# --- Entry: get_help ---
def get_help(inputs: dict, now: datetime) -> dict:
    """Fixed emergency options first, plus local police and station details when a place is given."""
    place = str((inputs or {}).get("place") or "").strip()
    local = _local_facts(place) if place else []
    where = f" near {place}" if place else ""
    sources = [POLICE_SOURCE, l1_tfl.SOURCE] if local else [FALLBACK_SOURCE]
    result = {
        "headline": f"If you are in danger now, call 999; here are discreet ways to get help{where}.",
        "facts": (FIXED_OPTIONS + local)[:MAX_FACTS],
        "source": "; ".join(sources),
        "as_of": l2_step_free.as_of(now),
    }
    if place:
        result["place"] = place
    return result
