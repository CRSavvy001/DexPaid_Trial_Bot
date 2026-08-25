"""
DexPaid Watcher — a personal Telegram bot.

Paste a Solana contract address into the chat and the bot will keep
checking DexScreener in the background. As soon as "Enhanced Token Info"
(a.k.a. "Dex paid") is approved for that token, it messages you.

Built for solo use: only the OWNER_CHAT_ID you configure can control it.
"""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]  # required
OWNER_CHAT_ID = os.environ.get("OWNER_CHAT_ID")  # set after first /start
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))
STALE_AFTER_MINUTES = float(os.environ.get("STALE_AFTER_MINUTES", "2880"))  # 2880 min = 48h default

DATA_FILE = Path(os.environ.get("DATA_FILE", "watchlist.json"))

DEXSCREENER_ORDERS_URL = "https://api.dexscreener.com/orders/v1/solana/{address}"
DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"

# Base58 Solana addresses are 32-44 chars, no 0/O/I/l
SOLANA_ADDRESS_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("dexpaid-bot")


# --------------------------------------------------------------------------
# Persistence — tiny JSON file, good enough for solo use
# --------------------------------------------------------------------------

def load_watchlist() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except json.JSONDecodeError:
            log.warning("watchlist.json was corrupt, starting fresh")
    return {}


def save_watchlist(watchlist: dict) -> None:
    DATA_FILE.write_text(json.dumps(watchlist, indent=2))


# --------------------------------------------------------------------------
# DexScreener calls
# --------------------------------------------------------------------------

async def check_paid_status(client: httpx.AsyncClient, address: str) -> bool:
    """Returns True if an 'approved' order exists for this token (Dex paid)."""
    url = DEXSCREENER_ORDERS_URL.format(address=address)
    resp = await client.get(url, timeout=15)
    if resp.status_code != 200:
        log.warning("orders check failed for %s: HTTP %s", address, resp.status_code)
        return False

    try:
        data = resp.json()
    except ValueError:
        log.warning(
            "orders check for %s returned non-JSON body: %s",
            address, resp.text[:300],
        )
        return False

    # DexScreener's live API wraps orders in a dict: {"orders": [...], "boosts": {...}}
    # even though older docs described a bare list. Support both shapes defensively.
    if isinstance(data, dict):
        orders = data.get("orders", [])
    elif isinstance(data, list):
        orders = data
    else:
        log.warning(
            "orders check for %s returned unexpected shape (%s): %s",
            address, type(data).__name__, str(data)[:300],
        )
        return False

    if not isinstance(orders, list):
        log.warning(
            "orders check for %s: 'orders' field was not a list (%s): %s",
            address, type(orders).__name__, str(orders)[:300],
        )
        return False

    approved = False
    for o in orders:
        if not isinstance(o, dict):
            log.warning(
                "orders check for %s: skipping non-dict order item (%s): %r",
                address, type(o).__name__, o,
            )
            continue
        if o.get("status") == "approved":
            approved = True

    return approved


async def get_token_label(client: httpx.AsyncClient, address: str) -> str:
    """Best-effort token name/symbol for nicer messages. Falls back to the address."""
    url = DEXSCREENER_TOKEN_URL.format(address=address)
    try:
        resp = await client.get(url, timeout=15)
        if resp.status_code == 200:
            pairs = resp.json().get("pairs") or []
            if pairs:
                base = pairs[0].get("baseToken", {})
                name = base.get("name")
                symbol = base.get("symbol")
                if name and symbol:
                    return f"{name} ({symbol})"
    except Exception:  # noqa: BLE001 — labeling is best-effort, never fatal
        pass
    return address


# --------------------------------------------------------------------------
# Access control — only you can use this bot
# --------------------------------------------------------------------------

def is_owner(update: Update) -> bool:
    if OWNER_CHAT_ID is None:
        # Not locked down yet — see /start
        return True
    return str(update.effective_chat.id) == str(OWNER_CHAT_ID)


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if OWNER_CHAT_ID is None:
        await update.message.reply_text(
            "Bot is not locked down yet.\n\n"
            f"Your chat ID is: `{chat_id}`\n\n"
            "Set this as the OWNER_CHAT_ID environment variable on Railway "
            "and redeploy so only you can use this bot.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    if not is_owner(update):
        return
    await update.message.reply_text(
        "Paste a Solana contract address and I'll watch it.\n"
        "I'll message you the moment DexScreener shows it as paid.\n\n"
        "Commands:\n"
        "/list — tokens currently being watched\n"
        "/stop <address> — stop watching a token"
    )


async def list_watched(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    watchlist = context.bot_data["watchlist"]
    if not watchlist:
        await update.message.reply_text("Not watching anything right now.")
        return
    lines = []
    for i, (address, info) in enumerate(watchlist.items(), start=1):
        age_hours = (time.time() - info["added_at"]) / 3600
        lines.append(f"{i}. {info.get('label', address)}\n   `{address}` — watching {age_hours:.1f}h")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def stop_watching(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /stop <contract address>")
        return
    address = context.args[0]
    watchlist = context.bot_data["watchlist"]
    if address in watchlist:
        del watchlist[address]
        save_watchlist(watchlist)
        await update.message.reply_text(f"Stopped watching `{address}`", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("That address isn't on the watchlist.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return

    text = update.message.text or ""
    matches = SOLANA_ADDRESS_RE.findall(text)
    if not matches:
        return

    watchlist = context.bot_data["watchlist"]
    client: httpx.AsyncClient = context.bot_data["http_client"]

    for address in matches:
        if address in watchlist:
            await update.message.reply_text(f"Already watching `{address}`", parse_mode=ParseMode.MARKDOWN)
            continue

        label = await get_token_label(client, address)
        watchlist[address] = {"added_at": time.time(), "label": label}
        save_watchlist(watchlist)

        await update.message.reply_text(
            f"Watching *{label}*\n`{address}`\n\nI'll ping you the moment it's Dex paid.",
            parse_mode=ParseMode.MARKDOWN,
        )


# --------------------------------------------------------------------------
# Background polling job
# --------------------------------------------------------------------------

async def poll_watchlist(context: ContextTypes.DEFAULT_TYPE) -> None:
    watchlist: dict = context.bot_data["watchlist"]
    client: httpx.AsyncClient = context.bot_data["http_client"]

    if not watchlist:
        return

    stale_cutoff = time.time() - STALE_AFTER_MINUTES * 60
    to_remove = []

    for address, info in list(watchlist.items()):
        if info["added_at"] < stale_cutoff:
            to_remove.append(address)
            continue

        try:
            paid = await check_paid_status(client, address)
        except Exception as exc:  # noqa: BLE001 — never let one bad check kill the loop
            log.warning("check failed for %s: %s", address, exc)
            continue

        if paid:
            label = info.get("label", address)
            await context.bot.send_message(
                chat_id=OWNER_CHAT_ID,
                text=f"✅ *Dex paid!*\n\n*{label}*\n`{address}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            to_remove.append(address)

        # Stay well under DexScreener's 60 req/min limit even with a large list
        await asyncio.sleep(1)

    for address in to_remove:
        watchlist.pop(address, None)

    if to_remove:
        save_watchlist(watchlist)


# --------------------------------------------------------------------------
# App wiring
# --------------------------------------------------------------------------

async def post_init(application: Application) -> None:
    application.bot_data["watchlist"] = load_watchlist()
    application.bot_data["http_client"] = httpx.AsyncClient()
    log.info("Loaded %d watched token(s) from disk", len(application.bot_data["watchlist"]))


async def post_shutdown(application: Application) -> None:
    client: httpx.AsyncClient = application.bot_data.get("http_client")
    if client:
        await client.aclose()


def main() -> None:
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("list", list_watched))
    application.add_handler(CommandHandler("stop", stop_watching))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.job_queue.run_repeating(poll_watchlist, interval=POLL_INTERVAL_SECONDS, first=10)

    log.info("Starting bot (poll interval: %ss)", POLL_INTERVAL_SECONDS)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
