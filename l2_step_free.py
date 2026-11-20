"""Level 2: step-free access and live lift faults for one London station."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import l1_tfl
from l1_http import NeedsInput, refuse

STATION_SUFFIX = re.compile(r"\s+(Underground|Rail|DLR|Overground|Elizabeth line)?\s*Station$", re.IGNORECASE)
YES = "yes"
TFL_ADVICE = "Call TfL 0343 222 1234."


# --- Small helpers ---
def _present(value) -> str:
    """Treat None and blank text as absent."""
    return str(value).strip() if value is not None else ""


def _short_name(name: str) -> str:
    return STATION_SUFFIX.sub("", name)


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


def _points(facilities: dict, lifts: str, faults: list[str]) -> list[str]:
    """Short points: faults first, then lift count and access quirks."""
    points = [f"{len(faults)} lift fault(s) reported now"] if faults else []
    if lifts:
        points.append(f"TfL lists {lifts}")
    if facilities.get("LimitedCapacityLift", "").lower() == YES:
        points.append("Lift has limited capacity")
    if facilities.get("SpecificEntranceRequired", "").lower() == YES:
        points.append("Needs a specific entrance")
    return points


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
        "points": _points(facilities, lifts, faults),
    }


# --- Headline wording ---
def _headline(access: dict) -> str:
    """Plain verdict first, so it reads well aloud: 'Step-free: yes, Oxford Circus.'"""
    name, state = access["name"], access["step_free"]
    if state == "unknown":
        return f"Step-free: not stated for {name}; ask staff."
    if state == "no":
        return f"Step-free: no lift at {name}. {TFL_ADVICE}"
    if state == "partial":
        return f"Step-free: no, lift out at {name}. {TFL_ADVICE}"
    return f"Step-free: yes, {name}. No lift faults reported."


# --- Entry: step_free_check ---
def step_free_check(inputs: dict, now: datetime) -> dict:
    """Step-free verdict for the station in inputs['place']."""
    place = _present((inputs or {}).get("place"))
    if not place:
        raise refuse(NeedsInput, "Which station? Tell me its name and I will check its lifts and step-free access.", "no station")
    access = station_access(place)
    points = list(access["points"])
    if access["faults"] or access["step_free"] == "unknown":
        points.insert(0, "Check TfL Go before you set out")
    return {
        "headline": _headline(access),
        "key_points": points[:3] or ["No lift details listed"],
        "source": l1_tfl.SOURCE,
        "place": access["name"],
    }
