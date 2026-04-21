"""Flight data provider interface and implementations."""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional

from zoneinfo import ZoneInfo

import aiohttp
import airportsdata

log = logging.getLogger(__name__)
AIRPORTS = airportsdata.load("IATA")

AIRLINE_NAMES = {
    "DL": "Delta Air Lines", "UA": "United Airlines", "AA": "American Airlines",
    "WN": "Southwest Airlines", "B6": "JetBlue Airways", "NK": "Spirit Airlines",
    "F9": "Frontier Airlines", "AS": "Alaska Airlines", "HA": "Hawaiian Airlines",
    "LH": "Lufthansa", "BA": "British Airways", "AF": "Air France",
    "KL": "KLM", "EK": "Emirates", "TK": "Turkish Airlines",
    "FR": "Ryanair", "U2": "easyJet", "SU": "Aeroflot",
    "SQ": "Singapore Airlines", "CX": "Cathay Pacific", "QF": "Qantas",
    "AC": "Air Canada", "WS": "WestJet", "OO": "SkyWest Airlines",
}

IATA_TO_ICAO = {
    "DL": "DAL", "UA": "UAL", "AA": "AAL", "WN": "SWA",
    "B6": "JBU", "NK": "NKS", "F9": "FFT", "AS": "ASA",
    "HA": "HAL", "LH": "DLH", "BA": "BAW", "AF": "AFR",
    "KL": "KLM", "EK": "UAE", "TK": "THY", "FR": "RYR",
    "U2": "EZY", "SU": "AFL", "SQ": "SIA", "CX": "CPA",
    "QF": "QFA", "AC": "ACA", "WS": "WJA",
}


# ── Shared types ──────────────────────────────────────────────────

STATUS_MAP = {
    "scheduled": "Scheduled",
    "active": "In Air",
    "landed": "Landed",
    "cancelled": "Cancelled",
    "incident": "Incident",
    "diverted": "Diverted",
}


def overall_status(legs: list[dict]) -> str:
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


def local_iso_to_utc(iso: str, iata: str) -> Optional[datetime]:
    """Convert a local-time ISO string to real UTC using airport timezone.

    Used by AviationStack which returns local times tagged as +00:00.
    """
    if not iso:
        return None
    try:
        naive = datetime.fromisoformat(iso.replace("+00:00", "").replace("Z", ""))
        tz_name = (AIRPORTS.get(iata) or {}).get("tz")
        if tz_name:
            return naive.replace(tzinfo=ZoneInfo(tz_name)).astimezone(timezone.utc)
        return naive.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _parse_utc(iso: str | None) -> Optional[datetime]:
    """Parse an ISO 8601 UTC timestamp (as returned by AeroAPI)."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None


def _utc_to_local_iso(utc_dt: datetime | None, tz_name: str | None) -> str:
    """Convert a UTC datetime to a local-time ISO string for display.

    We store local ISO strings so the frontend can extract HH:MM directly.
    """
    if not utc_dt:
        return ""
    if tz_name:
        try:
            local = utc_dt.astimezone(ZoneInfo(tz_name))
            return local.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        except Exception:
            pass
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _tz_abbrev(utc_dt: datetime | None, tz_name: str | None) -> str:
    """Get the timezone abbreviation (e.g. 'PDT', 'CDT') for display."""
    if not utc_dt or not tz_name:
        return ""
    try:
        local = utc_dt.astimezone(ZoneInfo(tz_name))
        return local.strftime("%Z")
    except Exception:
        return ""


def compute_leg_timing(leg: dict) -> dict:
    """Add duration_min, remaining_min, and progress_pct to a leg."""
    dep = leg["departure"]
    arr = leg["arrival"]
    dep_iso = dep.get("actual") or dep.get("estimated") or dep.get("scheduled")
    arr_iso = arr.get("estimated") or arr.get("scheduled")

    dep_utc = local_iso_to_utc(dep_iso, dep.get("iata", ""))
    arr_utc = local_iso_to_utc(arr_iso, arr.get("iata", ""))

    duration_min = None
    progress_pct = 0
    remaining_min = None

    if dep_utc and arr_utc:
        diff = (arr_utc - dep_utc).total_seconds()
        if diff > 0:
            duration_min = round(diff / 60)

    if leg["status"] == "Landed":
        progress_pct = 100
    elif leg["status"] == "In Air" and dep_utc and arr_utc:
        now = datetime.now(timezone.utc)
        total = (arr_utc - dep_utc).total_seconds()
        elapsed = (now - dep_utc).total_seconds()
        if total > 0:
            progress_pct = max(2, min(98, round(elapsed / total * 100)))
            remaining_min = max(0, round((total - elapsed) / 60))

    leg["duration_min"] = duration_min
    leg["remaining_min"] = remaining_min
    leg["progress_pct"] = progress_pct
    return leg


def _pick_best_flight(legs: list[dict]) -> list[dict]:
    """From a list of normalised legs, determine if they form a connecting
    itinerary or are duplicate instances of the same route.

    Connecting chain: leg1.arrival.iata == leg2.departure.iata (e.g. SFO→DAL, DAL→ATL)
    Duplicates: all legs share the same origin→destination (e.g. SFO→PHX x3)

    For duplicates, pick the single most relevant flight:
      In Air > Landed (most recent) > Scheduled (soonest)
    """
    if len(legs) <= 1:
        return legs

    routes = [(l["departure"]["iata"], l["arrival"]["iata"]) for l in legs]
    all_same_route = len(set(routes)) == 1

    if not all_same_route:
        is_chain = all(
            legs[i]["arrival"]["iata"] == legs[i + 1]["departure"]["iata"]
            for i in range(len(legs) - 1)
        )
        if is_chain:
            return legs

    STATUS_PRIORITY = {"In Air": 0, "Landed": 1, "Scheduled": 2}
    legs.sort(key=lambda l: (
        STATUS_PRIORITY.get(l["status"], 9),
        -(l.get("progress_pct") or 0),
    ))
    return [legs[0]]


def _group_flights_by_day(flights: list[dict], dep_time_key: str) -> dict[str, list[dict]]:
    """Group raw API flights by departure date (UTC date string)."""
    groups: dict[str, list[dict]] = {}
    for f in flights:
        dep = f.get(dep_time_key) or ""
        date = dep[:10] if len(dep) >= 10 else "unknown"
        groups.setdefault(date, []).append(f)
    return groups


def _select_best_day(day_groups: dict[str, list[dict]], status_extractor) -> tuple[str, list[dict]]:
    """Pick the most relevant day's flights.

    Priority:
      1. Any day with an in-air flight
      2. Closest upcoming scheduled flight
      3. Most recently landed flight
    """
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")

    for date, flights in sorted(day_groups.items()):
        if any(status_extractor(f) == "In Air" for f in flights):
            return date, flights

    future_days = {d: fs for d, fs in day_groups.items() if d >= today_str}
    if future_days:
        closest = min(future_days.keys())
        return closest, future_days[closest]

    past_days = {d: fs for d, fs in day_groups.items() if d < today_str}
    if past_days:
        latest = max(past_days.keys())
        return latest, past_days[latest]

    first_key = next(iter(day_groups))
    return first_key, day_groups[first_key]


def _available_dates(day_groups: dict[str, list[dict]]) -> list[str]:
    """Return sorted list of available dates."""
    return sorted(d for d in day_groups if d != "unknown")


# ── Abstract provider ─────────────────────────────────────────────

class FlightProvider(ABC):
    @abstractmethod
    async def fetch(self, flight_iata: str, date: Optional[str] = None) -> Optional[dict]:
        """Return normalised flight dict with `legs` array, or None.

        If date is provided (YYYY-MM-DD), select that specific day's flight.
        """
        ...


# ── AviationStack ─────────────────────────────────────────────────

class AviationStackProvider(FlightProvider):
    URL = "http://api.aviationstack.com/v1/flights"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def fetch(self, flight_iata: str, date: Optional[str] = None) -> Optional[dict]:
        params = {"access_key": self.api_key, "flight_iata": flight_iata}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        log.warning("AviationStack HTTP %d for %s", resp.status, flight_iata)
                        return None
                    body = await resp.json()
        except Exception as e:
            log.warning("AviationStack request failed for %s: %s", flight_iata, e)
            return None

        if "error" in body:
            log.warning("AviationStack error for %s: %s", flight_iata, body["error"].get("message", ""))
            return None

        flights = body.get("data") or []
        if not flights:
            return None

        day_groups: dict[str, list[dict]] = {}
        for f in flights:
            dep = (f.get("departure") or {}).get("scheduled", "")
            d = dep[:10] if len(dep) >= 10 else "unknown"
            day_groups.setdefault(d, []).append(f)

        def as_status(f):
            raw = (f.get("flight_status") or "unknown").lower()
            return STATUS_MAP.get(raw, raw.title())

        if date and date in day_groups:
            selected_date, selected_flights = date, day_groups[date]
        else:
            selected_date, selected_flights = _select_best_day(day_groups, as_status)
        selected_flights.sort(key=lambda f: (f.get("departure") or {}).get("scheduled", ""))

        airline = (selected_flights[0].get("airline") or {}).get("name", "")
        legs = [self._normalise_leg(f) for f in selected_flights]
        legs = _pick_best_flight(legs)
        dates = _available_dates(day_groups)

        return {
            "flight": flight_iata.upper(),
            "airline": airline,
            "status": overall_status(legs),
            "legs": legs,
            "date": selected_date,
            "available_dates": dates,
            "demo": False,
        }

    @staticmethod
    def _normalise_leg(f: dict) -> dict:
        dep = f.get("departure") or {}
        arr = f.get("arrival") or {}
        status_raw = (f.get("flight_status") or "unknown").lower()

        dep_tz = (AIRPORTS.get(dep.get("iata", "")) or {}).get("tz")
        arr_tz = (AIRPORTS.get(arr.get("iata", "")) or {}).get("tz")
        dep_ref = local_iso_to_utc(dep.get("actual") or dep.get("estimated") or dep.get("scheduled", ""), dep.get("iata", ""))
        arr_ref = local_iso_to_utc(arr.get("actual") or arr.get("estimated") or arr.get("scheduled", ""), arr.get("iata", ""))

        leg = {
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
                "tz": _tz_abbrev(dep_ref, dep_tz),
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
                "tz": _tz_abbrev(arr_ref, arr_tz),
            },
            "live": bool(f.get("live")),
        }
        return compute_leg_timing(leg)


# ── FlightAware AeroAPI ───────────────────────────────────────────

class FlightAwareProvider(FlightProvider):
    URL = "https://aeroapi.flightaware.com/aeroapi"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _to_icao_ident(self, flight_iata: str) -> str:
        """Convert IATA ident like 'WN1933' to ICAO like 'SWA1933' for better results."""
        m = re.match(r"([A-Z]{2})(\d+)", flight_iata.upper())
        if m:
            iata_code, number = m.group(1), m.group(2)
            icao = IATA_TO_ICAO.get(iata_code)
            if icao:
                return f"{icao}{number}"
        return flight_iata

    async def fetch(self, flight_iata: str, date: Optional[str] = None) -> Optional[dict]:
        ident = self._to_icao_ident(flight_iata)
        headers = {"x-apikey": self.api_key}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.URL}/flights/{ident}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 401:
                        log.error("FlightAware: invalid API key")
                        return None
                    if resp.status != 200:
                        log.warning("FlightAware HTTP %d for %s", resp.status, ident)
                        return None
                    body = await resp.json()
        except Exception as e:
            log.warning("FlightAware request failed for %s: %s", ident, e)
            return None

        flights = body.get("flights") or []
        if not flights:
            return None

        dep_key = "scheduled_out"
        day_groups = _group_flights_by_day(flights, dep_key)

        def fa_status(f):
            return FlightAwareProvider._derive_status(f)

        if date and date in day_groups:
            selected_date, selected_flights = date, day_groups[date]
        else:
            selected_date, selected_flights = _select_best_day(day_groups, fa_status)
        selected_flights.sort(key=lambda f: f.get("scheduled_out") or f.get("scheduled_off") or "")

        first = selected_flights[0]
        iata_code = re.match(r"([A-Z]{2})", flight_iata.upper())
        marketing_code = iata_code.group(1) if iata_code else ""
        operator_code = first.get("operator_iata") or first.get("operator") or ""
        airline = AIRLINE_NAMES.get(marketing_code) or AIRLINE_NAMES.get(operator_code) or operator_code
        legs = [self._normalise_leg(f) for f in selected_flights]
        legs = _pick_best_flight(legs)
        dates = _available_dates(day_groups)

        return {
            "flight": flight_iata.upper(),
            "airline": airline,
            "status": overall_status(legs),
            "legs": legs,
            "date": selected_date,
            "available_dates": dates,
            "demo": False,
        }

    @staticmethod
    def _derive_status(f: dict) -> str:
        if f.get("cancelled"):
            return "Cancelled"
        if f.get("diverted"):
            return "Diverted"
        progress = f.get("progress_percent")
        if progress is not None:
            if progress >= 100:
                return "Landed"
            if progress > 0:
                return "In Air"
        status_str = (f.get("status") or "").lower()
        if "landed" in status_str or "arrived" in status_str:
            return "Landed"
        if "en route" in status_str:
            return "In Air"
        return "Scheduled"

    @staticmethod
    def _normalise_leg(f: dict) -> dict:
        origin = f.get("origin") or {}
        dest = f.get("destination") or {}
        origin_tz = origin.get("timezone")
        dest_tz = dest.get("timezone")

        dep_sched_utc = _parse_utc(f.get("scheduled_out") or f.get("scheduled_off"))
        dep_est_utc = _parse_utc(f.get("estimated_out") or f.get("estimated_off"))
        dep_act_utc = _parse_utc(f.get("actual_out") or f.get("actual_off"))

        arr_sched_utc = _parse_utc(f.get("scheduled_in") or f.get("scheduled_on"))
        arr_est_utc = _parse_utc(f.get("estimated_in") or f.get("estimated_on"))
        arr_act_utc = _parse_utc(f.get("actual_in") or f.get("actual_on"))

        status = FlightAwareProvider._derive_status(f)

        dep_delay_sec = f.get("departure_delay")
        arr_delay_sec = f.get("arrival_delay")
        dep_delay_min = round(dep_delay_sec / 60) if dep_delay_sec and dep_delay_sec > 0 else None
        arr_delay_min = round(arr_delay_sec / 60) if arr_delay_sec and arr_delay_sec > 0 else None

        dep_utc = dep_act_utc or dep_est_utc or dep_sched_utc
        arr_utc = arr_est_utc or arr_sched_utc

        duration_min = None
        progress_pct = 0
        remaining_min = None

        if dep_utc and arr_utc:
            diff = (arr_utc - dep_utc).total_seconds()
            if diff > 0:
                duration_min = round(diff / 60)

        api_progress = f.get("progress_percent")
        if status == "Landed":
            progress_pct = 100
        elif status == "In Air":
            if api_progress is not None:
                progress_pct = max(2, min(98, api_progress))
            if dep_utc and arr_utc:
                now = datetime.now(timezone.utc)
                total = (arr_utc - dep_utc).total_seconds()
                elapsed = (now - dep_utc).total_seconds()
                if total > 0:
                    remaining_min = max(0, round((total - elapsed) / 60))
                    if api_progress is None:
                        progress_pct = max(2, min(98, round(elapsed / total * 100)))

        dep_ref = dep_act_utc or dep_est_utc or dep_sched_utc
        arr_ref = arr_act_utc or arr_est_utc or arr_sched_utc

        leg = {
            "status": status,
            "departure": {
                "airport": origin.get("name") or "",
                "iata": origin.get("code_iata") or origin.get("code") or "",
                "scheduled": _utc_to_local_iso(dep_sched_utc, origin_tz),
                "estimated": _utc_to_local_iso(dep_est_utc, origin_tz),
                "actual": _utc_to_local_iso(dep_act_utc, origin_tz) if status != "Scheduled" else "",
                "terminal": f.get("terminal_origin") or "",
                "gate": f.get("gate_origin") or "",
                "delay": dep_delay_min,
                "tz": _tz_abbrev(dep_ref, origin_tz),
            },
            "arrival": {
                "airport": dest.get("name") or "",
                "iata": dest.get("code_iata") or dest.get("code") or "",
                "scheduled": _utc_to_local_iso(arr_sched_utc, dest_tz),
                "estimated": _utc_to_local_iso(arr_est_utc, dest_tz),
                "actual": _utc_to_local_iso(arr_act_utc, dest_tz) if status == "Landed" else "",
                "terminal": f.get("terminal_destination") or "",
                "gate": f.get("gate_destination") or "",
                "delay": arr_delay_min,
                "tz": _tz_abbrev(arr_ref, dest_tz),
            },
            "live": status == "In Air",
            "duration_min": duration_min,
            "progress_pct": progress_pct,
            "remaining_min": remaining_min,
        }
        return leg
