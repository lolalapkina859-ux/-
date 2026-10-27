import os
import time
import logging
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

import scanner
import tracker

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("main")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
KYIV_TZ = ZoneInfo("Europe/Kyiv")

# Возвращены оригинальные тикеры
SYMBOLS = {
    "DXY": {"source": "yfinance", "ticker": "DX-Y.NYB"},
    "BTC / USDT": {"source": "bingx", "ticker": "BTC-USDT"},
    "ETH / USDT": {"source": "bingx", "ticker": "ETH-USDT"},
    "SOL / USDT": {"source": "bingx", "ticker": "SOL-USDT"},
    "XAU / USD": {"source": "yfinance", "ticker": "XAUT-USD"},
    "XAG / USD": {"source": "yfinance", "ticker": "SI=F"},
    "USOIL / USD": {"source": "yfinance", "ticker": "CL=F"},
    "US500 / USD": {"source": "yfinance", "ticker": "SPY"},
    "EUR / USD": {"source": "yfinance", "ticker": "EURUSD=X"},
    "GBP / USD": {"source": "yfinance", "ticker": "GBPUSD=X"},
    "USD / JPY": {"source": "yfinance", "ticker": "USDJPY=X"},
}

LAST_ALERTS = {}
COOLDOWN_SECONDS = 3600


def send_telegram_msg(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        log.error(f"Telegram error: {e}")


def fetch_bingx_bars(symbol, interval="1h", limit=100):
    try:
        pair = symbol.replace("-USD", "-USDT")
        url = f"https://open-api.bingx.com/openApi/swap/v2/quote/klines?symbol={pair}&interval={interval}&limit={limit}"
        res = requests.get(url, timeout=10).json()
        if res.get("code") == 0 and "data" in res:
            return [{
                "timestamp": int(item["time"]) // 1000,
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "volume": float(item["volume"])
            } for item in res["data"]]
    except Exception as e:
        log.error(f"BingX error ({symbol}): {e}")
    return []


def fetch_yfinance_bars(symbol, interval="1h", period="5d"):
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False)
        if df.empty:
            return []
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return [{
            "timestamp": int(idx.timestamp()),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]) if "Volume" in row else 0.0
        } for idx, row in df.iterrows()]
    except Exception as e:
        log.error(f"yfinance error ({symbol}): {e}")
    return []


def get_bars(symbol, timeframe):
    if "-USD" in symbol:
        interval_map = {"5m": "5m", "1h": "1h", "4h": "4h"}
        return fetch_bingx_bars(symbol, interval=interval_map.get(timeframe, "1h"))
    else:
        period_map = {"5m": "1d", "1h": "5d", "4h": "10d"}
        interval_map = {"5m": "5m", "1h": "1h", "4h": "1h"}
        raw_bars = fetch_yfinance_bars(symbol, interval=interval_map.get(timeframe, "1h"), period=period_map.get(timeframe, "5d"))
        
        if timeframe == "4h" and raw_bars:
            h4_bars = []
            for i in range(0, len(raw_bars), 4):
                chunk = raw_bars[i:i+4]
                if not chunk:
                    continue
                h4_bars.append({
                    "timestamp": chunk[0]["timestamp"],
                    "open": chunk[0]["open"],
                    "high": max(b["high"] for b in chunk),
                    "low": min(b["low"] for b in chunk),
                    "close": chunk[-1]["close"],
                    "volume": sum(b["volume"] for b in chunk)
                })
            return h4_bars
        return raw_bars


def process_symbol(symbol):
    h1_bars = get_bars(symbol, "1h")
    h4_bars = get_bars(symbol, "4h")
    if not h1_bars or not h4_bars:
        return

    current_price = h1_bars[-1]["close"]
    signal = scanner.detect_macro_range_sweep(h4_bars, h1_bars, symbol=symbol, current_price=current_price)
    if not signal:
        return

    now_ts = time.time()
    if now_ts - LAST_ALERTS.get(symbol, 0) < COOLDOWN_SECONDS:
        return

    LAST_ALERTS[symbol] = now_ts
    created_at = datetime.now(timezone.utc)
    created_kyiv_str = created_at.astimezone(KYIV_TZ).strftime("%H:%M:%S")

    tracker.save_trade(
        symbol=symbol,
        direction=signal["direction"],
        entry_price=signal["entry"],
        stop_loss=signal["stop_loss"],
        target_1=signal["target_1"],
        target_2=signal["target_2"],
        created_at=created_at
    )

    msg = (
        f"🚨 **PRICE ACTION SIGNAL: {symbol}** 🚨\n\n"
        f"📈 **Направление:** `{signal['direction']}`\n"
        f"🎯 **Триггер:** `{signal['confluence']}`\n"
        f"💵 **Цена входа:** `{signal['entry']:.5f}`\n"
        f"🛡 **Стоп-Лосс (SL):** `{signal['stop_loss']:.5f}`\n"
        f"🎯 **Тейк-Профит (TP):** `{signal['target_1']:.5f}`\n"
        f"⏱ **Время:** `{created_kyiv_str}` (Kyiv)"
    )
    send_telegram_msg(msg)


def main():
    tracker.init_db()
    log.info("🚀 Liquidity Hunter Bot запущен с оригинальными тикерами...")

    while True:
        try:
            for symbol in SYMBOLS:
                process_symbol(symbol)
                time.sleep(1)

            tracker.check_open_trades(lambda sym: get_bars(sym, "5m"), send_telegram_msg)
        except Exception as e:
            log.error(f"Main loop error: {e}")

        time.sleep(60)


if __name__ == "__main__":
    main()
