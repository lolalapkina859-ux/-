import os
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import yfinance as yf
from scanner import (
    detect_sweep, 
    format_signal, 
    format_confirmed_signal, 
    optimize_sl_tp_on_confirmation,
    calculate_daily_bias,
    get_quarterly_phase,
    calculate_range_dynamics,
    calculate_dol_levels,
    find_ocl_levels,
    find_order_blocks,
    find_fvg,
    detect_quasimodo_patterns,
    find_inducement
)
from tracker import init_db, save_signal, check_open_trades

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("liquidity-hunter")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SCAN_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))

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

last_alerts = set()
symbol_cooldown = {}

# УМЕНЬШЕННЫЕ КРИТЕРИИ ДЛЯ ЧАСТЫХ СИГНАЛОВ:
COOLDOWN_SECONDS = 1800  # Снизили с 3 часов до 30 минут
PENDING_TIMEOUT = 900     # Снизили с 2 часов до 15 минут, чтобы зависшие сетапы быстро сбрасывались
pending_setups = {}


def telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        response = requests.post(
            url, 
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}, 
            timeout=15
        )
        response.raise_for_status()
    except Exception:
        log.exception("Telegram delivery failed")


def fetch_bingx_bars(symbol, interval_str):
    interval_map = {"1d": "1d", "4h": "4h", "1h": "1h", "5m": "5m"}
    bx_interval = interval_map.get(interval_str, "1h")
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


def aggregate_1h_to_4h(h1_bars):
    if not h1_bars:
        return []
    h4_bars = []
    chunk_size = 4
    for i in range(0, len(h1_bars), chunk_size):
        chunk = h1_bars[i:i + chunk_size]
        if not chunk:
            continue
        h4_bars.append({
            "timestamp": chunk[0]["timestamp"],
            "open": chunk[0]["open"],
            "high": max(b["high"] for b in chunk),
            "low": min(b["low"] for b in chunk),
            "close": chunk[-1]["close"],
        })
    return h4_bars


def fetch_yfinance_bars(ticker_str, interval_str):
    period_map = {"1d": "350d", "4h": "60d", "1h": "60d", "15m": "14d", "5m": "7d"}
    period = period_map.get(interval_str, "60d")
    
    if interval_str == "1d":
        yf_interval = "1d"
    elif interval_str == "15m":
        yf_interval = "15m"
    elif interval_str == "5m":
        yf_interval = "5m"
    else:
        yf_interval = "1h"
    
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
    
    if interval_str == "4h":
        return aggregate_1h_to_4h(bars)
    return bars


def get_macro_news_narrative():
    local_tz = timezone(timedelta(hours=3))
    raw_events = [
        {"day": "Среда, 28 окт", "utc_hour": 21, "utc_min": 0, "event": "API / Crude Oil Inventories", "impact": "Medium"},
        {"day": "Четверг, 29 окт", "utc_hour": 1, "utc_min": 0, "event": "Federal Funds Rate & FOMC Statement", "impact": "HIGH 🔴"},
        {"day": "Четверг, 29 окт", "utc_hour": 1, "utc_min": 30, "event": "FOMC Press Conference", "impact": "HIGH 🔴"},
        {"day": "Четверг, 29 окт", "utc_hour": 12, "utc_min": 30, "event": "Advance GDP q/q & Core PCE Price Index", "impact": "HIGH 🔴"},
    ]
    
    narrative = f"📰 **Макро-наратив (Ваше локальное время):**\n"
    for ev in raw_events:
        utc_dt = datetime.now(timezone.utc).replace(hour=ev["utc_hour"], minute=ev["utc_min"], second=0, microsecond=0)
        local_dt = utc_dt.astimezone(local_tz)
        time_str = local_dt.strftime("%H:%M")
        narrative += f"• `{ev['day']} в {time_str}` — {ev['event']} [{ev['impact']}]\n"
    narrative += f"⚠️ _В период выхода этих новостей возможны резкие сквизы._\n"
    return narrative


def send_gold_morning_analysis():
    log.info("🌟 Выполнение утреннего анализа по золоту (XAU/USD) и DXY...")
    gold_info = SYMBOLS.get("XAU / USD")
    dxy_info = SYMBOLS.get("DXY")
    if not gold_info:
        return

    try:
        dxy_price = 0.0
        if dxy_info:
            dxy_bars = fetch_yfinance_bars(dxy_info["ticker"], "1h")
            if dxy_bars:
                dxy_price = dxy_bars[-1]["close"]

        ticker = gold_info["ticker"]
        d1 = fetch_yfinance_bars(ticker, "1d")
        h4 = fetch_yfinance_bars(ticker, "4h")
        h1 = fetch_yfinance_bars(ticker, "1h")
        
        if not d1 or not h4 or not h1:
            return

        bias = calculate_daily_bias(d1, h4)
        qt_phase = get_quarterly_phase()
        rd = calculate_range_dynamics(h1, window=24)
        dol = calculate_dol_levels(d1)
        ocl_levels = find_ocl_levels(h4)
        obs = find_order_blocks(h4)
        current_gold_price = h1[-1]["close"] if h1 else 0.0

        report = (
            f"🥇 **ЛОКАЛЬНЫЙ АНАЛИЗ GOLD (XAU/USD)** 🥇\n\n"
            f"💵 **Текущая цена золота:** `{current_gold_price:.2f}`\n"
            f"💵 **Индекс доллара (DXY):** `{dxy_price:.2f}`\n"
            f"📅 **Daily Bias:** `{bias}`\n"
            f"⌛ **QT Phase:** `{qt_phase}`\n\n"
        )

        if rd:
            report += (
                f"📊 **Локальный Dealing Range (1H):**\n"
                f"• Local High (Premium): `{rd['range_high']:.2f}`\n"
                f"• Local Low (Discount): `{rd['range_low']:.2f}`\n"
                f"• Equilibrium (50% EQ): `{rd['eq_level']:.2f}`\n\n"
            )

        if dol:
            report += f"🎯 **Цели ликвидности (DOL):**\n• PDH: `{dol.get('PDH', 0):.2f}` | PDL: `{dol.get('PDL', 0):.2f}`\n\n"

        if obs:
            report += f"📦 **Order Block (4H/1H):** `{obs[-1]['type']}` (`{obs[-1]['bottom']:.2f} — {obs[-1]['top']:.2f}`)\n"

        report += f"\n{get_macro_news_narrative()}\n"
        report += (
            f"📍 **РАБОЧИЙ ПЛАН (Внутри дня):**\n"
            f"• Отслеживаем реакцию цены внутри локального диапазона 1H.\n"
            f"• Ищем точки входа (FVG / OB + CHoCH на M5) при подходе к локальным границам."
        )

        telegram(report)
        log.info("Локальный отчет по золоту отправлен.")
    except Exception as e:
        log.exception(f"Ошибка локального анализа золота/DXY: {e}")


def send_btc_morning_analysis():
    log.info("🌟 Выполнение анализа по BTC...")
    btc_info = SYMBOLS.get("BTC / USDT")
    if not btc_info:
        return

    ticker = btc_info["ticker"]
    try:
        d1 = fetch_bingx_bars(ticker, "1d")
        h4 = fetch_bingx_bars(ticker, "4h")
        h1 = fetch_bingx_bars(ticker, "1h")
        
        if not d1 or not h4 or not h1:
            return

        bias = calculate_daily_bias(d1, h4)
        qt_phase = get_quarterly_phase()
        rd = calculate_range_dynamics(h1, window=24)
        dol = calculate_dol_levels(d1)
        obs = find_order_blocks(h4)
        current_btc_price = h1[-1]["close"] if h1 else 0.0

        report = (
            f"🟠 **ЛОКАЛЬНЫЙ АНАЛИЗ BITCOIN (BTC/USDT)** 🟠\n\n"
            f"💵 **Текущая цена:** `{current_btc_price:.2f}`\n"
            f"📅 **Daily Bias:** `{bias}`\n"
            f"⌛ **QT Phase:** `{qt_phase}`\n\n"
        )

        if rd:
            report += (
                f"📊 **Локальный Dealing Range (1H):**\n"
                f"• Local High (Premium): `{rd['range_high']:.2f}`\n"
                f"• Local Low (Discount): `{rd['range_low']:.2f}`\n"
                f"• Equilibrium (EQ): `{rd['eq_level']:.2f}`\n\n"
            )

        telegram(report)
        log.info("Локальный отчет по BTC отправлен.")
    except Exception as e:
        log.exception(f"Ошибка анализа BTC: {e}")


def cleanup_expired_pending(current_time):
    to_delete = []
    for symbol, setup in pending_setups.items():
        if current_time - setup.get("created_at", current_time) > PENDING_TIMEOUT:
            to_delete.append(symbol)
    for symbol in to_delete:
        del pending_setups[symbol]


def scan_once():
    log.info("--- Starting scan cycle ---")
    current_time = time.time()
    current_prices = {}
    cleanup_expired_pending(current_time)

    for display_symbol, info in SYMBOLS.items():
        if display_symbol == "DXY":
            continue

        try:
            source = info["source"]
            ticker = info["ticker"]

            if display_symbol in pending_setups:
                m5 = fetch_bingx_bars(ticker, "5m") if source == "bingx" else fetch_yfinance_bars(ticker, "5m")
                h1 = fetch_bingx_bars(ticker, "1h") if source == "bingx" else fetch_yfinance_bars(ticker, "1h")
                if m5 and h1:
                    last_m5_close = m5[-1]["close"]
                    current_prices[display_symbol] = {"high": m5[-1]["high"], "low": m5[-1]["low"], "close": last_m5_close}
                    setup = pending_setups[display_symbol]
                    
                    confirmed = (setup["direction"] == "LONG" and last_m5_close > setup["entry"]) or \
                                (setup["direction"] == "SHORT" and last_m5_close < setup["entry"])

                    if confirmed:
                        opt_sl, opt_t1, opt_t2, opt_rr = optimize_sl_tp_on_confirmation(
                            setup["direction"], last_m5_close, m5, h1, symbol=display_symbol
                        )
                        if opt_sl:
                            setup.update({"stop_loss": opt_sl, "target_1": opt_t1, "target_2": opt_t2, "rr_ratio": opt_rr})
                            telegram(format_confirmed_signal(display_symbol, setup, last_m5_close))
                            save_signal(display_symbol, setup['tf'], setup['direction'], last_m5_close, opt_sl, opt_t2, opt_rr)
                            del pending_setups[display_symbol]
                            symbol_cooldown[display_symbol] = current_time
                            continue

            if current_time - symbol_cooldown.get(display_symbol, 0) < COOLDOWN_SECONDS or display_symbol in pending_setups:
                continue

            d1 = fetch_bingx_bars(ticker, "1d") if source == "bingx" else fetch_yfinance_bars(ticker, "1d")
            time.sleep(0.15)
            h4 = fetch_bingx_bars(ticker, "4h") if source == "bingx" else fetch_yfinance_bars(ticker, "4h")
            time.sleep(0.15)
            h1 = fetch_bingx_bars(ticker, "1h") if source == "bingx" else fetch_yfinance_bars(ticker, "1h")

            current_market_price = h1[-1]["close"] if h1 else None
            if h1:
                current_prices[display_symbol] = {"high": h1[-1]["high"], "low": h1[-1]["low"], "close": current_market_price}

            event = detect_sweep(d1, h4, h1, display_symbol, current_price=current_market_price)
            if not event:
                time.sleep(0.15)
                continue

            key = f"{display_symbol}:{event['tf']}:{event['direction']}:{event['level']:.8f}:{event['bar_time']}"
            if key in last_alerts:
                time.sleep(0.15)
                continue

            last_alerts.add(key)
            telegram(format_signal(display_symbol, event))
            event["created_at"] = current_time
            pending_setups[display_symbol] = event
            time.sleep(0.2)
        except Exception as exc:
            log.warning("%s: scan failed: %s", display_symbol, exc)
            time.sleep(0.2)

    if current_prices:
        check_open_trades(current_prices, telegram)
    log.info("--- Scan cycle finished ---")


def main():
    if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
        raise SystemExit("Missing Telegram env vars.")
    init_db()
    log.info("Database initialized successfully.")
    
    send_gold_morning_analysis()
    time.sleep(2)
    send_btc_morning_analysis()
    
    while True:
        scan_once()
        time.sleep(SCAN_SECONDS)


if __name__ == "__main__":
    main()
