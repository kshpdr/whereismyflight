# whereismyflight - telegram flight previews

A good friend of mine Alex complained about absence of native flight previews in Telegram compared to iMessages. But we don't want to use iMessage. So have to fix this problem ourselves.

This bot allows you to type in inline in any chat your flight number and generate a live preview. You can pin the message and track your friends flying to you — hopefully time flies by quicker and you don't need to wait for your friends for too long.

![demo](webapp/demo.gif)

## Setup

```bash
cp .env.example .env   # set TELEGRAM_BOT_TOKEN + at least one flight API key
docker compose up -d --build
```

HTTPS required for Mini Apps — use [ngrok](https://ngrok.com) locally or Caddy/nginx in production.

## Structure

```
main.py          — entry point, runs bot + web server
bot.py           — telegram handlers (direct messages + inline queries)
flight_api.py    — caching, rate limiting, demo fallback
providers.py     — flight data provider interface + implementations
server.py        — aiohttp server, /api/flight endpoint + landing page
webapp/          — Mini App frontend (HTML/CSS/JS) + landing page
tests.py         — 49 tests
```

## Stack

Python, [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot), aiohttp, [FlightAware AeroAPI](https://www.flightaware.com/aeroapi/) (primary) / [AviationStack](https://aviationstack.com) (fallback), [airportsdata](https://github.com/mborsetti/airportsdata) for timezone lookups. No frontend framework — vanilla JS with Telegram Web App SDK. CI/CD via GitHub Actions → GHCR → Hetzner.

## License

MIT
