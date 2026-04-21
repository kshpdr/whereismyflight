"""Flight data service — caching layer + demo fallback on top of providers."""

from __future__ import annotations

import logging
import os
import re
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from providers import (
    AIRLINE_NAMES as PROVIDER_AIRLINE_NAMES,
    AviationStackProvider, FlightAwareProvider, FlightProvider,
    compute_leg_timing, overall_status,
)

log = logging.getLogger(__name__)

# ── Provider setup ────────────────────────────────────────────────
# Priority: FlightAware AeroAPI > AviationStack > demo mode

_provider: Optional[FlightProvider] = None

FLIGHTAWARE_KEY = os.getenv("FLIGHTAWARE_API_KEY", "")
AVIATIONSTACK_KEY = os.getenv("AVIATIONSTACK_API_KEY", "")

if FLIGHTAWARE_KEY:
    _provider = FlightAwareProvider(FLIGHTAWARE_KEY)
    log.info("Using FlightAware AeroAPI provider")
elif AVIATIONSTACK_KEY:
    _provider = AviationStackProvider(AVIATIONSTACK_KEY)
    log.info("Using AviationStack provider")


# ── Cache ─────────────────────────────────────────────────────────

CACHE_TTL_ACTIVE = 300     # 5 min for scheduled / in-air flights
CACHE_TTL_TERMINAL = 3600  # 1 hour for landed / cancelled

_cache: dict[str, tuple[float, dict]] = {}


def _cache_ttl(data: dict) -> int:
    status = data.get("status", "")
    if status in ("Landed", "Cancelled"):
        return CACHE_TTL_TERMINAL
    return CACHE_TTL_ACTIVE


def _cache_get(key: str) -> Optional[dict]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, data = entry
    ttl = _cache_ttl(data)
    if time.monotonic() - ts > ttl:
        del _cache[key]
        return None
    return data


def _cache_set(key: str, data: dict) -> None:
    _cache[key] = (time.monotonic(), data)


# ── Rate limiting ─────────────────────────────────────────────────

USER_RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_USER", "10"))   # per user per window
USER_RATE_WINDOW = 60                                             # 1 minute
GLOBAL_DAILY_LIMIT = int(os.getenv("RATE_LIMIT_DAILY", "500"))   # total API calls/day

_user_hits: dict[int, list[float]] = {}  # user_id → list of timestamps
_global_counter: list[float] = []        # timestamps of all API calls


def _check_rate_limit(user_id: Optional[int]) -> Optional[str]:
    """Return an error message if rate-limited, or None if OK."""
    now = time.monotonic()

    # global daily limit
    cutoff_day = now - 86400
    _global_counter[:] = [t for t in _global_counter if t > cutoff_day]
    if len(_global_counter) >= GLOBAL_DAILY_LIMIT:
        log.warning("Global daily rate limit reached (%d)", GLOBAL_DAILY_LIMIT)
        return "The bot is temporarily at capacity. Please try again later."

    # per-user limit
    if user_id is not None:
        cutoff = now - USER_RATE_WINDOW
        hits = _user_hits.get(user_id, [])
        hits = [t for t in hits if t > cutoff]
        _user_hits[user_id] = hits
        if len(hits) >= USER_RATE_LIMIT:
            log.warning("User %d rate-limited (%d/%d in %ds)", user_id, len(hits), USER_RATE_LIMIT, USER_RATE_WINDOW)
            return f"Too many requests — please wait a minute before trying again."

    return None


def _record_api_call(user_id: Optional[int]) -> None:
    """Record that an API call was made (only called on actual provider hits)."""
    now = time.monotonic()
    _global_counter.append(now)
    if user_id is not None:
        _user_hits.setdefault(user_id, []).append(now)


# ── Public API ────────────────────────────────────────────────────

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


def parse_flight_number(text: str) -> Optional[str]:
    """Extract a flight designator (e.g. 'DL404') from free-form text."""
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


class RateLimitError(Exception):
    """Raised when a user or global rate limit is exceeded."""
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


async def fetch_flight(flight_iata: str, user_id: Optional[int] = None, date: Optional[str] = None) -> Optional[dict]:
    """Return flight data — from cache, live provider, or demo fallback.

    If date is provided (YYYY-MM-DD), fetch that specific day's flight.
    Raises RateLimitError if the user or global limit is exceeded.
    """
    key = flight_iata.upper()
    cache_key = f"{key}:{date}" if date else key

    # cache hits are free — no rate limit check needed
    cached = _cache_get(cache_key)
    if cached is not None:
        log.debug("Cache hit for %s", cache_key)
        if not cached.get("demo"):
            _refresh_timing(cached)
        return cached

    # rate limit only applies to actual API calls
    if _provider:
        err = _check_rate_limit(user_id)
        if err:
            raise RateLimitError(err)

        data = await _provider.fetch(key, date=date)
        if data:
            _record_api_call(user_id)
            log.info("Live data for %s (%d legs)", key, len(data.get("legs", [])))
            _cache_set(cache_key, data)
            return data
        log.warning("Provider returned no data for %s, falling back to demo", key)

    demo = _generate_demo(key)
    return demo


def _refresh_timing(data: dict) -> None:
    """Recompute progress_pct and remaining_min on cached data (they're time-dependent)."""
    for leg in data.get("legs", []):
        compute_leg_timing(leg)
    data["status"] = overall_status(data.get("legs", []))


# ── Demo data ─────────────────────────────────────────────────────

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

DEMO_AIRLINES = PROVIDER_AIRLINE_NAMES


def _generate_demo(flight_iata: str) -> dict:
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

    legs = [compute_leg_timing(leg) for leg in legs]

    return {
        "flight": flight_iata.upper(),
        "airline": airline_name,
        "status": overall_status(legs),
        "legs": legs,
        "demo": True,
    }
