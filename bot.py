import time
import requests
import pandas as pd

BOT_TOKEN = "8694365620:AAEpimRdaZ0C8aG-jEZgeM8zaLq3ZTwIXSE"
CHAT_ID = "601425989"

SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT"]

CHECK_INTERVAL = 300

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    params = {"chat_id": CHAT_ID, "text": text}
    requests.get(url, params=params)

def get_price(symbol):
    url = "https://api.binance.com/api/v3/ticker/price"
    params = {"symbol": symbol}
    r = requests.get(url, params=params)
    return float(r.json()["price"])

def check_market():
    for symbol in SYMBOLS:
        price = get_price(symbol)
        print(symbol, price)

def run_bot():

    send_telegram("Бот работает 24/7 🚀")

    while True:

        try:
            check_market()
        except Exception as e:
            print("Ошибка:", e)

        time.sleep(CHECK_INTERVAL)

run_bot()
