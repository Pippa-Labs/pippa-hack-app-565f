"""Level 2: recorded street crime near London places."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import l1_geo
import l1_police
from l1_http import NeedsInput, refuse

SOURCE = "data.police.uk"
NOT_A_RATING = "Recorded incidents, not a safety rating"
TOP_CATEGORIES = 2


# --- Wording ---
def month_name(month: str) -> str:
    """'2026-09' as 'September 2026'."""
    return datetime.strptime(month, "%Y-%m").strftime("%B %Y")


# --- Counts for one or more places (one month lookup shared by all) ---
def _locate(place) -> dict:
    """A resolved point stays as it is; a place name is resolved."""
    return place if isinstance(place, dict) else l1_geo.resolve(str(place))


def recorded_near(places: list) -> dict:
    """Latest month and per-place counts: {"month", "places": [{"label", "total", "by_category"}]}."""
    with ThreadPoolExecutor(max_workers=len(places) + 1) as pool:
        month_job = pool.submit(l1_police.crime_last_updated)
        point_jobs = [pool.submit(_locate, place) for place in places]
        month, points = month_job.result(), [job.result() for job in point_jobs]
        count_jobs = [pool.submit(l1_police.street_crime_counts, p["lat"], p["lon"], month) for p in points]
        counts = [job.result() for job in count_jobs]
    return {"month": month, "places": [{"label": point["label"], **count} for point, count in zip(points, counts)]}


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
    found = recorded_near([str(place)])
    near = found["places"][0]
    count = f"{near['total']:,} crime{'' if near['total'] == 1 else 's'}" if near["total"] else "No crimes"
    top = [f"{name}: {number:,}" for name, number in near["by_category"][:TOP_CATEGORIES]]
    return {
        "headline": f"{count} recorded near {near['label']} in {month_name(found['month'])}; not a safety rating.",
        "key_points": [*top, NOT_A_RATING],
        "source": SOURCE,
        "data_month": found["month"],
        "place": near["label"],
    }
