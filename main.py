import os
import time
import logging
import requests
from dotenv import load_dotenv
from scanner import detect_sweep, format_signal

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("liquidity-hunter")

API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SCAN_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))
BASE_URL = "https://api.twelvedata.com/time_series"

# Provider symbols are not guaranteed to match FxPro CFD quotes exactly.
SYMBOLS = {
    "BTCUSD": os.getenv("TD_SYMBOL_BTCUSD", "BTC/USD"),
    "XAUUSD": os.getenv("TD_SYMBOL_XAUUSD", "XAU/USD"),
    "EURUSD": os.getenv("TD_SYMBOL_EURUSD", "EUR/USD"),
}
last_alerts = set()


def telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        response = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=15)
        response.raise_for_status()
    except Exception:
        log.exception("Telegram delivery failed")


def fetch_bars(symbol, interval, outputsize=100):
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "timezone": "UTC",
        "order": "asc",
        "apikey": API_KEY,
    }
    response = requests.get(BASE_URL, params=params, timeout=20)
    response.raise_for_status()
    payload = response.json()

    if "values" not in payload:
        raise RuntimeError(f"{symbol} {interval}: provider response: {payload.get('message', payload)}")

    bars = []
    for row in payload["values"]:
        from datetime import datetime, timezone
        dt = datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        bars.append({
            "timestamp": int(dt.timestamp()),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        })
    bars.sort(key=lambda bar: bar["timestamp"])
    return bars


def scan_once():
    for canonical, provider_symbol in SYMBOLS.items():
        try:
            h1 = fetch_bars(provider_symbol, "1h", 100)
            m5 = fetch_bars(provider_symbol, "5min", 100)
            event = detect_sweep(h1, m5)
            if not event:
                log.info("%s: no confirmed setup", canonical)
                continue

            key = f"{canonical}:{event['direction']}:{event['level']:.8f}:{event['bar_time']}"
            if key in last_alerts:
                continue
            last_alerts.add(key)
            if len(last_alerts) > 1000:
                last_alerts.clear()
                last_alerts.add(key)

            telegram(format_signal(canonical, event) +
                     "\n\nData: Twelve Data (not a guaranteed exact FxPro quote).")
            log.info("%s: alert sent", canonical)
        except Exception as exc:
            log.warning("%s (%s): data scan failed: %s", canonical, provider_symbol, exc)


def main():
    if not all([API_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
        raise SystemExit("Missing env vars. Copy .env.example and fill required values.")
    telegram("Liquidity Hunter FX started.\nData source: Twelve Data.\nSymbols: BTCUSD, XAUUSD, EURUSD.\nAlerts only; no orders.")
    while True:
        scan_once()
        time.sleep(SCAN_SECONDS)


if __name__ == "__main__":
    main()
