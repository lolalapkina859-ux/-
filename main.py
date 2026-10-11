import os
import time
import logging
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
import yfinance as yf
from scanner import detect_sweep, format_signal

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
symbol_cooldown = {}  # Защита от спама: хранит таймстамп последнего алерта по инструменту
COOLDOWN_SECONDS = 10800  # 3 часа перерыва для конкретной пары


def telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        response = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=15)
        response.raise_for_status()
    except Exception:
        log.exception("Telegram delivery failed")


def send_daily_briefing():
    today_str = datetime.now(timezone.utc).strftime("%d %B %Y")
    briefing = (
        f"Kozak Trend 📈📉\n"
        f"📅 {today_str}\n\n"
        f"⚖️ Новини сьогодні:\n"
        f"Слідкуйте за календарем волатильності по USD, EUR та XAU.\n\n"
        f"🎯 Під прицілом сьогодні:\n\n"
        f"1Д - USD/JPY 📉, XAU/USD 📈\n"
        f"4h - NZD/USD 📉, USD/CAD 📈\n"
        f"1h - BTC/USDT 📉, XAU/USD 📉, EUR/USD 📈, GBP/USD 📉\n\n"
        f"⚡️ Як тільки буде знята ключова ліквідність по цих парах — поговоримо!"
    )
    telegram(briefing)


def fetch_binance_bars(symbol, interval_str):
    intervals_map = {"1h": "1h", "5min": "5m"}
    binance_interval = intervals_map.get(interval_str, "1h")
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={binance_interval}&limit=100"
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
    yf_interval = "1h" if interval_str == "1h" else "5m"
    period = "7d" if yf_interval == "5m" else "60d"
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
    log.info("--- Starting scan cycle (Pro Features) ---")
    current_time = time.time()

    for display_symbol, info in SYMBOLS.items():
        try:
            # Проверка Cooldown (защита от спама)
            last_alert_time = symbol_cooldown.get(display_symbol, 0)
            if current_time - last_alert_time < COOLDOWN_SECONDS:
                log.info("%s is in cooldown period. Skipping...", display_symbol)
                continue

            log.info("Fetching bars for %s...", display_symbol)
            source = info["source"]
            ticker = info["ticker"]
            
            if source == "binance":
                h1 = fetch_binance_bars(ticker, "1h")
                time.sleep(1)
                m5 = fetch_binance_bars(ticker, "5min")
            else:
                h1 = fetch_yfinance_bars(ticker, "1h")
                time.sleep(1)
                m5 = fetch_yfinance_bars(ticker, "5min")
            
            event = detect_sweep(h1, m5)
            if not event:
                log.info("%s: no liquidity sweep setup found", display_symbol)
                time.sleep(2)
                continue

            key = f"{display_symbol}:{event['direction']}:{event['level']:.8f}:{event['bar_time']}"
            if key in last_alerts:
                log.info("%s: sweep found, but alert already sent", display_symbol)
                time.sleep(2)
                continue
            
            last_alerts.add(key)
            if len(last_alerts) > 1000:
                last_alerts.clear()
                last_alerts.add(key)

            # Устанавливаем кулдаун для символа
            symbol_cooldown[display_symbol] = current_time

            telegram(format_signal(display_symbol, event))
            log.info("PRO ALERT SENT for %s!", display_symbol)
            time.sleep(3)
        except Exception as exc:
            log.warning("%s: scan failed: %s", display_symbol, exc)
            time.sleep(3)
    log.info("--- Scan cycle finished ---")


def main():
    if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
        raise SystemExit("Missing Telegram env vars.")
    
    send_daily_briefing()
    
    while True:
        scan_once()
        time.sleep(SCAN_SECONDS)


if __name__ == "__main__":
    main()
