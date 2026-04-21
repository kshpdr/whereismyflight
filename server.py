"""Lightweight aiohttp web server — serves the Mini App and a flight-data API."""

from __future__ import annotations

import json
import os
from pathlib import Path

from aiohttp import web

from flight_api import fetch_flight, parse_flight_number, RateLimitError

WEBAPP_DIR = Path(__file__).parent / "webapp"
LANDING_HTML = WEBAPP_DIR / "landing.html"


async def handle_landing(request: web.Request) -> web.Response:
    return web.FileResponse(LANDING_HTML)


async def handle_flight_api(request: web.Request) -> web.Response:
    raw = request.match_info.get("flight", "")
    flight_code = parse_flight_number(raw) or raw.upper()
    if not flight_code:
        return web.json_response({"error": "invalid flight number"}, status=400)

    date = request.query.get("date")

    try:
        data = await fetch_flight(flight_code, date=date)
    except RateLimitError as e:
        return web.json_response({"error": e.message}, status=429)

    if data is None:
        return web.json_response({"error": "flight not found"}, status=404)

    return web.json_response(data, headers={"Access-Control-Allow-Origin": "*"})


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def create_web_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_landing)
    app.router.add_get("/api/flight/{flight}", handle_flight_api)
    app.router.add_get("/health", handle_health)
    app.router.add_static("/webapp", WEBAPP_DIR, show_index=True)
    return app
