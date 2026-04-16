# whereismyflight - telegram flight previews

A good friend of mine Alex complained about absence of native flight previews in Telegram compared to iMessages. But we don't want to use iMessage. So have to fix this problem ourselves.

This bot allows you to type in inline in any chat your flight number and generate a live preview. You can pin the message and track your friends flying to you — hopefully time flies by quicker and you don't need to wait for your friends for too long.

![](preview.png)

## Setup

```bash
cp .env.example .env   # set TELEGRAM_BOT_TOKEN + AVIATIONSTACK_API_KEY
docker compose up -d --build
```

HTTPS required for Mini Apps — use [ngrok](https://ngrok.com) locally or Caddy/nginx in production.

## Structure

```
main.py          — entry point, runs bot + web server
bot.py           — telegram handlers (direct messages + inline queries)
flight_api.py    — AviationStack client, timezone correction via airportsdata
server.py        — aiohttp server, /api/flight endpoint
webapp/          — Mini App frontend (HTML/CSS/JS)
```

## Stack

Python, [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot), aiohttp, [AviationStack API](https://aviationstack.com), [airportsdata](https://github.com/mborsetti/airportsdata) for timezone lookups. No frontend framework — vanilla JS with Telegram Web App SDK.

## License

MIT
