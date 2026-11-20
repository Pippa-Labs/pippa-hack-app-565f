"""Level 2: recorded street crime near a London place."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import l1_geo
import l1_police
from l1_http import NeedsInput, refuse

TOP_CATEGORIES = 4


# --- Wording ---
def _month_name(month: str) -> str:
    """'2026-09' as 'September 2026'."""
    return datetime.strptime(month, "%Y-%m").strftime("%B %Y")


def _headline(total: int, month_name: str, label: str) -> str:
    """One sentence with the total, the month and the place."""
    if total == 0:
        return f"No crimes were recorded in {month_name} within about a mile of {label}."
    noun = "crime was" if total == 1 else "crimes were"
    return f"In {month_name}, {total:,} {noun} recorded within about a mile of {label}."


# --- Crime watch entry ---
def crime_watch(inputs: dict, now: datetime) -> dict:
    """Count recorded street crimes near a place in the latest published month."""
    place = inputs.get("place")
    if place is None or not str(place).strip():
        raise refuse(
            NeedsInput,
            "Tell me a London postcode, station or area and I will look up recorded crime there.",
            "no place given",
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        located = pool.submit(l1_geo.resolve, str(place))
        latest = pool.submit(l1_police.crime_last_updated)
        point, month = located.result(), latest.result()
    counts = l1_police.street_crime_counts(point["lat"], point["lon"], month)
    month_name = _month_name(counts["month"])
    top = [f"{name}: {count:,}" for name, count in counts["by_category"][:TOP_CATEGORIES]]
    facts = top + [
        f"Data for {month_name}: police.uk publishes about two months behind.",
        "These are recorded incidents, not a safety rating.",
    ]
    return {
        "headline": _headline(counts["total"], month_name, point["label"]),
        "facts": facts,
        "source": l1_police.ATTRIBUTION,
        "as_of": counts["month"],
        "data_month": counts["month"],
    }
