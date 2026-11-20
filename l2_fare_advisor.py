"""Level 2: cheapest way to pay for London travel, single fares and airport trips."""

from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import l1_fares_data as fares
import l1_tfl
from l1_http import NeedsInput, NotFound, refuse
from pippa_agentapp_sdk import AgentAppError

SOURCE = l1_tfl.SOURCE  # TfL requires this exact attribution
TABLE_SOURCE = "TfL fares 2026"
MAX_POINTS = 3
STATION_SUFFIX = re.compile(r"\s+(Underground|Rail|DLR|Overground)?\s*Station$", re.IGNORECASE)
DEFAULT_STAY_DAYS = 7
DAYS_PER_MONTH_CARD = 28  # a monthly Travelcard covers about four weeks
CARDS = ("uk_contactless", "foreign_contactless", "none")
ZONE_CHOICES = "1-2, 1-3, 1-4 or 1-6"
AIRPORT_STATIONS = {"heathrow": "Heathrow Terminals 2 & 3", "city": "London City Airport"}
AIRPORT_NAMES = {"heathrow": "Heathrow", "gatwick": "Gatwick", "stansted": "Stansted", "luton": "Luton", "city": "London City"}
AIRPORT_ALIASES = {
    "heathrow": "heathrow", "lhr": "heathrow",
    "gatwick": "gatwick", "lgw": "gatwick",
    "stansted": "stansted", "stn": "stansted",
    "luton": "luton", "ltn": "luton",
    "city": "city", "london city": "city", "lcy": "city", "city airport": "city", "london city airport": "city",
}


# --- Input cleaning ---
def _present(value) -> bool:
    return value is not None and str(value).strip() != ""


def _whole_number(value, default: int) -> int:
    """An int from a model-extracted value, or the default when absent or unreadable."""
    if not _present(value):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("true", "yes", "1") if _present(value) else False


def _parse_zones(value) -> str:
    """Normalise '1 to 3', 'zone 1-3', '2' into a known zone range or refuse."""
    if not _present(value):
        return "1-2"
    numbers = re.findall(r"\d", str(value))
    zones = None
    if len(numbers) == 1:
        zones = f"1-{numbers[0]}"
    elif len(numbers) == 2:
        zones = f"{numbers[0]}-{numbers[1]}"
    if zones not in fares.ZONES:
        raise refuse(NeedsInput, f"I can price zones {ZONE_CHOICES}. Which of those do you need?",
                     f"unknown zones {value!r}")
    return zones


def _parse_card(value) -> str:
    card = str(value).strip().lower() if _present(value) else "uk_contactless"
    return card if card in CARDS else "uk_contactless"


# --- Money ---
def _gbp(pence: int) -> float:
    return round(pence / 100, 2)


def _text(pence: int) -> str:
    return f"£{pence / 100:.2f}"


# --- Ticket cost rules (pence) ---
def _travelling_blocks(stay_days: int, days_per_week: int) -> list[int]:
    """Travelling days in each 7-day block of the stay."""
    full_weeks, leftover = divmod(stay_days, 7)
    blocks = [days_per_week] * full_weeks
    if leftover:
        blocks.append(min(leftover, days_per_week))
    return blocks


def _capped_cost(stay_days: int, days_per_week: int, daily_cap: int, weekly_cap: int) -> int:
    """Upper bound of pay as you go: every travelling day hits the daily cap, each block hits the weekly cap."""
    return sum(min(days * daily_cap, weekly_cap) for days in _travelling_blocks(stay_days, days_per_week))


def _weekly_cards_cost(stay_days: int, weekly_price: int) -> int:
    return math.ceil(stay_days / 7) * weekly_price


def _monthly_card_cost(stay_days: int, weekly_price: int, monthly_price: int) -> int:
    """Whole months, then the cheaper of weekly cards or one more month for the leftover days."""
    months, leftover = divmod(stay_days, DAYS_PER_MONTH_CARD)
    extra = min(_weekly_cards_cost(leftover, weekly_price), monthly_price) if leftover else 0
    return months * monthly_price + extra


def _options(zones: str, stay_days: int, days_per_week: int, bus_only: bool, student: bool) -> list[dict]:
    """Candidate tickets as {name, pence, reason}, in preference order (pay as you go first)."""
    if bus_only:
        daily, weekly = fares.BUS_DAILY_CAP_PENCE, fares.BUS_WEEKLY_PASS_PENCE
        options = [{"name": "Pay as you go on buses", "pence": _capped_cost(stay_days, days_per_week, daily, weekly)},
                   {"name": "7-day bus pass", "pence": _weekly_cards_cost(stay_days, weekly)}]
        return options
    daily, weekly = fares.daily_cap(zones), fares.weekly_cap(zones)
    discount = fares.student_price if student else (lambda pence: pence)
    options = [{"name": "Pay as you go with contactless", "pence": _capped_cost(stay_days, days_per_week, daily, weekly)},
               {"name": "Weekly Travelcards", "pence": _weekly_cards_cost(stay_days, discount(weekly))}]
    if stay_days >= DAYS_PER_MONTH_CARD:
        monthly = discount(fares.monthly_travelcard(zones))
        options.append({"name": "Monthly Travelcard", "pence": _monthly_card_cost(stay_days, discount(weekly), monthly)})
    return options


# --- Best ticket ---
def _why(best: dict, stay_days: int, days_per_week: int, daily: int, weekly: int) -> str:
    if not best["name"].startswith("Pay as you go"):
        return "Beats the pay as you go caps"
    capped_week = any(days * daily > weekly for days in _travelling_blocks(stay_days, days_per_week))
    return "Weekly cap kicks in" if capped_week else "Daily cap limits each day"


def _caveats(fee_pence: int, card: str, student: bool, bus_only: bool) -> list[str]:
    points = []
    if card == "none":
        points.append(f"Includes {_text(fee_pence)} Oyster card fee")
    if card == "foreign_contactless":
        points.append("Some foreign cards charge fees")
    if student and not bus_only:
        points.append("Student 30% off Travelcards only")
    return points


def best_ticket(inputs: dict, now: datetime) -> dict:
    """Cheapest way to pay for a stay: contactless caps, Travelcards or bus options."""
    stay_days = max(1, _whole_number(inputs.get("stay_days"), DEFAULT_STAY_DAYS))
    days_per_week = min(7, max(1, _whole_number(inputs.get("days_per_week"), 7)))
    bus_only, student = _truthy(inputs.get("bus_only")), _truthy(inputs.get("student"))
    zones, card = _parse_zones(inputs.get("zones")), _parse_card(inputs.get("card"))
    daily, weekly = (fares.BUS_DAILY_CAP_PENCE, fares.BUS_WEEKLY_PASS_PENCE) if bus_only else (
        fares.daily_cap(zones), fares.weekly_cap(zones))
    fee_pence = fares.STANDARD_OYSTER_FEE_PENCE if card == "none" else 0

    options = _options(zones, stay_days, days_per_week, bus_only, student)
    best = min(options, key=lambda option: option["pence"])  # min keeps the first on a tie
    total = best["pence"] + fee_pence
    place = "on buses" if bus_only else f"in zones {zones}"
    way = best["name"] if card != "none" else best["name"].replace("with contactless", "with Oyster")
    at_most = "at most " if best["name"].startswith("Pay as you go") else ""
    unit = "day" if stay_days == 1 else "days"
    points = [_why(best, stay_days, days_per_week, daily, weekly), *_caveats(fee_pence, card, student, bus_only)]
    return {
        "headline": f"{way}: {at_most}{_text(total)} for {stay_days} {unit} {place}.",
        "key_points": points[:MAX_POINTS],
        "source": TABLE_SOURCE,
        "best_option": way,
        "cost_gbp": _gbp(total),
    }


# --- Single fare ---
def _is_payg(fare_row: dict) -> bool:
    return "pay as you go" in fare_row["ticket"].lower()


def _is_peak(fare_row: dict) -> bool:
    return fare_row["time"].lower().replace(" ", "") == "peak"


def _station(name: str) -> str:
    """'Oxford Circus Underground Station' as 'Oxford Circus'."""
    return STATION_SUFFIX.sub("", name)


def _both_stations(from_place: str, to_place: str) -> tuple[dict, dict]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        start, end = pool.submit(l1_tfl.find_station, from_place), pool.submit(l1_tfl.find_station, to_place)
        return start.result(), end.result()


def single_fare(inputs: dict, now: datetime) -> dict:
    """Pay as you go fare between two stations: off-peak unless peak was asked for."""
    from_place, to_place = inputs.get("from_place"), inputs.get("to_place")
    if not (_present(from_place) and _present(to_place)):
        raise refuse(NeedsInput, "Which two stations do you want the fare between? Tell me where from and where to.",
                     "from_place or to_place missing")
    start, end = _both_stations(str(from_place).strip(), str(to_place).strip())
    rows = l1_tfl.fare(start["id"], end["id"])
    if not rows:
        raise refuse(NotFound, f"TfL publishes no fare between {start['name']} and {end['name']}.",
                     f"empty fare {start['id']} to {end['id']}")
    payg = [row for row in rows if _is_payg(row)] or rows
    peak = next((row for row in payg if _is_peak(row)), None)
    off_peak = next((row for row in payg if not _is_peak(row)), None)
    chosen = peak if _truthy(inputs.get("peak")) and peak else (off_peak or peak)
    label = "peak" if chosen is peak else "off-peak"
    other = [f"{name.capitalize()} £{row['cost_gbp']:.2f}" for name, row in (("peak", peak), ("off-peak", off_peak))
             if row and row is not chosen]
    return {
        "headline": (f"£{chosen['cost_gbp']:.2f} {label} with contactless, "
                     f"{_station(start['name'])} to {_station(end['name'])}."),
        "key_points": [*other, "Daily cap still applies"],
        "source": SOURCE,
        "best_option": f"{chosen['ticket']} ({chosen['time']})",
        "cost_gbp": round(float(chosen["cost_gbp"]), 2),
    }


# --- Airport fare ---
def _parse_airport(value) -> str:
    key = str(value).strip().lower().replace("airport", "").strip() if _present(value) else ""
    airport = AIRPORT_ALIASES.get(key) or AIRPORT_ALIASES.get(str(value).strip().lower() if _present(value) else "")
    if airport is None:
        raise refuse(NeedsInput, "Which airport? I cover Heathrow, Gatwick, Stansted, Luton and London City.",
                     f"unknown airport {value!r}")
    return airport


def _short_option(name: str) -> str:
    """'National Rail (Thameslink, Southern or Gatwick Express)' as 'National Rail'."""
    return name.split(" (")[0].split(",")[0]


def _option_point(option: dict) -> str:
    price = _text(option["price_pence"]) if option["price_pence"] is not None else "check price"
    return f"{_short_option(option['name'])}: {price}"


def _live_fare(airport: str, from_place: str) -> tuple[str, float] | None:
    """The start station's short name and its peak pay as you go fare, or None when TfL cannot give one."""
    try:
        start, end = _both_stations(from_place, AIRPORT_STATIONS[airport])
        rows = [row for row in l1_tfl.fare(start["id"], end["id"]) if _is_payg(row)]
    except AgentAppError:
        return None
    if not rows:
        return None
    row = max(rows, key=lambda candidate: candidate["cost_gbp"])  # peak is the higher fare
    return _station(start["name"]), float(row["cost_gbp"])


def airport_fare(inputs: dict, now: datetime) -> dict:
    """Cheapest known way to reach a London airport."""
    airport = _parse_airport(inputs.get("airport"))
    name = AIRPORT_NAMES[airport]
    from_place = inputs.get("from_place")
    live = _live_fare(airport, str(from_place).strip()) if _present(from_place) and airport in AIRPORT_STATIONS else None
    options = fares.AIRPORTS[airport]
    priced = [option for option in options if option["price_pence"] is not None]
    points = [_option_point(option) for option in options][:MAX_POINTS]
    if live:
        start, cost = live
        return {"headline": f"£{cost:.2f} with contactless from {start} to {name}.", "key_points": points,
                "source": SOURCE, "best_option": "Pay as you go", "cost_gbp": round(cost, 2)}
    if not priced:
        return {"headline": f"No checked price for {name}; check {_short_option(options[0]['name'])}.",
                "key_points": points, "source": TABLE_SOURCE}
    cheapest = min(priced, key=lambda option: option["price_pence"])
    return {
        "headline": f"{_short_option(cheapest['name'])} to {name}: {_text(cheapest['price_pence'])}.",
        "key_points": points,
        "source": TABLE_SOURCE,
        "best_option": cheapest["name"],
        "cost_gbp": _gbp(cheapest["price_pence"]),
    }
