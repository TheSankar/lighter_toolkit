import asyncio
import json
import logging
import requests
import websockets
from datetime import datetime, timezone

# ─────────────────────────────────────────────────────────
# ENVIRONMENT VARIABLES — set these in Railway dashboard
# ─────────────────────────────────────────────────────────
import os
BOT_TOKEN  = os.environ.get("BOT_TOKEN",  "YOUR_BOT_TOKEN_HERE")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "YOUR_CHANNEL_ID_HERE")

# ─────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────
# Lighter market IDs  (confirmed from /api/v1/orderBookDetails)
MARKETS = {
    1:   "BTC",
    120: "LIT",
}

UPDATE_INTERVAL_SECONDS = 5     # how often to edit the message
WS_URL = "wss://mainnet.zklighter.elliot.ai/stream?readonly=true"
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ─────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────────────────
market_data: dict[str, dict] = {}   # "BTC" → {"price": 69102.24, "change": -1.23}
pinned_msg_id: int | None = None


# ─────────────────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────────────────
def tg(method: str, payload: dict) -> dict | None:
    try:
        r = requests.post(f"{TG_API}/{method}", json=payload, timeout=10)
        body = r.json()
        if not body.get("ok"):
            desc = body.get("description", "")
            if "not modified" not in desc.lower():
                log.warning(f"TG {method}: {desc}")
        return body
    except Exception as e:
        log.error(f"TG request error ({method}): {e}")
        return None


def send_msg(text: str) -> int | None:
    res = tg("sendMessage", {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })
    if res and res.get("ok"):
        return res["result"]["message_id"]
    return None


def edit_msg(msg_id: int, text: str) -> bool:
    res = tg("editMessageText", {
        "chat_id": CHANNEL_ID,
        "message_id": msg_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })
    if res is None:
        return False
    if not res.get("ok") and "not modified" in res.get("description", "").lower():
        return True
    return bool(res.get("ok"))


# ─────────────────────────────────────────────────────────
# MESSAGE BUILDER
# ─────────────────────────────────────────────────────────
def fmt_price(symbol: str, price: float) -> str:
    if price >= 1_000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:,.3f}"
    else:
        return f"${price:,.4f}"


def build_message() -> str:
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    lines = [f"⚡ <b>Lighter Prices</b>  <code>{now}</code>\n"]

    for symbol in ["BTC", "LIT"]:
        d = market_data.get(symbol)
        if not d:
            lines.append(f"<b>{symbol}</b>  —  loading…")
            continue

        price = d["price"]
        chg   = d.get("change")

        price_str = fmt_price(symbol, price)

        if chg is not None:
            arrow   = "🔺" if chg >= 0 else "🔻"
            sign    = "+" if chg >= 0 else ""
            chg_str = f"{arrow} {sign}{chg:.2f}%"
        else:
            chg_str = "—"

        lines.append(f"<b>{symbol}</b>  —  {price_str}  •  24h: {chg_str}")

    lines.append(f"\n<i>Lighter.xyz perps  ·  live every {UPDATE_INTERVAL_SECONDS}s</i>")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
# WEBSOCKET LISTENER
# ─────────────────────────────────────────────────────────
def handle(raw: str):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    if data.get("type") != "update/market_stats":
        return

    stats  = data.get("market_stats", {})
    symbol = stats.get("symbol", "").upper()

    if symbol not in ("BTC", "LIT"):
        return

    raw_price = stats.get("mark_price") or stats.get("last_trade_price")
    if not raw_price:
        return

    try:
        price = float(raw_price)
        chg   = float(stats["daily_price_change"]) if stats.get("daily_price_change") is not None else None
    except (ValueError, TypeError):
        return

    market_data[symbol] = {"price": price, "change": chg}


async def ws_loop():
    delay = 5
    while True:
        try:
            log.info("Connecting to Lighter WebSocket…")
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10) as ws:
                log.info("Connected ✅")
                delay = 5
                for market_id in MARKETS:
                    await ws.send(json.dumps({
                        "type": "subscribe",
                        "channel": f"market_stats/{market_id}"
                    }))
                    log.info(f"  → subscribed market_stats/{market_id} ({MARKETS[market_id]})")
                async for raw in ws:
                    handle(raw)
        except (websockets.exceptions.ConnectionClosed,
                websockets.exceptions.WebSocketException, OSError) as e:
            log.warning(f"WS disconnected: {e} — retry in {delay}s")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
        except Exception as e:
            log.error(f"WS error: {e} — retry in {delay}s")
            await asyncio.sleep(delay)


# ─────────────────────────────────────────────────────────
# TICKER LOOP  (posts + edits message every N seconds)
# ─────────────────────────────────────────────────────────
async def ticker_loop():
    global pinned_msg_id

    log.info("Waiting for first price data…")
    for _ in range(30):
        if len(market_data) >= 2:
            break
        await asyncio.sleep(1)

    if not market_data:
        log.error("No data received after 30s — check WS / market IDs")
        return

    pinned_msg_id = send_msg(build_message())
    if pinned_msg_id:
        log.info(f"Ticker posted (msg_id={pinned_msg_id}) ✅")
    else:
        log.error("Failed to post initial message")
        return

    while True:
        await asyncio.sleep(UPDATE_INTERVAL_SECONDS)
        ok = edit_msg(pinned_msg_id, build_message())
        if not ok:
            log.warning("Edit failed — reposting")
            pinned_msg_id = send_msg(build_message())


# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────
async def main():
    log.info("━" * 44)
    log.info("  Lighter Live Ticker Bot  (BTC + LIT)")
    log.info("━" * 44)

    if "YOUR_" in BOT_TOKEN or "YOUR_" in CHANNEL_ID:
        log.error("❌  Set BOT_TOKEN and CHANNEL_ID env vars before running!")
        return

    await asyncio.gather(ws_loop(), ticker_loop())


if __name__ == "__main__":
    asyncio.run(main())
