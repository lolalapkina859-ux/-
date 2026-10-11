import os
import time
import logging
import requests
from datetime import datetime, timezone
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

# Возвращаем 3 ключевых инструмента, чтобы укладываться в лимиты бесплатного API
SYMBOLS = {
    "BTC / USDT": "BTC/USD",
    "XAU / USD": "XAU/USD",
    "EUR / USD": "EUR/USD",
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
    log.info("--- Starting scan cycle ---")
    for display_symbol, provider_symbol in SYMBOLS.items():
        try:
            log.info("Fetching bars for %s...", display_symbol)
            h1 = fetch_bars(provider_symbol, "1h", 100)
            time.sleep(8)  # Пауза между запросами, чтобы не превысить лимит API (429 Too Many Requests)
            m5 = fetch_bars(provider_symbol, "5min", 100)
            
            last_h1 = h1[-1] if h1 else None
            if last_h1:
                dt_str = datetime.fromtimestamp(last_h1["timestamp"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                log.info("%s fetched OK. Last H1 close: %s at %s", display_symbol, last_h1["close"], dt_str)

            event = detect_sweep(h1, m5)
            if not event:
                log.info("%s: no liquidity sweep setup found", display_symbol)
                time.sleep(8)
                continue

            key = f"{display_symbol}:{event['direction']}:{event['level']:.8f}:{event['bar_time']}"
            if key in last_alerts:
                log.info("%s: sweep found, but alert already sent", display_symbol)
                time.sleep(8)
                continue
            
            last_alerts.add(key)
            if len(last_alerts) > 1000:
                last_alerts.clear()
                last_alerts.add(key)

            telegram(format_signal(display_symbol, event))
            log.info("ALERT SENT for %s!", display_symbol)
            time.sleep(8)
        except Exception as exc:
            log.warning("%s: scan failed: %s", display_symbol, exc)
            time.sleep(10)
    log.info("--- Scan cycle finished ---")


def main():
    if not all([API_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
        raise SystemExit("Missing env vars.")
    telegram("Liquidity Hunter FX started.\nScanning pairs: BTC / USDT, XAU / USD, EUR / USD.")
    while True:
        scan_once()
        time.sleep(SCAN_SECONDS)


if __name__ == "__main__":
    main()
