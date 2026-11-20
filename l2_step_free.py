"""Level 2: step-free access and live lift faults for one London station."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import l1_tfl
from l1_http import NeedsInput, refuse

STATION_SUFFIX = re.compile(r"\s+(Underground|Rail|DLR|Overground|Elizabeth line)?\s*Station$", re.IGNORECASE)
YES = "yes"


# --- Small helpers ---
def _present(value) -> str:
    """Treat None and blank text as absent."""
    return str(value).strip() if value is not None else ""


def _short_name(name: str) -> str:
    return STATION_SUFFIX.sub("", name)


def as_of(now: datetime) -> str:
    return now.astimezone(l1_tfl.LONDON).isoformat(timespec="minutes")


# --- Step-free verdict ---
def _lift_count(facilities: dict) -> int:
    lifts = facilities.get("Lifts", "")
    return int(lifts) if lifts.isdigit() else 0


def _step_free(facilities: dict, faults: list[str]) -> str:
    """yes/no/partial/unknown from TfL's lift facts; a lift fault caps it at partial."""
    access = facilities.get("AccessViaLift", "").lower()
    if access == YES or _lift_count(facilities) > 0:
        verdict = "yes"
    elif access == "no":
        verdict = "no"
    else:
        verdict = "unknown"
    return "partial" if verdict == "yes" and faults else verdict


def _facts(facilities: dict, lifts: str, faults: list[str]) -> list[str]:
    facts = []
    if lifts:
        facts.append(f"TfL lists {lifts} at this station.")
    if facilities.get("LimitedCapacityLift", "").lower() == YES:
        facts.append("The lift has limited capacity.")
    if facilities.get("SpecificEntranceRequired", "").lower() == YES:
        facts.append("A specific entrance is needed for step-free access.")
    facts.append(f"{len(faults)} lift fault(s) reported right now." if faults else "No lift faults reported right now.")
    return facts


# --- Station access ---
def station_access(station_name: str) -> dict:
    """Lifts, step-free verdict, lift faults and help points for one station."""
    station = l1_tfl.find_station(station_name)
    with ThreadPoolExecutor(max_workers=2) as pool:
        facilities_job = pool.submit(l1_tfl.station_facilities, station["id"])
        faults_job = pool.submit(l1_tfl.lift_faults)
        details, all_faults = facilities_job.result(), faults_job.result()
    ids = set(details["station_ids"])
    faults = list(dict.fromkeys(fault["message"] for fault in all_faults if fault["station_id"] in ids))
    facilities = details["facilities"]
    count = _lift_count(facilities)
    lifts = f"{count} lift{'s' if count != 1 else ''}" if count else ""
    return {
        "name": _short_name(details["name"]),
        "lifts": lifts,
        "step_free": _step_free(facilities, faults),
        "faults": faults,
        "help_points": facilities.get("Help Points", ""),
        "facts": _facts(facilities, lifts, faults),
    }


# --- Headline wording ---
def _headline(access: dict) -> str:
    name, state, faults = access["name"], access["step_free"], access["faults"]
    if state == "unknown":
        return f"TfL does not say whether {name} has step-free access, so check before you travel."
    if state == "no":
        return f"TfL lists no step-free access via lift at {name}."
    if faults:
        return f"{name} has step-free access via lift, but a lift is out of service: {faults[0]}"
    return f"{name} has step-free access via lift, and no lift faults are reported right now."


# --- Entry: step_free_check ---
def step_free_check(inputs: dict, now: datetime) -> dict:
    """Step-free verdict and advice for the station in inputs['place']."""
    place = _present((inputs or {}).get("place"))
    if not place:
        raise refuse(NeedsInput, "Which station? Tell me its name and I will check its lifts and step-free access.", "no station")
    access = station_access(place)
    facts = list(access["facts"])
    if access["faults"] or access["step_free"] == "unknown":
        facts.append("Check TfL Go or ask station staff before you set out.")
        facts.append("Plan a step-free route with the TfL journey planner.")
    return {
        "headline": _headline(access),
        "facts": facts[:6],
        "source": l1_tfl.SOURCE,
        "as_of": as_of(now),
        "place": access["name"],
    }
