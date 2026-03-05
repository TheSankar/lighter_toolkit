import requests
import time
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

UPDATE_INTERVAL = 5

LIGHTER_API = "https://mainnet.zklighter.elliot.ai/api/v1/markets"


def get_prices():
    try:
        url = "https://mainnet.zklighter.elliot.ai/api/v1/markets"

        r = requests.get(url, timeout=10)
        print(r.text)

        if r.status_code != 200:
            print("Bad API status:", r.status_code)
            return None, None, None, None

        data = r.json()

        btc_price = None
        btc_change = None
        lit_price = None
        lit_change = None

        for market in data.get("markets", []):

            if market["symbol"] == "BTC-USD":
                btc_price = float(market["markPrice"])
                btc_change = float(market["priceChangePercent24h"])

            if market["symbol"] == "LIT-USD":
                lit_price = float(market["markPrice"])
                lit_change = float(market["priceChangePercent24h"])

        return btc_price, btc_change, lit_price, lit_change

    except Exception as e:
        print("API Error:", e)
        return None, None, None, None

        for market in data["markets"]:

            if market["symbol"] == "BTC-USD":
                btc_price = float(market["markPrice"])
                btc_change = float(market["priceChangePercent24h"])

            if market["symbol"] == "LIT-USD":
                lit_price = float(market["markPrice"])
                lit_change = float(market["priceChangePercent24h"])

        return btc_price, btc_change, lit_price, lit_change

    except Exception as e:
        print("API Error:", e)
        return None, None, None, None


def format_price(symbol, price, change):

    if price is None:
        return f"{symbol} - loading..."

    arrow = "🔺" if change >= 0 else "🔻"

    return f"{symbol} - ${price:,.2f} • 24h: {arrow} {change:.2f}%"


def send_message(text):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": text
    }

    r = requests.post(url, json=payload)

    return r.json()["result"]["message_id"]


def edit_message(message_id, text):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"

    payload = {
        "chat_id": CHAT_ID,
        "message_id": message_id,
        "text": text
    }

    requests.post(url, json=payload)


btc, btc_c, lit, lit_c = get_prices()

text = f"""
{format_price("BTC", btc, btc_c)}
{format_price("LIT", lit, lit_c)}
"""

message_id = send_message(text)


while True:

    btc, btc_c, lit, lit_c = get_prices()

    text = f"""
{format_price("BTC", btc, btc_c)}
{format_price("LIT", lit, lit_c)}
"""

    edit_message(message_id, text)

    time.sleep(UPDATE_INTERVAL)
