"""Level 2: discreet ways to get help, with the local police team when a place is given."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime

import l1_geo
import l1_police
from pippa_agentapp_sdk import AgentAppError

HEADLINE = "In danger, call 999; press 55 if you can't speak."
FIXED_POINTS = ("On a train, text BTP on 61016", 'In a bar, ask staff for "Angela"')
NON_EMERGENCY = "Non-emergency police: 101"
MAX_TEAM_WORDS = 4  # "Police: <team>, call 101" must stay under 8 words
TEAM_SUFFIXES = (" Safer Neighbourhood Team", " Neighbourhood Team", " Team")
DISCREET_LINE = "In danger call 999 (55 if you can't speak); text BTP 61016; ask for Angela"
POLICE_SOURCE = "data.police.uk"
FALLBACK_SOURCE = "UK emergency services"
LOCAL_BUDGET_SECONDS = 12


# --- Optional police lookup (any failure falls back to plain 101) ---
def _police_point(place: str) -> str | None:
    try:
        point = l1_geo.resolve(place)
        located = l1_police.locate_neighbourhood(point["lat"], point["lon"])
        team = l1_police.neighbourhood_team(located["force"], located["neighbourhood"])
    except AgentAppError:
        return None
    short = _short_team(team["name"])
    return f"Police: {short}, call 101" if short else None


def _short_team(name: str) -> str:
    """The team name without generic suffixes once it is too long; '' if it still will not fit."""
    name = " ".join(name.split())
    for suffix in TEAM_SUFFIXES:
        if len(name.split()) <= MAX_TEAM_WORDS:
            return name
        name = name.removesuffix(suffix)
    return name if len(name.split()) <= MAX_TEAM_WORDS else ""  # a cut-off name would mislead


def _police_within_budget(place: str) -> str | None:
    """The local team point if it arrives within the budget; a slow or failed lookup gives None."""
    pool = ThreadPoolExecutor(max_workers=1)
    job = pool.submit(_police_point, place)
    wait([job], timeout=LOCAL_BUDGET_SECONDS)
    pool.shutdown(wait=False, cancel_futures=True)  # help must answer even if a service hangs
    if not job.done() or job.cancelled() or job.exception() is not None:
        return None
    return job.result()


# --- Entry: get_help ---
def get_help(inputs: dict, now: datetime) -> dict:
    """999 first, then BTP and Angela, then the local police team with 101 (or plain 101)."""
    place = str((inputs or {}).get("place") or "").strip()
    police = _police_within_budget(place) if place else None
    result = {
        "headline": HEADLINE,
        "key_points": [*FIXED_POINTS, police or NON_EMERGENCY],
        "source": POLICE_SOURCE if police else FALLBACK_SOURCE,
    }
    if place:
        result["place"] = place
    return result
