"""Level 1: read a clock time the way people type it ("6pm", "half six", "1830") as London time."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from l1_http import NeedsInput, refuse

LONDON = ZoneInfo("Europe/London")
STALE_AFTER = timedelta(hours=1)  # up to an hour ago still means "just now", not tomorrow
UNREADABLE = "I could not read that time. Try something like 18:30 or 6pm."
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
NAMED = {"noon": (12, 0), "midday": (12, 0), "midnight": (0, 0)}
HOUR = r"(\d{1,2}|" + "|".join(NUMBER_WORDS) + r")"
MERIDIEM = r"\s*(am|pm|a\.m\.|p\.m\.)?"
DIGITS = re.compile(r"^(\d{1,2})(?:[:.](\d{2}))?" + MERIDIEM + "$")
COMPACT = re.compile(r"^(\d{1,2})(\d{2})" + MERIDIEM + "$")
PHRASE = re.compile(r"^(half past|half|quarter past|quarter to)\s+" + HOUR + MERIDIEM + "$")
PHRASE_MINUTES = {"half past": 30, "half": 30, "quarter past": 15, "quarter to": -15}


# --- Reading the words ---
def _hour_value(text: str) -> int:
    return NUMBER_WORDS[text] if text in NUMBER_WORDS else int(text)


def _read(text: str) -> tuple[int, int, str | None]:
    """(hour, minute, 'am'/'pm'/None) from the typed text, or ValueError."""
    text = " ".join(text.lower().replace("o'clock", "").split()).removeprefix("at ").strip()
    if text in NAMED:
        return (*NAMED[text], "fixed")
    for pattern in (DIGITS, COMPACT):
        match = pattern.match(text)
        if match:
            return int(match.group(1)), int(match.group(2) or 0), _meridiem(match.group(3))
    match = PHRASE.match(text)
    if not match:
        raise ValueError(text)
    hour, offset = _hour_value(match.group(2)), PHRASE_MINUTES[match.group(1)]
    if offset < 0:
        hour, offset = (hour - 1) % 12 or 12, 60 + offset
    return hour, offset, _meridiem(match.group(3))


def _meridiem(text: str | None) -> str | None:
    return text.replace(".", "") if text else None


def _hours_meant(hour: int, minute: int, meridiem: str | None, evening: bool) -> list[int]:
    """Every 24-hour reading of the hour; a bare 1-11 could be morning or evening."""
    if not 0 <= minute <= 59:
        raise ValueError(minute)
    if meridiem == "fixed":
        return [hour]
    if meridiem:
        if not 1 <= hour <= 12:
            raise ValueError(hour)
        return [hour % 12 + (12 if meridiem == "pm" else 0)]
    if not 0 <= hour <= 23:
        raise ValueError(hour)
    if 1 <= hour <= 11:
        return [hour + 12] if evening else [hour, hour + 12]
    return [hour]


# --- Next occurrence ---
def _next_at(local_now: datetime, hour: int, minute: int) -> datetime:
    moment = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return moment + timedelta(days=1) if local_now - moment > STALE_AFTER else moment


def parse_clock(text, now: datetime, *, evening: bool = False) -> datetime:
    """The next London time at or after now (or within the last hour) that the text names."""
    try:
        hour, minute, meridiem = _read(str(text or ""))
        hours = _hours_meant(hour, minute, meridiem, evening)
    except ValueError:
        raise refuse(NeedsInput, UNREADABLE, f"unreadable clock time {text!r}") from None
    local_now = now.astimezone(LONDON)
    return min(_next_at(local_now, h, minute) for h in hours)
