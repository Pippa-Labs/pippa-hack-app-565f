"""London Companion: validates the manifest and dispatches each outcome to its module."""

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

import l2_live
import l2_walk_now

MANIFEST_PATH = Path(__file__).with_name("manifest.yaml")

# --- Outcome -> sub-task -> entry function (each takes inputs and now) ---
ROUTES = {
    "transport.live": {
        "status": l2_live.line_status,
    },
    "outdoors.walk": {
        "weather": l2_walk_now.weather_watch,
        "walk": l2_walk_now.walk_now,
    },
}

BASE_FIELDS = ("headline", "facts", "source", "as_of")


class UnknownOutcome(AgentAppError):
    """Pippa asked for an outcome this app does not declare."""

    user_message = "London Companion cannot answer that kind of question."
    retryable = False


# --- Sub-task choice when the extractor left `task` empty ---
def _default_task(outcome_id: str, inputs: dict) -> str:
    """Infer the sub-task from which inputs are present."""
    has = lambda name: inputs.get(name) not in (None, "")  # noqa: E731
    if outcome_id == "transportation_fares.cheapest":
        if has("airport"):
            return "airport"
        return "single_fare" if has("from_place") and has("to_place") else "best_ticket"
    if outcome_id == "journey.plan":
        return "leave_by" if has("arrive_by") else "route"
    if outcome_id == "transport.live":
        return "departures" if has("place") and not has("line") else "status"
    if outcome_id == "outdoors.walk":
        return "walk" if has("minutes") else "weather"
    return "help"


class App:
    """London Companion: fares, journeys, live transport, walking weather and help."""

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
        task = (inputs.get("task") or "").strip().lower()
        if task not in tasks:
            task = _default_task(call.outcome_id, inputs)
        if task not in tasks:
            task = next(iter(tasks))
        answer = tasks[task](inputs, call.context.now)
        allowed = self._fields[call.outcome_id]
        data = {"task": task, **{k: v for k, v in answer.items() if k in allowed and v is not None}}
        missing = [name for name in BASE_FIELDS if name not in data]
        if missing:
            raise AgentAppError(f"{call.outcome_id}/{task} returned no {missing}")
        return Result(data=data, narrative=answer["headline"])
