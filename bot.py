import requests
import time
import os

TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

message_id = None

def get_price():
    url = "https://api.coingecko.com/api/v3/simple/price?ids=litentry&vs_currencies=usd&include_24hr_change=true"
    data = requests.get(url).json()

    price = data["litentry"]["usd"]
    change = data["litentry"]["usd_24h_change"]

    return price, change


def send_message(text):
    global message_id

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    r = requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text
    }).json()

    message_id = r["result"]["message_id"]


def edit_message(text):

    url = f"https://api.telegram.org/bot{TOKEN}/editMessageText"

    requests.post(url, data={
        "chat_id": CHAT_ID,
        "message_id": message_id,
        "text": text
    })


while True:

    price, change = get_price()

    text = f"${price:.3f} • 24h: {change:.2f}%"

    if message_id is None:
        send_message(text)
    else:
        edit_message(text)

    time.sleep(5)

    send_message(message)

    time.sleep(60)

