"""Telegram bot handlers — parses flight numbers and replies with summaries."""

from __future__ import annotations

import os
from datetime import datetime
from uuid import uuid4

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle,
    InputTextMessageContent, Update,
)
from telegram.ext import Application, CommandHandler, InlineQueryHandler, MessageHandler, filters, ContextTypes

from flight_api import parse_flight_number, fetch_flight

WEBAPP_BASE_URL: str = os.getenv("WEBAPP_BASE_URL", "")
MINIAPP_DIRECT_LINK: str = os.getenv("MINIAPP_DIRECT_LINK", "https://t.me/whereismyflightbot/whereismyflight")


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hey! Send me a flight number like *DL 404* or *United 1234* "
        "and I'll fetch the latest status for you.",
        parse_mode="Markdown",
    )


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if not text:
        return

    flight_code = parse_flight_number(text)
    if not flight_code:
        await update.message.reply_text(
            "I couldn't find a flight number in that message.\n"
            "Try something like *DL 404*, *UA 100*, or *Lufthansa 401*.",
            parse_mode="Markdown",
        )
        return

    data = await fetch_flight(flight_code)
    if not data:
        await update.message.reply_text(f"Sorry, I couldn't find any data for *{flight_code}*.", parse_mode="Markdown")
        return

    summary = _format_summary(data)

    webapp_url = f"{WEBAPP_BASE_URL}/webapp/index.html?flight={flight_code}"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✈️ Live View", web_app={"url": webapp_url})]
    ])

    await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=keyboard)


def _format_summary(d: dict) -> str:
    legs = d.get("legs", [])
    status = d["status"]

    status_emoji = {
        "Scheduled": "🕐",
        "In Air": "✈️",
        "Landed": "🛬",
        "Cancelled": "❌",
        "Diverted": "⚠️",
        "En Route": "✈️",
    }.get(status, "ℹ️")

    lines = [
        f"{status_emoji}  *{d['flight']}* — {d['airline']}",
        f"Status: *{status}*",
    ]

    if len(legs) > 1:
        route_parts = [legs[0]["departure"]["iata"]]
        for leg in legs:
            route_parts.append(leg["arrival"]["iata"])
        lines.append(f"Route: {' → '.join(route_parts)}")

    for i, leg in enumerate(legs):
        dep = leg["departure"]
        arr = leg["arrival"]

        if len(legs) > 1:
            lines.append(f"\n*Leg {i + 1}: {dep['iata']} → {arr['iata']}*  —  {leg['status']}")
        else:
            lines.append(f"\n🛫  {dep['iata']}  →  {arr['iata']}  🛬")

        dep_time = _short_time(dep.get("estimated") or dep.get("scheduled"))
        arr_time = _short_time(arr.get("estimated") or arr.get("scheduled"))

        delay_note = ""
        if dep.get("delay") and int(dep["delay"]) > 0:
            delay_note = f"  _(+{dep['delay']} min)_"

        lines.append(f"Departs: {dep_time}   T{dep.get('terminal', '–')} · Gate {dep.get('gate', '–')}{delay_note}")
        lines.append(f"Arrives:  {arr_time}   T{arr.get('terminal', '–')} · Gate {arr.get('gate', '–')}")

    if d.get("demo"):
        lines.append("\n_ℹ️ Demo data — add an AviationStack API key for live flights._")

    return "\n".join(lines)


async def handle_inline(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = (update.inline_query.query or "").strip()
    if not query:
        return

    flight_code = parse_flight_number(query)
    if not flight_code:
        return

    data = await fetch_flight(flight_code)
    if not data:
        await update.inline_query.answer([
            InlineQueryResultArticle(
                id=str(uuid4()),
                title=f"No data for {flight_code}",
                description="Flight not found",
                input_message_content=InputTextMessageContent(
                    f"Could not find flight *{flight_code}*.",
                    parse_mode="Markdown",
                ),
            )
        ], cache_time=30)
        return

    summary = _format_summary(data)
    legs = data.get("legs", [])
    status = data["status"]

    if len(legs) > 1:
        route_parts = [legs[0]["departure"]["iata"]]
        for leg in legs:
            route_parts.append(leg["arrival"]["iata"])
        description = f"{status} · {' → '.join(route_parts)}"
    elif legs:
        dep = legs[0]["departure"]
        arr = legs[0]["arrival"]
        dep_time = _short_time(dep.get("estimated") or dep.get("scheduled"))
        arr_time = _short_time(arr.get("estimated") or arr.get("scheduled"))
        description = f"{status} · {dep['iata']} {dep_time} → {arr['iata']} {arr_time}"
    else:
        description = status

    miniapp_url = f"{MINIAPP_DIRECT_LINK}?startapp={flight_code}"

    results = [
        InlineQueryResultArticle(
            id=str(uuid4()),
            title=f"✈️ {flight_code} — {data['airline']}",
            description=description,
            input_message_content=InputTextMessageContent(
                summary,
                parse_mode="Markdown",
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✈️ Live View", url=miniapp_url)]
            ]),
        )
    ]

    await update.inline_query.answer(results, cache_time=60)


def _short_time(iso: str | None) -> str:
    if not iso:
        return "–"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%H:%M")
    except Exception:
        return iso[:16]


def build_bot_app() -> Application:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(InlineQueryHandler(handle_inline))
    return app
