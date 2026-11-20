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

DEFAULT_STAY_DAYS = 7
DEFAULT_RIDES_PER_DAY = 4
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
        return "it beats the pay as you go caps"
    capped_week = any(days * daily > weekly for days in _travelling_blocks(stay_days, days_per_week))
    return "the weekly cap kicks in" if capped_week else "the daily cap limits each day"


def _facts(options: list[dict], fee_pence: int, card: str, student: bool, bus_only: bool, rides: int) -> list[str]:
    facts = [f"{o['name']}: {'at most ' if o['name'].startswith('Pay as you go') else ''}{_text(o['pence'])}"
             for o in options]
    if any(o["name"] == "Monthly Travelcard" for o in options):
        facts.append(f"Monthly Travelcard price is about {fares.MONTHLY_FACTOR} times the 7-day price (derived, not an official fare)")
    if card == "none":
        facts.append(f"Oyster card fee of {_text(fee_pence)} is included (a Visitor Oyster costs "
                     f"{_text(fares.VISITOR_OYSTER_FEE_PENCE)}, fee not refundable)")
    if card == "foreign_contactless":
        facts.append("Foreign contactless cards are capped too, but some are refused and your card issuer may charge fees")
    if student and not bus_only:
        facts.append("Student discount is 30% on Travelcards only and needs an 18+ Student Oyster for London residents")
    if not bus_only:
        facts.append(f"Pay as you go figures assume the daily cap is reached; {rides} rides a day may cost less")
    return facts[:6]


def best_ticket(inputs: dict, now: datetime) -> dict:
    """Cheapest way to pay for a stay: contactless caps, Travelcards or bus options."""
    stay_days = max(1, _whole_number(inputs.get("stay_days"), DEFAULT_STAY_DAYS))
    rides = max(1, _whole_number(inputs.get("rides_per_day"), DEFAULT_RIDES_PER_DAY))
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
    return {
        "headline": (f"{way} {'are' if way.endswith('Travelcards') else 'is'} cheapest for {stay_days} {unit} {place}: {at_most}{_text(total)}, "
                     f"because {_why(best, stay_days, days_per_week, daily, weekly)}."),
        "facts": _facts(options, fee_pence, card, student, bus_only, rides),
        "source": fares.SOURCE,
        "as_of": fares.FARES_AS_OF,
        "best_option": way,
        "cost_gbp": _gbp(total),
    }


# --- Single fare ---
def _is_payg(fare_row: dict) -> bool:
    return "pay as you go" in fare_row["ticket"].lower()


def _is_peak(fare_row: dict) -> bool:
    return fare_row["time"].lower().replace(" ", "") == "peak"


def _london_minute(now: datetime) -> str:
    return now.astimezone(l1_tfl.LONDON).isoformat(timespec="minutes")


def _both_stations(from_place: str, to_place: str) -> tuple[dict, dict]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        start, end = pool.submit(l1_tfl.find_station, from_place), pool.submit(l1_tfl.find_station, to_place)
        return start.result(), end.result()


def single_fare(inputs: dict, now: datetime) -> dict:
    """Pay as you go peak and off-peak single fares between two stations."""
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
    names = f"{start['name']} to {end['name']}"
    parts = [f"{label} £{row['cost_gbp']:.2f}" for label, row in (("peak", peak), ("off-peak", off_peak)) if row]
    facts = [f"{row['ticket']}, {row['time']}: £{row['cost_gbp']:.2f}" for row in rows]
    facts.append("Daily and weekly caps still apply, so you never pay more than the cap in a day")
    return {
        "headline": f"Pay as you go from {names} is {' and '.join(parts)}.",
        "facts": facts[:6],
        "source": l1_tfl.SOURCE,
        "as_of": _london_minute(now),
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


def _table_facts(airport: str) -> list[str]:
    facts = []
    for option in fares.AIRPORTS[airport]:
        price = (f"{_text(option['price_pence'])}" if option["price_pence"] is not None
                 else "price not checked, see the operator")
        cap = "counts toward the daily cap" if option["capped"] else "outside the daily cap"
        facts.append(f"{option['name']}: {price}; {cap}")
    return facts


def _live_fare_fact(airport: str, from_place: str) -> tuple[str, float] | None:
    """A live pay as you go fare line and its price, or None when TfL cannot give one."""
    try:
        start, end = _both_stations(from_place, AIRPORT_STATIONS[airport])
        rows = [row for row in l1_tfl.fare(start["id"], end["id"]) if _is_payg(row)]
    except AgentAppError:
        return None
    if not rows:
        return None
    row = max(rows, key=lambda candidate: candidate["cost_gbp"])  # peak is the higher fare
    return f"Pay as you go from {start['name']} is £{row['cost_gbp']:.2f} ({row['time']})", float(row["cost_gbp"])


def _airport_headline(airport: str, priced: list[dict], total: int) -> str:
    name = AIRPORT_NAMES[airport]
    if not priced:
        return f"I have no verified price for getting to {name}; the facts list the options to check with the operator."
    cheapest = min(priced, key=lambda option: option["price_pence"])
    unpriced = total - len(priced)
    tail = f"; {unpriced} other {'option was' if unpriced == 1 else 'options were'} not priced here" if unpriced else ""
    return f"{cheapest['name']} to {name} costs {_text(cheapest['price_pence'])}{tail}."


def airport_fare(inputs: dict, now: datetime) -> dict:
    """Ways to reach a London airport with prices where they are known."""
    airport = _parse_airport(inputs.get("airport"))
    from_place = inputs.get("from_place")
    live = _live_fare_fact(airport, str(from_place).strip()) if _present(from_place) and airport in AIRPORT_STATIONS else None
    options = fares.AIRPORTS[airport]
    priced = [option for option in options if option["price_pence"] is not None]
    facts = _table_facts(airport)
    result = {
        "headline": _airport_headline(airport, priced, len(options)),
        "facts": facts[:5],
        "source": fares.SOURCE,
        "as_of": fares.FARES_AS_OF,
    }
    if priced:
        cheapest = min(priced, key=lambda option: option["price_pence"])
        result["best_option"], result["cost_gbp"] = cheapest["name"], _gbp(cheapest["price_pence"])
    if live:
        line, cost = live
        result["facts"] = [line, *facts[:4]]
        result["headline"] = f"{line} to {AIRPORT_NAMES[airport]}."
        result["source"], result["as_of"] = l1_tfl.SOURCE, _london_minute(now)
        result["best_option"], result["cost_gbp"] = "Pay as you go", round(cost, 2)
    return result
