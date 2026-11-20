"""Level 3: a late-night route using night buses, the Night Tube and other night-time services."""

from __future__ import annotations

from datetime import datetime

from l2_best_route import SOURCE, hhmm, night_departure, night_route, place_names, route_words, times_point

MAX_POINTS = 3


# --- Entry ---
def night_journey(inputs: dict, now: datetime) -> dict:
    """A late-night route between two places."""
    start, end = place_names(inputs)
    leave, assumed = night_departure(inputs, now)
    route = night_route(start, end, leave, now)
    journey = route["journey"]
    points = [times_point(journey), *route["notes"]]
    if assumed:
        points.insert(1, "Assumes leaving at 23:30")
    return {
        "headline": f"{route_words(journey)}, {journey['duration_minutes']} min, leave {hhmm(journey['start'])}.",
        "key_points": points[:MAX_POINTS],
        "source": SOURCE,
        **route["extras"],
    }
