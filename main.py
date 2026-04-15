"""Entry point — starts the Telegram bot and the aiohttp web server side by side."""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from aiohttp import web

load_dotenv()

from bot import build_bot_app
from server import create_web_app

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("whereismyflight")


async def main() -> None:
    port = int(os.getenv("PORT", "8080"))
    base = os.getenv("WEBAPP_BASE_URL", "")

    log.info("Starting web server on :%d", port)
    webapp = create_web_app()
    runner = web.AppRunner(webapp)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    log.info("Web server ready — Mini App served at %s/webapp/index.html", base or f"http://localhost:{port}")

    bot_app = build_bot_app()
    log.info("Starting Telegram bot (polling)…")
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling()

    log.info("All systems go. Press Ctrl+C to stop.")

    stop = asyncio.Event()
    try:
        await stop.wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        log.info("Shutting down…")
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
