"""Level 2: live TfL lookups shaped into answers (line status, departures, bike docks)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import l1_tfl

LONDON = ZoneInfo("Europe/London")
GOOD_SERVICE = 10
MAX_FACTS = 6


def _text(inputs: dict, name: str) -> str:
    """A string input, with None and blanks treated as absent."""
    return (inputs.get(name) or "").strip()


def _as_of(now: datetime) -> str:
    """The read time as ISO London time, to the minute."""
    return now.astimezone(LONDON).replace(second=0, microsecond=0).isoformat()


# --- Line status ---
def _reason(line: dict) -> str:
    """The reason without TfL's repeated "<Line> Line: " prefix."""
    return line["reason"].removeprefix(f"{line['line']} Line: ").strip()


def line_status(inputs: dict, now: datetime) -> dict:
    """Current status of every line, or of the one line named."""
    lines = l1_tfl.line_statuses()
    wanted = _text(inputs, "line").lower().removesuffix(" line")
    if wanted:
        lines = [line for line in lines if wanted in line["line"].lower()] or lines
    problems = [line for line in lines if line["severity"] != GOOD_SERVICE]
    if wanted and len(lines) == 1:
        line = lines[0]
        headline = f"The {line['line']} line has {line['status'].lower()}."
        facts = [_reason(line)] if line["reason"] else [f"No problems reported on the {line['line']} line."]
    elif problems:
        headline = f"{len(problems)} of {len(lines)} lines have problems right now; the rest have good service."
        facts = [f"{line['line']}: {line['status']}. {_reason(line)}".strip() for line in problems]
    else:
        headline = "Good service on every Tube, Overground, Elizabeth line and DLR line."
        facts = [f"{len(lines)} lines checked, none with delays."]
    return {
        "headline": headline,
        "facts": facts[:MAX_FACTS],
        "source": l1_tfl.SOURCE,
        "as_of": _as_of(now),
    }
