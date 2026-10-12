import os
import time
import logging
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
import yfinance as yf
from scanner import detect_sweep, format_signal
from tracker import init_db, save_signal, check_open_trades

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("liquidity-hunter")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SCAN_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))

SYMBOLS = {
    "BTC / USDT": {"source": "binance", "ticker": "BTCUSDT"},
    "ETH / USDT": {"source": "binance", "ticker": "ETHUSDT"},
    "SOL / USDT": {"source": "binance", "ticker": "SOLUSDT"},
    "XAU / USD": {"source": "yfinance", "ticker": "GC=F"},
    "EUR / USD": {"source": "yfinance", "ticker": "EURUSD=X"},
    "GBP / USD": {"source": "yfinance", "ticker": "GBPUSD=X"},
    "USD / JPY": {"source": "yfinance", "ticker": "USDJPY=X"},
    "USD / CAD": {"source": "yfinance", "ticker": "USDCAD=X"},
    "NZD / USD": {"source": "yfinance", "ticker": "NZDUSD=X"},
    "AUD / USD": {"source": "yfinance", "ticker": "AUDUSD=X"},
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


def fetch_binance_bars(symbol, interval_str):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval_str}&limit=100"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    raw_data = resp.json()
    bars = []
    for row in raw_data:
        bars.append({
            "timestamp": int(row[0] / 1000),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
        })
    bars.sort(key=lambda b: b["timestamp"])
    return bars


def fetch_yfinance_bars(ticker_str, interval_str):
    period = "60d" if interval_str in ["1h", "4h"] else "350d"
    yf_interval = "1d" if interval_str == "1d" else "1h"
    
    df = yf.download(ticker_str, period=period, interval=yf_interval, progress=False)
    if df.empty:
        raise RuntimeError(f"Empty data from yfinance for {ticker_str}")
    if hasattr(df.columns, 'levels') and len(df.columns.levels) > 1:
        df.columns = df.columns.get_level_values(0)
    bars = []
    for idx, row in df.iterrows():
        bars.append({
            "timestamp": int(idx.timestamp()),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
        })
    bars.sort(key=lambda b: b["timestamp"])
    return bars


def scan_once():
    log.info("--- Starting scan cycle (Multi-TF & Tracking) ---")
    current_time = time.time()
    current_prices = {}

    for display_symbol, info in SYMBOLS.items():
        try:
            last_alert_time = symbol_cooldown.get(display_symbol, 0)
            if current_time - last_alert_time < COOLDOWN_SECONDS:
                log.info("%s is in cooldown period. Skipping scan...", display_symbol)
                # Подтягиваем хотя бы текущую цену для проверки трекинга
                source = info["source"]
                ticker = info["ticker"]
                bars = fetch_binance_bars(ticker, "1h") if source == "binance" else fetch_yfinance_bars(ticker, "1h")
                if bars:
                    current_prices[display_symbol] = {"high": bars[-1]["high"], "low": bars[-1]["low"], "close": bars[-1]["close"]}
                continue

            log.info("Fetching multi-TF bars for %s...", display_symbol)
            source = info["source"]
            ticker = info["ticker"]
            
            if source == "binance":
                d1 = fetch_binance_bars(ticker, "1d")
                h4 = fetch_binance_bars(ticker, "4h")
                h1 = fetch_binance_bars(ticker, "1h")
            else:
                d1 = fetch_yfinance_bars(ticker, "1d")
                h4 = fetch_yfinance_bars(ticker, "1h")
                h1 = fetch_yfinance_bars(ticker, "1h")
            
            # Сохраняем последние котировки для трекера отработки
            if h1:
                current_prices[display_symbol] = {"high": h1[-1]["high"], "low": h1[-1]["low"], "close": h1[-1]["close"]}

            event = detect_sweep(d1, h4, h1)
            if not event:
                log.info("%s: no liquidity sweep setup found", display_symbol)
                time.sleep(1)
                continue

            key = f"{display_symbol}:{event['tf']}:{event['direction']}:{event['level']:.8f}:{event['bar_time']}"
            if key in last_alerts:
                log.info("%s: sweep found, but alert already sent", display_symbol)
                time.sleep(1)
                continue
            
            last_alerts.add(key)
            symbol_cooldown[display_symbol] = current_time

            # Отправляем сигнал в Telegram
            telegram(format_signal(display_symbol, event))

            # Сохраняем в базу данных для отслеживания результата
            save_signal(
                symbol=display_symbol,
                tf=event['tf'],
                direction=event['direction'],
                entry_price=event['level'],
                stop_loss=event['stop_loss'],
                target=event['target'],
                rr_ratio=event['rr_ratio']
            )

            log.info("MULTI-TF ALERT SENT & SAVED for %s (%s)!", display_symbol, event['tf'])
            time.sleep(2)
        except Exception as exc:
            log.warning("%s: scan failed: %s", display_symbol, exc)
            time.sleep(2)

    # Проверяем отработку открытых сделок по актуальным ценам
    if current_prices:
        check_open_trades(current_prices, telegram)

    log.info("--- Scan cycle finished ---")


def main():
    if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
        raise SystemExit("Missing Telegram env vars.")
    
    # Инициализируем базу данных для статистики
    init_db()
    log.info("Database initialized successfully.")
    
    while True:
        scan_once()
        time.sleep(SCAN_SECONDS)


if __name__ == "__main__":
    main()
