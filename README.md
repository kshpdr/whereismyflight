# Where Is My Flight

A Telegram bot that tracks flights and displays their status via a beautiful, iOS-widget-style Mini App.

Send a flight number → get a text summary + a **Live View** button that opens an interactive widget right inside Telegram.

## Quick Start

### 1. Prerequisites

- Python 3.10+
- A Telegram Bot token from [@BotFather](https://t.me/BotFather)
- (Optional) An [AviationStack](https://aviationstack.com) API key for live data — without one the bot runs in **demo mode** with realistic mock flights.

### 2. Install

```bash
cd whereismyflight
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env — at minimum set TELEGRAM_BOT_TOKEN
```

### 4. Expose HTTPS (required for Mini Apps)

Telegram Mini Apps must be served over HTTPS. During development use [ngrok](https://ngrok.com):

```bash
ngrok http 8080
```

Copy the `https://…ngrok-free.app` URL into `WEBAPP_BASE_URL` in your `.env`.

### 5. Run

```bash
python main.py
```

Open your bot in Telegram, send a flight number (e.g. `DL 404`), and tap **Live View**.

## Project Structure

```
main.py            ← entry point (bot + web server)
bot.py             ← Telegram message handlers
server.py          ← aiohttp web server & /api/flight endpoint
flight_api.py      ← AviationStack client + demo data generator
webapp/
  index.html       ← Mini App HTML
  style.css        ← iOS-widget styling (glassmorphism, dark mode)
  app.js           ← client-side data fetching & rendering
```

## How It Works

1. User sends a flight number (e.g. "DL 404", "United 100").
2. `bot.py` parses the message → calls `flight_api.py` for data.
3. Bot replies with a text summary and an inline **Live View** button.
4. Tapping the button opens the Mini App (`webapp/`) which fetches flight data from `/api/flight/<code>` and renders the widget.
5. The Mini App auto-refreshes every 60 seconds.

## License

MIT
