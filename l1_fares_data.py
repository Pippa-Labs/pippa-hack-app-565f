"""London adult fares for 2026 as static constants (money in integer pence)."""

from __future__ import annotations

FARES_AS_OF = "2026-11"
SOURCE = "Fares: GLA decision MD3464 / TfL (caps frozen until March 2027)"

# --- Travelcard zones and PAYG caps (7-day cap equals the 7-day Travelcard price) ---
ZONES = ("1-2", "1-3", "1-4", "1-6")
DAILY_CAP_PENCE = {"1-2": 890, "1-3": 1050, "1-4": 1280, "1-6": 1630}
WEEKLY_CAP_PENCE = {"1-2": 4470, "1-3": 5250, "1-4": 6420, "1-6": 8160}

# Monthly Travelcard is not published here: derived as 7-day price x 3.84.
MONTHLY_FACTOR = 3.84

# --- Bus and tram ---
BUS_SINGLE_PENCE = 185
BUS_DAILY_CAP_PENCE = 555
BUS_WEEKLY_PASS_PENCE = 2610

# --- Cards and discounts ---
STUDENT_TRAVELCARD_DISCOUNT = 0.30  # Travelcards only; PAYG caps are not discounted
VISITOR_OYSTER_FEE_PENCE = 1050  # fee is not refundable, unused credit is
STANDARD_OYSTER_FEE_PENCE = 700

# --- Airport options (price_pence None = no verified figure) ---
AIRPORTS = {
    "heathrow": [
        {"name": "Elizabeth line from zone 1", "price_pence": 1550, "capped": False},
        {"name": "Piccadilly line (zone 1 to 6 pay as you go)", "price_pence": None, "capped": True},  # unverified
        {"name": "Heathrow Express", "price_pence": None, "capped": False},  # unverified: outside caps
    ],
    "gatwick": [
        {"name": "National Rail (Thameslink, Southern or Gatwick Express)", "price_pence": None, "capped": False},  # unverified: outside caps
    ],
    "stansted": [
        {"name": "Stansted Express or National Rail", "price_pence": None, "capped": False},  # unverified: outside caps
    ],
    "luton": [
        {"name": "Thameslink to Luton Airport Parkway, then shuttle bus", "price_pence": None, "capped": False},  # unverified: outside caps
    ],
    "city": [
        {"name": "DLR to London City Airport (zone 3)", "price_pence": None, "capped": True},
    ],
}


# --- Lookups ---
def _check_zones(zones: str) -> str:
    """Return zones if known, else raise ValueError."""
    if zones not in ZONES:
        raise ValueError(f"unknown zones {zones!r}; expected one of {ZONES}")
    return zones


def daily_cap(zones: str) -> int:
    """Daily PAYG cap in pence for the zone range."""
    return DAILY_CAP_PENCE[_check_zones(zones)]


def weekly_cap(zones: str) -> int:
    """7-day cap (= 7-day Travelcard) in pence for the zone range."""
    return WEEKLY_CAP_PENCE[_check_zones(zones)]


def monthly_travelcard(zones: str) -> int:
    """Derived monthly Travelcard in pence (7-day price x 3.84, not an official figure)."""
    return round(weekly_cap(zones) * MONTHLY_FACTOR)


def student_price(pence: int) -> int:
    """A Travelcard price with the 18+ Student Oyster discount applied."""
    return round(pence * (1 - STUDENT_TRAVELCARD_DISCOUNT))
