import requests
print("Your Railway Public IP is:", requests.get("https://api.ipify.org").text)
import requests
import time
import datetime
import sys
import hmac
import hashlib
import os
from urllib.parse import urlencode

# Read secrets from environment variables (NO config.py)
try:
    TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
    TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
    BINANCE_API_KEY = os.environ["BINANCE_API_KEY"]
    BINANCE_API_SECRET = os.environ["BINANCE_API_SECRET"]
except KeyError:
    print("❗ Environment variables missing. Set them in Railway project settings.")
    sys.exit(1)

CHECK_INTERVAL = 1800  # 30 minutes

def send_telegram_alert(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, json=payload)
        resp_json = response.json()
        if response.status_code == 200 and resp_json.get("ok"):
            print("[TELEGRAM] Alert sent!")
        else:
            print(f"[TELEGRAM] Failed! Response: {resp_json}")
    except Exception as e:
        print(f"Failed to send Telegram alert: {e}")

def get_funding_rates():
    try:
        url = "https://fapi.binance.com/fapi/v1/premiumIndex"
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error getting funding rates: {e}")
        return []

def get_exchange_info():
    try:
        url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error getting exchange info: {e}")
        return {}

def get_valid_futures_symbols(exchange_info):
    return set(
        s['symbol']
        for s in exchange_info.get('symbols', [])
        if s.get('contractType') == 'PERPETUAL' and s.get('quoteAsset') == 'USDT' and s.get('status') == 'TRADING'
    )

def get_max_leverage(symbol):
    """Fetch max leverage for a symbol using a SIGNED request (API key and secret required)"""
    try:
        base_url = "https://fapi.binance.com"
        endpoint = "/fapi/v1/leverageBracket"
        timestamp = int(time.time() * 1000)
        query = {
            "symbol": symbol,
            "timestamp": timestamp,
        }
        query_string = urlencode(query)
        signature = hmac.new(
            BINANCE_API_SECRET.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        query["signature"] = signature
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        response = requests.get(f"{base_url}{endpoint}", params=query, headers=headers)
        if response.status_code == 200:
            return int(response.json()[0]['brackets'][0]['initialLeverage'])
        else:
            print(f"  [WARN] {symbol}: leverageBracket not available ({response.status_code}). Skipping.")
            return None
    except Exception as e:
        print(f"  [SKIP] {symbol}: Could not determine max leverage. Error: {e}")
        return None

def main():
    started_msg = "🤖 Binance Funding Rate Alert Bot started!\nChecking every 30 minutes."
    stopped_msg = "🛑 Binance Funding Rate Alert Bot stopped."
    send_telegram_alert(started_msg)
    print(started_msg)

    already_alerted = set()
    exchange_info = get_exchange_info()
    valid_symbols = get_valid_futures_symbols(exchange_info)
    print(f"Loaded {len(valid_symbols)} valid USDT perpetual futures symbols.")

    try:
        while True:
            current_time = datetime.datetime.now(datetime.timezone.utc)
            print(f"\n⏰ Check started at {current_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")

            funding_data = get_funding_rates()
            if not funding_data:
                print("No funding data received. Waiting for next check...")
                send_telegram_alert("❗ No funding data received from Binance this check.")
                time.sleep(CHECK_INTERVAL)
                continue

            alert_triggered = False

            for coin_data in funding_data:
                symbol = coin_data['symbol']
                if symbol not in valid_symbols:
                    continue

                try:
                    funding_rate = float(coin_data['lastFundingRate']) * 100
                    next_funding_time = coin_data['nextFundingTime'] / 1000
                    next_funding = datetime.datetime.fromtimestamp(next_funding_time, datetime.timezone.utc)
                    time_left = next_funding - current_time
                    minutes_left = max(0, int(time_left.total_seconds() / 60))

                    if minutes_left < 30:
                        print(f"  {symbol}: Skipping (only {minutes_left} min left)")
                        continue

                    max_leverage = get_max_leverage(symbol)
                    if not max_leverage:
                        continue

                    alert_value = funding_rate * max_leverage
                    print(f"  {symbol}: {funding_rate:.4f}% * {max_leverage}x = {alert_value:.2f} | {minutes_left} min left")

                    # Notify if alert_value > 100 or < -100
                    if abs(alert_value) > 100:
                        alert_id = f"{symbol}-{next_funding_time}"
                        if alert_id not in already_alerted:
                            message = (
                                f"🚨 <b>ALERT: {symbol}</b> 🚨\n"
                                f"▫️ Funding Rate: <b>{funding_rate:.4f}%</b>\n"
                                f"▫️ Max Leverage: <b>{max_leverage}x</b>\n"
                                f"▫️ Value: <b>{alert_value:.2f}</b>\n"
                                f"▫️ Funding in: <b>{minutes_left} minutes</b>\n"
                                f"▫️ Next window: {next_funding.strftime('%H:%M:%S %Z')}\n\n"
                                "<i>Trade Setup Time: ~50 minutes remaining</i>"
                            )
                            send_telegram_alert(message)
                            already_alerted.add(alert_id)
                            alert_triggered = True
                            print(f"  🔔 ALERT SENT FOR {symbol}")

                except KeyError as e:
                    print(f"  Error processing {symbol}: Missing data field {e}")
                except Exception as e:
                    print(f"  Error processing {symbol}: {e}")

            if not alert_triggered:
                print("No alerts triggered this check")
                send_telegram_alert("❕ No lead found this check.")

            print(f"Next check in {CHECK_INTERVAL//60} minutes...")
            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print("\n🛑 Script stopped by user")
        send_telegram_alert(stopped_msg)
    except Exception as e:
        print(f"Unexpected error: {e}")
        send_telegram_alert("‼️ Bot crashed due to unexpected error. Restarting in 60 seconds...")
        time.sleep(60)
        main()

if __name__ == "__main__":
    main()