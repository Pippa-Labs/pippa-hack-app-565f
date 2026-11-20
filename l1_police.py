"""Street-level crime counts and neighbourhood team from data.police.uk (no key)."""

from __future__ import annotations

from collections import Counter
from urllib.parse import quote, urlencode

import l1_http

ATTRIBUTION = (
    "Contains public sector information licensed under the Open Government "
    "Licence v3.0 (data.police.uk)"
)
BASE = "https://data.police.uk/api"
SERVICE = "data.police.uk"
CRIME_TIMEOUT = 10.0  # the all-crime answer is ~1.6 MB in central London
CITY_OF_LONDON = "city-of-london"  # separate force from the Met

# --- Category slugs from /api/crime-categories ---
CATEGORY_LABELS = {
    "anti-social-behaviour": "Anti-social behaviour",
    "bicycle-theft": "Bicycle theft",
    "burglary": "Burglary",
    "criminal-damage-arson": "Criminal damage and arson",
    "drugs": "Drugs",
    "other-theft": "Other theft",
    "possession-of-weapons": "Possession of weapons",
    "public-order": "Public order",
    "robbery": "Robbery",
    "shoplifting": "Shoplifting",
    "theft-from-the-person": "Theft from the person",
    "vehicle-crime": "Vehicle crime",
    "violent-crime": "Violence and sexual offences",
    "other-crime": "Other crime",
}


# --- Neighbourhood lookup ---
def locate_neighbourhood(lat: float, lon: float) -> dict:
    """Return {"force", "neighbourhood"} ids for a point."""
    data = l1_http.get_json(f"{BASE}/locate-neighbourhood?" + urlencode({"q": f"{lat},{lon}"}), SERVICE)
    if not isinstance(data, dict) or not data.get("force") or not data.get("neighbourhood"):
        raise l1_http.refuse(l1_http.NotFound, "I could not find a police neighbourhood for that spot.", "empty locate-neighbourhood")
    return {"force": data["force"], "neighbourhood": data["neighbourhood"]}


def neighbourhood_team(force: str, neighbourhood: str) -> dict:
    """Return {"name", "url", "phone"?, "twitter"?}; emails are never kept."""
    url = f"{BASE}/{quote(force, safe='')}/{quote(neighbourhood, safe='')}"
    data = l1_http.get_json(url, SERVICE)
    if not isinstance(data, dict) or not data.get("name"):
        raise l1_http.refuse(l1_http.NotFound, "I could not find that neighbourhood police team.", f"no team for {force}/{neighbourhood}")
    contact = data.get("contact_details") or {}
    team = {"name": data["name"], "url": data.get("url_force") or ""}
    phone = contact.get("telephone")
    if phone:
        team["phone"] = phone
    twitter = contact.get("twitter")
    if twitter and twitter != "undefined":
        team["twitter"] = twitter
    return team


# --- Crime data month ---
def crime_last_updated() -> str:
    """Latest month with street crime data, as YYYY-MM."""
    data = l1_http.get_json(f"{BASE}/crime-last-updated", SERVICE)
    date = data.get("date", "") if isinstance(data, dict) else ""
    if len(date) < 7:
        raise l1_http.refuse(l1_http.NotFound, "I could not tell which month the crime data covers.", f"bad crime-last-updated {data!r}")
    return date[:7]


# --- Street crime counts ---
def street_crime_counts(lat: float, lon: float, month: str) -> dict:
    """Aggregate recorded street crimes near a point: {"month", "total", "by_category"}."""
    query = urlencode({"lat": lat, "lng": lon, "date": month})
    items = l1_http.get_json(f"{BASE}/crimes-street/all-crime?{query}", SERVICE, timeout=CRIME_TIMEOUT)
    if not isinstance(items, list):
        raise l1_http.refuse(l1_http.NotFound, "I could not read crime figures for that spot.", "crimes-street not a list")
    counts = Counter(item.get("category", "other-crime") for item in items)
    by_category = sorted(
        ((CATEGORY_LABELS.get(slug, slug.replace("-", " ").capitalize()), n) for slug, n in counts.items()),
        key=lambda pair: (-pair[1], pair[0]),
    )
    return {"month": month, "total": len(items), "by_category": by_category}

