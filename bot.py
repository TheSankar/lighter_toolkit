import asyncio
import json
import logging
import os
import requests
import websockets

# ---------------------------------------------------------
# ENVIRONMENT VARIABLES — set these in Railway dashboard
# ---------------------------------------------------------
BOT_TOKEN  = os.environ.get("BOT_TOKEN",  "YOUR_BOT_TOKEN_HERE")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "YOUR_CHANNEL_ID_HERE")

# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------
# Price ticker markets (WebSocket)
MARKETS = {
    1:   "BTC",
    120: "LIT",
}

# Liquidation alert markets + their Lighter market IDs
LIQUIDATION_MARKETS = {
    "BTC": 1,
    "LIT": 120,
    "ETH": 0,
    "SOL": 2,
}

MIN_LIQUIDATION_USD  = 40_000   # only alert if liquidation >= $40k
LIQUIDATION_POLL_SEC = 15       # how often to check for new liquidations
UPDATE_INTERVAL_SECONDS = 15    # price message interval

REST_URL = "https://mainnet.zklighter.elliot.ai"
WS_URL   = "wss://mainnet.zklighter.elliot.ai/stream?readonly=true"
TG_API   = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ---------------------------------------------------------
# LOGGING
# ---------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------
# STATE
# ---------------------------------------------------------
market_data   = {}   # "BTC" -> {"price": 69102.24, "change": -1.23}
seen_liq_ids  = set()  # track already-alerted liquidation IDs


# ---------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------
def tg(method, payload):
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


def send_msg(text):
    res = tg("sendMessage", {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })
    if res and res.get("ok"):
        return res["result"]["message_id"]
    return None


# ---------------------------------------------------------
# PRICE MESSAGE BUILDER
# ---------------------------------------------------------
def fmt_price(symbol, price):
    if price >= 1000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:,.3f}"
    else:
        return f"${price:,.4f}"


def build_price_message():
    lines = []
    for symbol in ["BTC", "LIT"]:
        d = market_data.get(symbol)
        if not d:
            continue
        price_str = fmt_price(symbol, d["price"])
        lines.append(f"<b>{symbol}</b>  \u2014  {price_str}")
    return "\n".join(lines)


# ---------------------------------------------------------
# LIQUIDATION ALERTS
# ---------------------------------------------------------
def fetch_liquidations(market_id):
    try:
        r = requests.get(
            f"{REST_URL}/api/v1/liquidations",
            params={"market_id": market_id, "limit": 20},
            timeout=10,
        )
        return r.json().get("liquidations", [])
    except Exception as e:
        log.error(f"Liquidation fetch error (market {market_id}): {e}")
        return []


def build_liq_message(symbol, liq):
    # side: is_ask=True means they were short (ask) → Short Liquidation
    is_ask   = liq.get("is_ask", False)
    side     = "Short" if is_ask else "Long"
    dot      = "\U0001f7e2" if is_ask else "\U0001f534"   # green=short, red=long
    usd_amt  = float(liq.get("usd_amount", 0))
    price    = float(liq.get("price", 0))
    usd_str  = f"${usd_amt/1000:.1f}k" if usd_amt >= 1000 else f"${usd_amt:.0f}"
    px_str   = fmt_price(symbol, price)
    return f"{dot} <b>#{symbol}</b> {side} Liquidation: {usd_str} @ {px_str}"


async def liquidation_loop():
    global seen_liq_ids

    # Seed seen IDs on startup so we don't blast old liquidations
    log.info("Seeding liquidation history...")
    for symbol, market_id in LIQUIDATION_MARKETS.items():
        for liq in fetch_liquidations(market_id):
            seen_liq_ids.add(liq.get("liquidation_id") or liq.get("id"))
    log.info(f"Seeded {len(seen_liq_ids)} existing liquidations")

    while True:
        await asyncio.sleep(LIQUIDATION_POLL_SEC)
        for symbol, market_id in LIQUIDATION_MARKETS.items():
            liqs = fetch_liquidations(market_id)
            for liq in liqs:
                liq_id = liq.get("liquidation_id") or liq.get("id")
                if liq_id in seen_liq_ids:
                    continue
                seen_liq_ids.add(liq_id)

                usd_amt = float(liq.get("usd_amount", 0))
                if usd_amt < MIN_LIQUIDATION_USD:
                    continue

                msg = build_liq_message(symbol, liq)
                send_msg(msg)
                log.info(f"Liquidation alert sent: {symbol} ${usd_amt:,.0f}")


# ---------------------------------------------------------
# WEBSOCKET LISTENER (price data)
# ---------------------------------------------------------
def handle(raw):
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
    delay = 45
    while True:
        try:
            log.info("Connecting to Lighter WebSocket...")
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10) as ws:
                log.info("Connected")
                delay = 5
                for market_id in MARKETS:
                    await ws.send(json.dumps({
                        "type": "subscribe",
                        "channel": f"market_stats/{market_id}"
                    }))
                    log.info(f"  -> subscribed market_stats/{market_id} ({MARKETS[market_id]})")
                async for raw in ws:
                    handle(raw)
        except (websockets.exceptions.ConnectionClosed,
                websockets.exceptions.WebSocketException, OSError) as e:
            log.warning(f"WS disconnected: {e} - retry in {delay}s")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
        except Exception as e:
            log.error(f"WS error: {e} - retry in {delay}s")
            await asyncio.sleep(delay)


# ---------------------------------------------------------
# PRICE TICKER LOOP
# ---------------------------------------------------------
async def ticker_loop():
    log.info("Waiting for first price data...")
    for _ in range(30):
        if len(market_data) >= 2:
            break
        await asyncio.sleep(1)

    if not market_data:
        log.error("No data received after 30s - check WS / market IDs")
        return

    while True:
        msg = build_price_message()
        if msg:
            send_msg(msg)
        await asyncio.sleep(UPDATE_INTERVAL_SECONDS)


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
async def main():
    log.info("Lighter Bot  (Price: BTC+LIT  |  Liquidations: BTC+LIT+ETH+SOL)")

    if "YOUR_" in BOT_TOKEN or "YOUR_" in CHANNEL_ID:
        log.error("Set BOT_TOKEN and CHANNEL_ID env vars before running!")
        return

    await asyncio.gather(ws_loop(), ticker_loop(), liquidation_loop())


if __name__ == "__main__":
    asyncio.run(main())
