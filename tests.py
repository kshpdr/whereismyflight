"""Tests for flight parsing, caching, provider fallback, and leg timing."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest

from flight_api import (
    _cache, _cache_get, _cache_set, _generate_demo,
    fetch_flight, parse_flight_number,
)
from providers import (
    AviationStackProvider, FlightAwareProvider, FlightProvider,
    compute_leg_timing, local_iso_to_utc, overall_status,
    _parse_utc, _utc_to_local_iso,
)


# ── parse_flight_number ───────────────────────────────────────────

class TestParseFlightNumber:
    def test_standard_formats(self):
        assert parse_flight_number("DL 404") == "DL404"
        assert parse_flight_number("dl404") == "DL404"
        assert parse_flight_number("DL-404") == "DL404"
        assert parse_flight_number("UA 100") == "UA100"

    def test_airline_names(self):
        assert parse_flight_number("Delta 404") == "DL404"
        assert parse_flight_number("united 1234") == "UA1234"
        assert parse_flight_number("Southwest 1933") == "WN1933"
        assert parse_flight_number("air france 2227") == "AF2227"
        assert parse_flight_number("british airways 100") == "BA100"

    def test_no_match(self):
        assert parse_flight_number("hello world") is None
        assert parse_flight_number("") is None
        assert parse_flight_number("just text") is None

    def test_whitespace(self):
        assert parse_flight_number("  DL 404  ") == "DL404"
        assert parse_flight_number("WN  1933") == "WN1933"

    def test_long_flight_numbers(self):
        assert parse_flight_number("AA 12345") == "AA12345"


# ── local_iso_to_utc ─────────────────────────────────────────────

class TestLocalIsoToUtc:
    def test_known_airport(self):
        utc = local_iso_to_utc("2026-04-15T13:20:00+00:00", "SFO")
        assert utc is not None
        assert utc.hour == 20  # SFO is PDT (UTC-7) in April
        assert utc.minute == 20

    def test_different_timezone(self):
        utc = local_iso_to_utc("2026-04-15T18:55:00+00:00", "DAL")
        assert utc is not None
        assert utc.hour == 23  # DAL is CDT (UTC-5) in April
        assert utc.minute == 55

    def test_unknown_airport_falls_back_to_utc(self):
        utc = local_iso_to_utc("2026-04-15T10:00:00+00:00", "XXX")
        assert utc is not None
        assert utc.hour == 10

    def test_empty_string(self):
        assert local_iso_to_utc("", "SFO") is None
        assert local_iso_to_utc(None, "SFO") is None


# ── compute_leg_timing ────────────────────────────────────────────

class TestComputeLegTiming:
    def _make_leg(self, status, dep_iata="SFO", arr_iata="DAL",
                  dep_time="2026-04-15T10:00:00+00:00",
                  arr_time="2026-04-15T13:30:00+00:00"):
        return {
            "status": status,
            "departure": {
                "iata": dep_iata, "airport": "", "scheduled": dep_time,
                "estimated": dep_time, "actual": dep_time if status != "Scheduled" else "",
                "terminal": "1", "gate": "A1", "delay": None,
            },
            "arrival": {
                "iata": arr_iata, "airport": "", "scheduled": arr_time,
                "estimated": arr_time, "actual": arr_time if status == "Landed" else "",
                "terminal": "2", "gate": "B2", "delay": None,
            },
            "live": status == "In Air",
        }

    def test_landed_is_100_pct(self):
        leg = compute_leg_timing(self._make_leg("Landed"))
        assert leg["progress_pct"] == 100
        assert leg["duration_min"] is not None
        assert leg["duration_min"] > 0

    def test_scheduled_is_0_pct(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        future_arr = (datetime.now(timezone.utc) + timedelta(hours=15)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        leg = compute_leg_timing(self._make_leg("Scheduled", dep_time=future, arr_time=future_arr))
        assert leg["progress_pct"] == 0
        assert leg["remaining_min"] is None

    def test_in_air_has_progress(self):
        now = datetime.now(timezone.utc)
        dep = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        arr = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        leg = compute_leg_timing(self._make_leg("In Air", dep_iata="JFK", arr_iata="JFK",
                                                 dep_time=dep, arr_time=arr))
        assert 2 <= leg["progress_pct"] <= 98
        assert leg["remaining_min"] is not None
        assert leg["remaining_min"] > 0
        assert leg["duration_min"] is not None

    def test_duration_cross_timezone(self):
        leg = compute_leg_timing(self._make_leg("Landed",
                                                 dep_iata="SFO", arr_iata="DAL",
                                                 dep_time="2026-04-15T13:20:00+00:00",
                                                 arr_time="2026-04-15T18:55:00+00:00"))
        assert leg["duration_min"] is not None
        assert 200 <= leg["duration_min"] <= 220  # ~3.5h, not naive 5.5h


# ── overall_status ────────────────────────────────────────────────

class TestOverallStatus:
    def test_all_scheduled(self):
        assert overall_status([{"status": "Scheduled"}, {"status": "Scheduled"}]) == "Scheduled"

    def test_all_landed(self):
        assert overall_status([{"status": "Landed"}, {"status": "Landed"}]) == "Landed"

    def test_one_in_air(self):
        assert overall_status([{"status": "Landed"}, {"status": "In Air"}]) == "In Air"

    def test_cancelled_takes_priority(self):
        assert overall_status([{"status": "In Air"}, {"status": "Cancelled"}]) == "Cancelled"

    def test_mixed_landed_scheduled(self):
        assert overall_status([{"status": "Landed"}, {"status": "Scheduled"}]) == "En Route"


# ── Cache ─────────────────────────────────────────────────────────

class TestCache:
    def setup_method(self):
        _cache.clear()

    def test_set_and_get(self):
        data = {"status": "In Air", "legs": []}
        _cache_set("DL404", data)
        assert _cache_get("DL404") is data

    def test_miss(self):
        assert _cache_get("NOPE") is None

    def test_expired(self):
        data = {"status": "In Air", "legs": []}
        _cache["DL404"] = (time.monotonic() - 600, data)  # 10 min ago
        assert _cache_get("DL404") is None

    def test_landed_has_longer_ttl(self):
        data = {"status": "Landed", "legs": []}
        _cache["DL404"] = (time.monotonic() - 400, data)  # 6.6 min ago
        assert _cache_get("DL404") is data  # still valid (1hr TTL)


# ── fetch_flight integration ──────────────────────────────────────

class TestFetchFlight:
    def setup_method(self):
        _cache.clear()

    @pytest.mark.asyncio
    async def test_demo_fallback_when_no_provider(self):
        with patch("flight_api._provider", None):
            data = await fetch_flight("DL404")
            assert data is not None
            assert data["demo"] is True
            assert len(data["legs"]) >= 1

    @pytest.mark.asyncio
    async def test_cache_prevents_second_call(self):
        mock_provider = AsyncMock(spec=FlightProvider)
        mock_provider.fetch.return_value = {
            "flight": "DL404", "airline": "Delta", "status": "In Air",
            "legs": [{"status": "In Air", "departure": {"iata": "SFO"}, "arrival": {"iata": "JFK"}}],
            "demo": False,
        }

        with patch("flight_api._provider", mock_provider):
            first = await fetch_flight("DL404")
            second = await fetch_flight("DL404")

        assert mock_provider.fetch.call_count == 1
        assert first["flight"] == "DL404"
        assert second["flight"] == "DL404"

    @pytest.mark.asyncio
    async def test_provider_failure_falls_back_to_demo(self):
        mock_provider = AsyncMock(spec=FlightProvider)
        mock_provider.fetch.return_value = None

        with patch("flight_api._provider", mock_provider):
            data = await fetch_flight("XX999")
            assert data is not None
            assert data["demo"] is True


# ── Demo data ─────────────────────────────────────────────────────

# ── FlightAware provider ──────────────────────────────────────────

SAMPLE_FA_FLIGHT = {
    "ident": "SWA1933",
    "ident_iata": "WN1933",
    "operator": "SWA",
    "operator_iata": "WN",
    "flight_number": "1933",
    "cancelled": False,
    "diverted": False,
    "origin": {
        "code": "KSFO", "code_iata": "SFO", "code_icao": "KSFO",
        "name": "San Francisco Intl", "city": "San Francisco",
        "timezone": "America/Los_Angeles",
    },
    "destination": {
        "code": "KDAL", "code_iata": "DAL", "code_icao": "KDAL",
        "name": "Dallas Love Field", "city": "Dallas",
        "timezone": "America/Chicago",
    },
    "scheduled_out": "2026-04-15T20:20:00Z",
    "estimated_out": "2026-04-15T20:25:00Z",
    "actual_out": "2026-04-15T20:28:00Z",
    "scheduled_off": "2026-04-15T20:30:00Z",
    "actual_off": "2026-04-15T20:35:00Z",
    "scheduled_on": "2026-04-16T01:50:00Z",
    "estimated_on": "2026-04-16T01:45:00Z",
    "actual_on": None,
    "scheduled_in": "2026-04-16T01:55:00Z",
    "estimated_in": "2026-04-16T01:50:00Z",
    "actual_in": None,
    "progress_percent": 45,
    "status": "En Route / On Time",
    "departure_delay": 480,
    "arrival_delay": None,
    "gate_origin": "15",
    "gate_destination": "7",
    "terminal_origin": "2",
    "terminal_destination": "1",
    "filed_ete": 19800,
    "baggage_claim": None,
}


class TestFlightAwareNormaliseLeg:
    def test_basic_fields(self):
        leg = FlightAwareProvider._normalise_leg(SAMPLE_FA_FLIGHT)
        assert leg["status"] == "In Air"
        assert leg["departure"]["iata"] == "SFO"
        assert leg["departure"]["airport"] == "San Francisco Intl"
        assert leg["arrival"]["iata"] == "DAL"
        assert leg["departure"]["terminal"] == "2"
        assert leg["departure"]["gate"] == "15"
        assert leg["arrival"]["gate"] == "7"

    def test_delay_converted_to_minutes(self):
        leg = FlightAwareProvider._normalise_leg(SAMPLE_FA_FLIGHT)
        assert leg["departure"]["delay"] == 8  # 480 seconds → 8 min

    def test_progress_from_api(self):
        leg = FlightAwareProvider._normalise_leg(SAMPLE_FA_FLIGHT)
        assert leg["progress_pct"] == 45

    def test_duration_computed(self):
        leg = FlightAwareProvider._normalise_leg(SAMPLE_FA_FLIGHT)
        assert leg["duration_min"] is not None
        assert leg["duration_min"] > 0

    def test_times_converted_to_local(self):
        leg = FlightAwareProvider._normalise_leg(SAMPLE_FA_FLIGHT)
        assert "13:20" in leg["departure"]["scheduled"]  # 20:20 UTC → 13:20 PDT
        assert "20:55" in leg["arrival"]["scheduled"]     # 01:55 UTC → 20:55 CDT

    def test_cancelled_status(self):
        f = {**SAMPLE_FA_FLIGHT, "cancelled": True, "progress_percent": None, "status": "Cancelled"}
        leg = FlightAwareProvider._normalise_leg(f)
        assert leg["status"] == "Cancelled"

    def test_landed_status(self):
        f = {**SAMPLE_FA_FLIGHT, "progress_percent": 100, "status": "Arrived / Gate Arrival",
             "actual_in": "2026-04-16T01:52:00Z", "actual_on": "2026-04-16T01:48:00Z"}
        leg = FlightAwareProvider._normalise_leg(f)
        assert leg["status"] == "Landed"
        assert leg["progress_pct"] == 100

    def test_scheduled_status(self):
        f = {**SAMPLE_FA_FLIGHT, "progress_percent": None, "status": "Scheduled",
             "actual_out": None, "actual_off": None}
        leg = FlightAwareProvider._normalise_leg(f)
        assert leg["status"] == "Scheduled"
        assert leg["progress_pct"] == 0


class TestFlightAwareIcaoConversion:
    def test_known_airlines(self):
        p = FlightAwareProvider("fake")
        assert p._to_icao_ident("WN1933") == "SWA1933"
        assert p._to_icao_ident("DL404") == "DAL404"
        assert p._to_icao_ident("UA100") == "UAL100"
        assert p._to_icao_ident("BA247") == "BAW247"

    def test_unknown_airline_passes_through(self):
        p = FlightAwareProvider("fake")
        assert p._to_icao_ident("XX999") == "XX999"


class TestParseUtcAndLocalIso:
    def test_parse_utc_z_suffix(self):
        dt = _parse_utc("2026-04-15T20:20:00Z")
        assert dt is not None
        assert dt.hour == 20

    def test_parse_utc_offset(self):
        dt = _parse_utc("2026-04-15T20:20:00+00:00")
        assert dt is not None
        assert dt.hour == 20

    def test_parse_utc_none(self):
        assert _parse_utc(None) is None
        assert _parse_utc("") is None

    def test_utc_to_local_sfo(self):
        utc = datetime(2026, 4, 15, 20, 20, tzinfo=timezone.utc)
        local = _utc_to_local_iso(utc, "America/Los_Angeles")
        assert "13:20" in local  # PDT = UTC-7

    def test_utc_to_local_none(self):
        assert _utc_to_local_iso(None, "America/New_York") == ""


class TestDemoData:
    def test_deterministic(self):
        a = _generate_demo("DL404")
        b = _generate_demo("DL404")
        assert a["airline"] == b["airline"]
        assert len(a["legs"]) == len(b["legs"])

    def test_has_required_fields(self):
        data = _generate_demo("UA100")
        assert "flight" in data
        assert "airline" in data
        assert "status" in data
        assert "legs" in data
        assert "demo" in data
        assert data["demo"] is True

        for leg in data["legs"]:
            assert "duration_min" in leg
            assert "progress_pct" in leg
            assert "remaining_min" in leg
            assert "departure" in leg
            assert "arrival" in leg
