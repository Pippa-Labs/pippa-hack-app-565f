"""Level 1: Transport for London open data (anonymous, no key)."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

import l1_http
from l1_http import NotFound, refuse

BASE = "https://api.tfl.gov.uk"
SERVICE = "TfL"
SOURCE = "Powered by TfL Open Data"
LONDON = ZoneInfo("Europe/London")
RAIL_MODES = "tube,overground,elizabeth-line,dlr,national-rail"
BUS_STOP_PREFIX = "490"
MAX_BIKE_POINTS = 5
FACILITY_CATEGORIES = ("Facility", "Accessibility")
SENTENCE_END = re.compile(r"(?<!St)(?<=[.!?])\s+")
PREFERENCES = {"fastest": "LeastTime", "fewest_changes": "LeastInterchange", "least_walking": "LeastWalking"}


def _get(path: str, params: dict | None = None):
    """GET one TfL path with optional query values."""
    query = f"?{urlencode(params)}" if params else ""
    return l1_http.get_json(f"{BASE}{path}{query}", SERVICE)


# --- Line statuses ---
def _first_sentence(text: str) -> str:
    return SENTENCE_END.split(text.strip(), maxsplit=1)[0]


def line_statuses(modes: str = "tube,overground,elizabeth-line,dlr") -> list[dict]:
    """Worst current status of each line as {line, status, severity, reason}."""
    lines = _get(f"/Line/Mode/{quote(modes, safe=',')}/Status")
    result = []
    for line in lines:
        worst = min(line["lineStatuses"], key=lambda status: status["statusSeverity"])  # lowest is worst
        result.append({
            "line": line["name"],
            "status": worst["statusSeverityDescription"],
            "severity": worst["statusSeverity"],
            "reason": _first_sentence(worst.get("reason") or ""),
        })
    return result


# --- Lift faults ---
def lift_faults() -> list[dict]:
    """Every current lift fault as {station_id, message}."""
    return [
        {"station_id": fault["stationUniqueId"], "message": fault["message"]}
        for fault in _get("/Disruptions/Lifts/v2/")
    ]


# --- Station search ---
def find_station(name: str) -> dict:
    """Best station match for a name as {id, name, lat, lon}."""
    found = _get(f"/StopPoint/Search/{quote(name, safe='')}", {"modes": RAIL_MODES})
    for match in found.get("matches") or []:
        if match.get("lat") is not None and match.get("lon") is not None:
            return {"id": match["id"], "name": match["name"], "lat": match["lat"], "lon": match["lon"]}
    raise refuse(NotFound, f"I could not find a station called {name}.", f"no stoppoint for {name!r}")


def _stop_point(stop_id: str, label: str) -> dict:
    """Fetch one stop point, naming it in the error if it does not exist."""
    try:
        return _get(f"/StopPoint/{quote(stop_id, safe='')}")
    except NotFound:
        raise refuse(NotFound, f"I could not find {label}.", f"no stoppoint {stop_id!r}") from None


def _rail_children(stop: dict) -> list[str]:
    """Rail station ids under a stop, tube/dlr style ids (940G) first."""
    ids = [child["id"] for child in stop.get("children") or [] if not child["id"].startswith(BUS_STOP_PREFIX)]
    return sorted(ids, key=lambda stop_id: not stop_id.startswith("940G"))


# --- Station facilities ---
def station_facilities(station_id: str) -> dict:
    """Facilities as {name, station_ids, facilities: {label: plain string}}."""
    stop = _stop_point(station_id, "that station")
    facilities = {
        prop["key"]: str(prop["value"])
        for prop in stop.get("additionalProperties") or []
        if prop["category"] in FACILITY_CATEGORIES
    }
    return {
        "name": stop["commonName"],
        "station_ids": [station_id, *_rail_children(stop)],
        "facilities": facilities,
    }


# --- Bus stops and arrivals ---
def stops_near(lat: float, lon: float, radius: int = 300) -> list[dict]:
    """Bus stops within radius metres, nearest first, as {id, name, indicator, distance_m}."""
    params = {"lat": lat, "lon": lon, "stopTypes": "NaptanPublicBusCoachTram", "radius": radius}
    stops = _get("/StopPoint", params).get("stopPoints") or []
    return [
        {
            "id": stop["naptanId"],
            "name": stop["commonName"],
            "indicator": stop.get("indicator") or "",
            "distance_m": round(stop.get("distance") or 0),
        }
        for stop in sorted(stops, key=lambda stop: stop.get("distance") or 0)
    ]


def arrivals(stop_id: str, limit: int = 5) -> list[dict]:
    """Soonest predicted arrivals as {line, destination, minutes, platform}."""
    rows = _get(f"/StopPoint/{quote(stop_id, safe='')}/Arrivals")
    soonest = sorted(rows, key=lambda row: row["timeToStation"])[:limit]
    return [
        {
            "line": row["lineName"],
            "destination": row.get("destinationName") or row.get("towards") or "",
            "minutes": row["timeToStation"] // 60,
            "platform": row.get("platformName") or "",
        }
        for row in soonest
    ]


# --- Santander Cycles ---
def _count(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _bike_point(point_id: str) -> dict:
    point = _get(f"/BikePoint/{quote(point_id, safe='')}")
    props = {prop["key"]: prop["value"] for prop in point.get("additionalProperties") or []}
    return {
        "name": point["commonName"],
        "bikes": _count(props.get("NbBikes")),  # total, e-bikes included
        "ebikes": _count(props.get("NbEBikes")),
        "empty_docks": _count(props.get("NbEmptyDocks")),
        "lat": point["lat"],
        "lon": point["lon"],
    }


def bike_points(query: str) -> list[dict]:
    """Up to five docks matching a place as {name, bikes, ebikes, empty_docks, lat, lon}."""
    matches = _get("/BikePoint/Search", {"query": query})[:MAX_BIKE_POINTS]
    if not matches:
        raise refuse(NotFound, f"I could not find a bike dock near {query}.", f"no bikepoint for {query!r}")
    with ThreadPoolExecutor(max_workers=MAX_BIKE_POINTS) as pool:
        return list(pool.map(_bike_point, [match["id"] for match in matches]))


# --- Fares ---
def _fare_station_id(stop_id: str) -> str:
    """The station id FareTo accepts: hubs (HUB...) must be swapped for a rail child."""
    if stop_id.startswith(("940G", "910G")):
        return stop_id
    children = _rail_children(_stop_point(stop_id, "that station"))
    return children[0] if children else stop_id


def fare(from_id: str, to_id: str) -> list[dict]:
    """Adult single fares as {ticket, time, cost_gbp}; empty when TfL publishes none."""
    start, end = _fare_station_id(from_id), _fare_station_id(to_id)
    sections = _get(f"/StopPoint/{quote(start, safe='')}/FareTo/{quote(end, safe='')}")
    tickets = {}
    for section in sections:
        for row in section.get("rows") or []:
            for ticket in row.get("ticketsAvailable") or []:
                if ticket["passengerType"] == "Adult":
                    key = (ticket["ticketType"]["type"], ticket["ticketTime"]["type"])
                    tickets.setdefault(key, float(ticket["cost"]))
    return [{"ticket": kind, "time": when, "cost_gbp": cost} for (kind, when), cost in tickets.items()]


# --- Journey planner ---
def _london_time(text: str) -> datetime:
    """TfL sends London wall-clock times with no offset."""
    return datetime.fromisoformat(text).replace(tzinfo=LONDON)


def _leg(raw: dict) -> dict:
    options = [option["name"] for option in raw.get("routeOptions") or [] if option.get("name")]
    return {
        "mode": raw["mode"]["id"],
        "summary": raw["instruction"]["summary"],
        "line": options[0] if options else "",
        "minutes": raw["duration"],
    }


def _journey(raw: dict) -> dict:
    pence = (raw.get("fare") or {}).get("totalCost")
    disruptions = [
        disruption["description"]
        for leg in raw["legs"]
        for disruption in leg.get("disruptions") or []
        if disruption.get("description")
    ]
    return {
        "duration_minutes": raw["duration"],
        "start": _london_time(raw["startDateTime"]),
        "arrival": _london_time(raw["arrivalDateTime"]),
        "fare_gbp": None if pence is None else pence / 100,
        "legs": [_leg(leg) for leg in raw["legs"]],
        "disruptions": list(dict.fromkeys(disruptions)),
    }


def _journey_params(when, arriving, preference, modes, step_free) -> dict:
    params = {}
    if when is not None:
        local = when.astimezone(LONDON)
        params.update(date=local.strftime("%Y%m%d"), time=local.strftime("%H%M"),
                      timeIs="Arriving" if arriving else "Departing")
    if preference in PREFERENCES:
        params["journeyPreference"] = PREFERENCES[preference]
    if modes:
        params["mode"] = modes
    if step_free:
        params["accessibilityPreference"] = "StepFreeToVehicle"
    return params


def journey(frm: str, to: str, *, when: datetime | None = None, arriving: bool = False,
            preference: str = "fastest", modes: str | None = None, step_free: bool = False) -> list[dict]:
    """Journeys between two 'lat,lon' strings as {duration_minutes, start, arrival, fare_gbp, legs, disruptions}."""
    params = _journey_params(when, arriving, preference, modes, step_free)
    path = f"/Journey/JourneyResults/{quote(frm, safe=',')}/to/{quote(to, safe=',')}"
    try:
        data = _get(path, params)
    except NotFound:
        data = {}
    journeys = data.get("journeys") or []
    if not journeys:
        raise refuse(NotFound, "TfL found no route between those two places.", f"no journeys {frm} to {to}")
    return [_journey(raw) for raw in journeys]
