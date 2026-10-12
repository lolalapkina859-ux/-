import os
import time
import logging
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
from scanner import detect_sweep, format_signal
from tracker import init_db, save_signal, check_open_trades

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("liquidity-hunter")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SCAN_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))

# Все инструменты на фьючерсах BingX (крипта, металлы, форекс)
SYMBOLS = {
    "BTC / USDT": {"ticker": "BTC-USDT"},
    "ETH / USDT": {"ticker": "ETH-USDT"},
    "SOL / USDT": {"ticker": "SOL-USDT"},
    "XAU / USD": {"ticker": "XAU-USDT"},     # Золото на фьючерсах BingX
    "EUR / USD": {"ticker": "EURUSD-USDT"}, # Форекс на фьючерсах BingX (USD₃-M)
    "GBP / USD": {"ticker": "GBPUSD-USDT"},
    "USD / JPY": {"ticker": "USDJPY-USDT"},
    "USD / CAD": {"ticker": "USDCAD-USDT"},
    "NZD / USD": {"ticker": "NZDUSD-USDT"},
    "AUD / USD": {"ticker": "AUDUSD-USDT"},
}

last_alerts = set()
symbol_cooldown = {}
COOLDOWN_SECONDS = 10800  # 3 часа перерыва для символа


def telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        response = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=15)
        response.raise_for_status()
    except Exception:
        log.exception("Telegram delivery failed")


def fetch_bingx_bars(symbol, interval_str):
    """Получение свечей с публичного API BingX Futures для любых инструментов"""
    interval_map = {"1d": "d", "4h": "240", "1h": "60"}
    bx_interval = interval_map.get(interval_str, "60")
    
    url = f"https://open-api.bingx.com/openApi/swap/v2/quote/klines?symbol={symbol}&interval={bx_interval}&limit=100"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    
    if data.get("code") != 0 or not data.get("data"):
        raise RuntimeError(f"BingX API error for {symbol}: {data}")
        
    bars = []
    for row in data["data"]:
        bars.append({
            "timestamp": int(row["time"] / 1000),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        })
    bars.sort(key=lambda b: b["timestamp"])
    return bars


def scan_once():
    log.info("--- Starting scan cycle (100% BingX Futures) ---")
    current_time = time.time()
    current_prices = {}

    for display_symbol, info in SYMBOLS.items():
        try:
            last_alert_time = symbol_cooldown.get(display_symbol, 0)
            ticker = info["ticker"]

            if current_time - last_alert_time < COOLDOWN_SECONDS:
                log.info("%s is in cooldown period. Skipping scan...", display_symbol)
                bars = fetch_bingx_bars(ticker, "1h")
                if bars:
                    current_prices[display_symbol] = {"high": bars[-1]["high"], "low": bars[-1]["low"], "close": bars[-1]["close"]}
                continue

            log.info("Fetching multi-TF bars for %s from BingX...", display_symbol)
            
            d1 = fetch_bingx_bars(ticker, "1d")
            time.sleep(0.3)
            h4 = fetch_bingx_bars(ticker, "4h")
            time.sleep(0.3)
            h1 = fetch_bingx_bars(ticker, "1h")
            
            if h1:
                current_prices[display_symbol] = {"high": h1[-1]["high"], "low": h1[-1]["low"], "close": h1[-1]["close"]}

            event = detect_sweep(d1, h4, h1)
            if not event:
                log.info("%s: no liquidity sweep setup found", display_symbol)
                time.sleep(0.5)
                continue

            key = f"{display_symbol}:{event['tf']}:{event['direction']}:{event['level']:.8f}:{event['bar_time']}"
            if key in last_alerts:
                log.info("%s: sweep found, but alert already sent", display_symbol)
                time.sleep(0.5)
                continue
            
            last_alerts.add(key)
            symbol_cooldown[display_symbol] = current_time

            telegram(format_signal(display_symbol, event))

            save_signal(
                symbol=display_symbol,
                tf=event['tf'],
                direction=event['direction'],
                entry_price=event['level'],
                stop_loss=event['stop_loss'],
                target=event['target'],
                rr_ratio=event['rr_ratio']
            )

            log.info("BINGX 100% FUTURES ALERT SENT & SAVED for %s (%s)!", display_symbol, event['tf'])
            time.sleep(1)
        except Exception as exc:
            log.warning("%s: scan failed: %s", display_symbol, exc)
            time.sleep(0.5)

    if current_prices:
        check_open_trades(current_prices, telegram)

    log.info("--- Scan cycle finished ---")


def main():
    if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
        raise SystemExit("Missing Telegram env vars.")
    
    init_db()
    log.info("Database initialized successfully.")
    
    while True:
        scan_once()
        time.sleep(SCAN_SECONDS)


if __name__ == "__main__":
    main()
