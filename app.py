"""Pocket Londoner: validates the manifest and dispatches each outcome to its module."""

from __future__ import annotations

from pathlib import Path

import yaml
from pippa_agentapp_sdk import (
    AgentAppError,
    Call,
    Manifest,
    Result,
    load_manifest_file,
    validate_manifest,
)

import l2_best_route
import l2_crime_watch
import l2_fare_advisor
import l2_get_help
import l2_going_out
import l2_live
import l2_step_free
import l2_walk_now
import l3_leave_by
import l3_night_journey

MANIFEST_PATH = Path(__file__).with_name("manifest.yaml")

# --- Outcome -> sub-task -> entry function (each takes inputs and now) ---
ROUTES = {
    "transportation.fares": {
        "best_ticket": l2_fare_advisor.best_ticket,
        "single_fare": l2_fare_advisor.single_fare,
        "airport": l2_fare_advisor.airport_fare,
    },
    "journey.plan": {
        "route": l2_best_route.best_route,
        "leave_by": l3_leave_by.leave_by,
        "night": l3_night_journey.night_journey,
    },
    "transport.live": {
        "status": l2_live.line_status,
        "departures": l2_live.next_departures,
        "bikes": l2_live.bike_docks,
        "step_free": l2_step_free.step_free_check,
    },
    "outdoors.walk": {
        "weather": l2_walk_now.weather_watch,
        "walk": l2_walk_now.walk_now,
        "going_out": l2_going_out.going_out,
    },
    "safety.help": {
        "help": l2_get_help.get_help,
        "crime": l2_crime_watch.crime_watch,
    },
}

BASE_FIELDS = ("headline", "key_points", "source")


class UnreadableData(AgentAppError):
    """An upstream service answered in a shape the app could not read."""

    user_message = "A live data service sent something I could not read. Please try again later."
    retryable = False


class UnknownOutcome(AgentAppError):
    """Pippa asked for an outcome this app does not declare."""

    user_message = "Pocket Londoner cannot answer that kind of question."
    retryable = False


# --- Sub-task choice when the extractor left `task` empty ---
def _is_late(clock: str) -> bool:
    """True for an HH:MM between 23:00 and 04:59."""
    hour = clock.split(":")[0].strip()
    return hour.isdigit() and (int(hour) >= 23 or int(hour) < 5)


def _default_task(outcome_id: str, inputs: dict) -> str:
    """Infer the sub-task from which inputs are present."""
    has = lambda name: inputs.get(name) not in (None, "")  # noqa: E731
    if outcome_id == "transportation.fares":
        if has("airport"):
            return "airport"
        return "single_fare" if has("from_place") and has("to_place") else "best_ticket"
    if outcome_id == "journey.plan":
        if has("arrive_by"):
            return "leave_by"
        return "night" if _is_late(str(inputs.get("depart_at") or "")) else "route"
    if outcome_id == "transport.live":
        return "departures" if has("place") and not has("line") else "status"
    if outcome_id == "outdoors.walk":
        if inputs.get("tonight") is True or any(has(name) for name in ("from_time", "until_time", "cold_below_c")):
            return "going_out"
        return "walk" if has("minutes") else "weather"
    return "help"


class App:
    """Pocket Londoner: fares, journeys, live transport, walking weather and help."""

    def __init__(self) -> None:
        self._manifest = validate_manifest(load_manifest_file(MANIFEST_PATH))
        declared = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
        self._fields = {
            outcome["id"]: {field["name"] for field in outcome["output"]}
            for outcome in declared["outcomes"]
        }

    @property
    def manifest(self) -> Manifest:
        return self._manifest

    def handle(self, call: Call) -> Result:
        """Pick the sub-task, run it, and keep only the declared output fields."""
        tasks = ROUTES.get(call.outcome_id)
        if tasks is None:
            raise UnknownOutcome(f"undeclared outcome {call.outcome_id!r}")
        inputs = dict(call.inputs)
        task = str(inputs.get("task") or "").strip().lower()
        if task not in tasks:
            task = _default_task(call.outcome_id, inputs)
        if task not in tasks:
            task = next(iter(tasks))
        try:
            answer = tasks[task](inputs, call.context.now)
        except AgentAppError:
            raise
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, OverflowError) as exc:
            raise UnreadableData(f"{call.outcome_id}/{task}: {type(exc).__name__}: {exc}") from None
        allowed = self._fields[call.outcome_id]
        data = {"task": task, **{k: v for k, v in answer.items() if k in allowed and v is not None}}
        missing = [name for name in BASE_FIELDS if name not in data]
        if missing:
            raise AgentAppError(f"{call.outcome_id}/{task} returned no {missing}")
        return Result(data=data, narrative=data["headline"])
