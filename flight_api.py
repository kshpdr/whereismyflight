"""Flight data service — fetches live data from AviationStack or returns demo fixtures."""

from __future__ import annotations

import os
import re
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp

AVIATIONSTACK_KEY: str = os.getenv("AVIATIONSTACK_API_KEY", "")
AVIATIONSTACK_URL = "http://api.aviationstack.com/v1/flights"


def parse_flight_number(text: str) -> Optional[str]:
    """Extract a flight designator (e.g. 'DL404') from free-form text.

    Accepts formats like 'DL 404', 'dl404', 'DL-404', 'Delta 404'.
    Returns the normalised IATA code (uppercase, no spaces) or None.
    """
    AIRLINE_NAMES = {
        "delta": "DL", "united": "UA", "american": "AA",
        "southwest": "WN", "jetblue": "B6", "spirit": "NK",
        "frontier": "F9", "alaska": "AS", "hawaiian": "HA",
        "lufthansa": "LH", "british airways": "BA", "air france": "AF",
        "klm": "KL", "emirates": "EK", "turkish": "TK",
        "ryanair": "FR", "easyjet": "U2", "aeroflot": "SU",
        "singapore": "SQ", "cathay": "CX", "qantas": "QF",
        "air canada": "AC", "westjet": "WS",
    }

    cleaned = text.strip().lower()

    for name, code in AIRLINE_NAMES.items():
        if cleaned.startswith(name):
            rest = cleaned[len(name):].strip(" -")
            if re.fullmatch(r"\d{1,5}", rest):
                return f"{code}{rest}"

    m = re.search(r"([A-Za-z]{2})\s*[-]?\s*(\d{1,5})", text)
    if m:
        return f"{m.group(1).upper()}{m.group(2)}"
    return None


async def fetch_flight(flight_iata: str) -> Optional[dict]:
    """Return a normalised flight-data dict with a `legs` array, or None."""
    if AVIATIONSTACK_KEY:
        data = await _fetch_live(flight_iata)
        if data:
            return data
    return _generate_demo(flight_iata)


STATUS_MAP = {
    "scheduled": "Scheduled",
    "active": "In Air",
    "landed": "Landed",
    "cancelled": "Cancelled",
    "incident": "Incident",
    "diverted": "Diverted",
}


def _normalise_leg(f: dict) -> dict:
    dep = f.get("departure") or {}
    arr = f.get("arrival") or {}
    status_raw = (f.get("flight_status") or "unknown").lower()
    return {
        "status": STATUS_MAP.get(status_raw, status_raw.title()),
        "departure": {
            "airport": dep.get("airport", ""),
            "iata": dep.get("iata", ""),
            "scheduled": dep.get("scheduled", ""),
            "estimated": dep.get("estimated", ""),
            "actual": dep.get("actual", ""),
            "terminal": dep.get("terminal", ""),
            "gate": dep.get("gate", ""),
            "delay": dep.get("delay"),
        },
        "arrival": {
            "airport": arr.get("airport", ""),
            "iata": arr.get("iata", ""),
            "scheduled": arr.get("scheduled", ""),
            "estimated": arr.get("estimated", ""),
            "actual": arr.get("actual", ""),
            "terminal": arr.get("terminal", ""),
            "gate": arr.get("gate", ""),
            "delay": arr.get("delay"),
        },
        "live": bool(f.get("live")),
    }


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def _fetch_live(flight_iata: str) -> Optional[dict]:
    params = {
        "access_key": AVIATIONSTACK_KEY,
        "flight_iata": flight_iata,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(AVIATIONSTACK_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                body = await resp.json()
    except Exception:
        return None

    flights = body.get("data") or []
    if not flights:
        return None

    today = _today_utc()
    today_flights = [
        f for f in flights
        if (f.get("departure") or {}).get("scheduled", "").startswith(today)
    ]
    if not today_flights:
        today_flights = flights

    today_flights.sort(key=lambda f: (f.get("departure") or {}).get("scheduled", ""))

    airline = (today_flights[0].get("airline") or {}).get("name", "")
    legs = [_normalise_leg(f) for f in today_flights]

    overall = _overall_status(legs)

    return {
        "flight": flight_iata.upper(),
        "airline": airline,
        "status": overall,
        "legs": legs,
        "demo": False,
    }


def _overall_status(legs: list[dict]) -> str:
    statuses = [leg["status"] for leg in legs]
    if any(s == "Cancelled" for s in statuses):
        return "Cancelled"
    if any(s == "Diverted" for s in statuses):
        return "Diverted"
    if any(s == "In Air" for s in statuses):
        return "In Air"
    if all(s == "Landed" for s in statuses):
        return "Landed"
    if all(s == "Scheduled" for s in statuses):
        return "Scheduled"
    return "En Route"


DEMO_ROUTES: list[dict] = [
    {"dep": ("Los Angeles Intl", "LAX"), "arr": ("John F. Kennedy Intl", "JFK"), "duration_h": 5.2},
    {"dep": ("San Francisco Intl", "SFO"), "arr": ("London Heathrow", "LHR"), "duration_h": 10.5},
    {"dep": ("Chicago O'Hare", "ORD"), "arr": ("Miami Intl", "MIA"), "duration_h": 3.1},
    {"dep": ("Seattle-Tacoma Intl", "SEA"), "arr": ("Tokyo Narita", "NRT"), "duration_h": 10.8},
    {"dep": ("Dallas Fort Worth", "DFW"), "arr": ("Atlanta Hartsfield", "ATL"), "duration_h": 2.2},
    {"dep": ("Boston Logan", "BOS"), "arr": ("Paris Charles de Gaulle", "CDG"), "duration_h": 7.3},
    {"dep": ("Denver Intl", "DEN"), "arr": ("Honolulu Intl", "HNL"), "duration_h": 7.0},
    {"dep": ("New York Newark", "EWR"), "arr": ("San Francisco Intl", "SFO"), "duration_h": 5.8},
]

DEMO_AIRLINES = {
    "DL": "Delta Air Lines", "UA": "United Airlines", "AA": "American Airlines",
    "WN": "Southwest Airlines", "B6": "JetBlue Airways", "NK": "Spirit Airlines",
    "F9": "Frontier Airlines", "AS": "Alaska Airlines", "LH": "Lufthansa",
    "BA": "British Airways", "AF": "Air France", "EK": "Emirates",
    "TK": "Turkish Airlines", "SQ": "Singapore Airlines", "QF": "Qantas",
    "AC": "Air Canada",
}


def _generate_demo(flight_iata: str) -> dict:
    """Produce realistic-looking fake flight data seeded by the flight number."""
    seed = sum(ord(c) for c in flight_iata)
    rng = random.Random(seed)

    airline_code = re.match(r"[A-Z]{2}", flight_iata.upper())
    code = airline_code.group() if airline_code else "DL"
    airline_name = DEMO_AIRLINES.get(code, "Demo Airlines")

    num_legs = rng.choice([1, 1, 1, 2])
    chosen_routes = rng.sample(DEMO_ROUTES, k=min(num_legs, len(DEMO_ROUTES)))

    now = datetime.now(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%S+00:00"
    legs = []
    cursor = now - timedelta(hours=sum(r["duration_h"] for r in chosen_routes) * rng.uniform(0.3, 0.7))

    for route in chosen_routes:
        dep_scheduled = cursor.replace(minute=rng.choice([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]), second=0, microsecond=0)
        arr_scheduled = dep_scheduled + timedelta(hours=route["duration_h"])

        delay_minutes = rng.choice([0, 0, 0, 0, 12, 25, 47])
        dep_actual = dep_scheduled + timedelta(minutes=delay_minutes)
        arr_estimated = arr_scheduled + timedelta(minutes=delay_minutes)

        elapsed = (now - dep_actual).total_seconds()
        total = (arr_estimated - dep_actual).total_seconds()

        if elapsed < 0:
            status = "Scheduled"
        elif elapsed >= total:
            status = "Landed"
        else:
            status = "In Air"

        legs.append({
            "status": status,
            "departure": {
                "airport": route["dep"][0],
                "iata": route["dep"][1],
                "scheduled": dep_scheduled.strftime(fmt),
                "estimated": dep_actual.strftime(fmt),
                "actual": dep_actual.strftime(fmt) if status != "Scheduled" else "",
                "terminal": str(rng.randint(1, 8)),
                "gate": f"{rng.choice('ABCDEF')}{rng.randint(1, 60)}",
                "delay": delay_minutes if delay_minutes else None,
            },
            "arrival": {
                "airport": route["arr"][0],
                "iata": route["arr"][1],
                "scheduled": arr_scheduled.strftime(fmt),
                "estimated": arr_estimated.strftime(fmt),
                "actual": arr_estimated.strftime(fmt) if status == "Landed" else "",
                "terminal": str(rng.randint(1, 8)),
                "gate": f"{rng.choice('ABCDEF')}{rng.randint(1, 60)}",
                "delay": delay_minutes if delay_minutes else None,
            },
            "live": status == "In Air",
        })

        cursor = arr_estimated + timedelta(minutes=rng.randint(45, 120))

    overall = _overall_status(legs)

    return {
        "flight": flight_iata.upper(),
        "airline": airline_name,
        "status": overall,
        "legs": legs,
        "demo": True,
    }
