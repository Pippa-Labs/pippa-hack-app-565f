"""Level 1: turn what a person typed into a London latitude and longitude."""

from __future__ import annotations

import math
import re
from urllib.parse import quote, urlencode

import l1_http
from l1_http import NeedsInput, NotFound, refuse

BASE = "https://api.postcodes.io"
SERVICE = "postcodes.io"
LATLON = re.compile(r"^(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)$")
POSTCODE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$", re.IGNORECASE)
OUTCODE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?$", re.IGNORECASE)
EARTH_RADIUS_KM = 6371.0088
NOMINATIM = "https://nominatim.openstreetmap.org/search"
GREATER_LONDON_BOX = "-0.52,51.70,0.34,51.28"  # west,north,east,south


# --- Distance ---
def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres (haversine)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    half_dphi = (phi2 - phi1) / 2
    half_dlam = math.radians(lon2 - lon1) / 2
    a = math.sin(half_dphi) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(half_dlam) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


# --- Lookups per kind of text ---
def _not_found(text: str, internal: str):
    return refuse(NotFound, f"I could not find {text} in London.", internal)


def _postcode_point(text: str, path: str, label_key: str) -> dict:
    """Look up a postcode or outcode path and return its point."""
    try:
        data = l1_http.get_json(f"{BASE}/{path}", SERVICE)
    except NotFound:
        raise _not_found(text, f"{SERVICE} has no {path!r}") from None
    result = data.get("result") or {}
    if result.get("latitude") is None or result.get("longitude") is None:
        raise _not_found(text, f"{SERVICE} has no point for {path!r}")
    return {"lat": result["latitude"], "lon": result["longitude"], "label": result[label_key]}


def _in_london(place: dict) -> bool:
    return place.get("county_unitary") == "Greater London" or place.get("region") == "London"


def _place_point(text: str) -> dict:
    """Find a named place, preferring one inside Greater London."""
    url = f"{BASE}/places?" + urlencode({"q": text, "limit": 5})
    places = (l1_http.get_json(url, SERVICE).get("result") or [])
    london = [place for place in places if _in_london(place) and place.get("latitude") is not None]
    if not london:
        return _landmark_point(text)
    best = london[0]
    return {"lat": best["latitude"], "lon": best["longitude"], "label": best["name_1"]}


def _landmark_point(text: str) -> dict:
    """Landmarks postcodes.io does not know (Trafalgar Square, Canary Wharf), via OpenStreetMap."""
    url = f"{NOMINATIM}?" + urlencode({
        "q": text, "format": "jsonv2", "limit": 5, "countrycodes": "gb",
        "viewbox": GREATER_LONDON_BOX, "bounded": 1,
    })
    hits = l1_http.get_json(url, "OpenStreetMap Nominatim")
    london = [hit for hit in hits if "Greater London" in hit.get("display_name", "")] if isinstance(hits, list) else []
    if not london:
        raise _not_found(text, f"no London place or landmark for {text!r}")
    best = london[0]
    return {"lat": float(best["lat"]), "lon": float(best["lon"]), "label": best.get("name") or text}


# --- Resolve ---
def resolve(text: str) -> dict:
    """Resolve 'lat,lon', a postcode, an outward code or a place name to {lat, lon, label}."""
    text = (text or "").strip()
    if not text:
        raise refuse(NeedsInput, "Tell me where, for example a place name or a postcode.", "empty location")
    pair = LATLON.match(text)
    if pair:
        lat, lon = float(pair.group(1)), float(pair.group(2))
        return {"lat": lat, "lon": lon, "label": f"{lat:.4f},{lon:.4f}"}
    compact = text.replace(" ", "")
    if POSTCODE.match(text):
        return _postcode_point(text, f"postcodes/{quote(compact, safe='')}", "postcode")
    if OUTCODE.match(compact):
        return _postcode_point(text, f"outcodes/{quote(compact, safe='')}", "outcode")
    return _place_point(text)
