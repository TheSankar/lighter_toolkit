import requests
import time

TOKEN = "YOUR_BOT_TOKEN"
CHAT_ID = "-100XXXXXXXX"

def get_price():
    url = "https://api.coingecko.com/api/v3/simple/price?ids=litentry&vs_currencies=usd&include_24hr_change=true"
    data = requests.get(url).json()
    price = data["litentry"]["usd"]
    change = data["litentry"]["usd_24h_change"]
    return price, change

def send_message(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text
    })

while True:
    price, change = get_price()

    message = f"""
LIT price / Lighter price

${price}

24h change: {round(change,2)}%
"""

    send_message(message)

    time.sleep(60)

