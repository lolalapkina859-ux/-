import warnings
warnings.simplefilter(action='ignore', category=FutureWarning)
warnings.filterwarnings('ignore', category=UserWarning, module='yfinance')
warnings.filterwarnings('ignore', message='.*inplace keyword.*')
try:
    from pandas.errors import Pandas4Warning
    warnings.simplefilter('ignore', category=Pandas4Warning)
except ImportError:
    pass

import os
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import yfinance as yf
from scanner import (
    detect_crt_setup,
    CRT_TF,
    format_signal,
    calculate_daily_bias,
    get_quarterly_phase,
    calculate_range_dynamics,
    calculate_dol_levels,
)
from tracker import init_db, save_signal, check_open_trades, has_open_trade

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("liquidity-hunter")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SCAN_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))
LOCAL_TZ = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Kyiv"))

SYMBOLS = {
    "DXY": {"source": "yfinance", "ticker": "DX-Y.NYB"},
    "BTC / USDT": {"source": "bingx", "ticker": "BTC-USDT"},
    "ETH / USDT": {"source": "bingx", "ticker": "ETH-USDT"},
    "SOL / USDT": {"source": "bingx", "ticker": "SOL-USDT"},
    "XAU / USD": {"source": "yfinance", "ticker": os.getenv("GOLD_TICKER", "XAUUSD=X")},
    "XAG / USD": {"source": "yfinance", "ticker": "SI=F"},
    "USOIL / USD": {"source": "yfinance", "ticker": "CL=F"},
    "US500 / USD": {"source": "yfinance", "ticker": "SPY"},
    "EUR / USD": {"source": "yfinance", "ticker": "EURUSD=X"},
    "GBP / USD": {"source": "yfinance", "ticker": "GBPUSD=X"},
    "USD / JPY": {"source": "yfinance", "ticker": "USDJPY=X"},
}

# Какие инструменты сканировать (через запятую, Railway Variables). По умолчанию только золото.
ACTIVE_SYMBOLS = [x.strip() for x in os.getenv("ACTIVE_SYMBOLS", "XAU / USD").split(",") if x.strip()]

last_alerts = set()
symbol_cooldown = {}
COOLDOWN_SECONDS = 1800  # 30 минут кулдаун на инструмент
_bars_cache = {}
CACHE_TTL = {"1d": 3600, "4h": 55, "1h": 55, "5m": 25}


def telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}, timeout=15)
        if r.status_code == 400:  # ошибка разметки — шлём без форматирования, чтобы не потерять сигнал
            r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message.replace("*", "").replace("`", "")}, timeout=15)
        r.raise_for_status()
    except Exception:
        log.exception("Telegram delivery failed")


def fetch_bingx_bars(symbol, interval_str):
    interval_map = {"1d": "1d", "4h": "4h", "1h": "1h", "5m": "5m"}
    bx_interval = interval_map.get(interval_str, "1h")
    limit = 1000 if interval_str == "5m" else 100  # длинная история M5 нужна только трекеру
    url = f"https://open-api.bingx.com/openApi/swap/v2/quote/klines?symbol={symbol}&interval={bx_interval}&limit={limit}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0 or not data.get("data"):
        raise RuntimeError(f"BingX API error for {symbol}: {data}")
    bars = [{
        "timestamp": int(row["time"] / 1000),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
    } for row in data["data"]]
    bars.sort(key=lambda b: b["timestamp"])
    return bars


def aggregate_1h_to_4h(h1_bars):
    """Склейка H1 -> H4 по реальным границам 4-часовых свечей (UTC 00/04/08/12/16/20)."""
    buckets = {}
    for b in h1_bars:
        buckets.setdefault(b["timestamp"] // 14400, []).append(b)
    h4 = []
    for key in sorted(buckets):
        chunk = buckets[key]
        h4.append({
            "timestamp": key * 14400,
            "open": chunk[0]["open"],
            "high": max(b["high"] for b in chunk),
            "low": min(b["low"] for b in chunk),
            "close": chunk[-1]["close"],
        })
    return h4


def fetch_yfinance_bars(ticker_str, interval_str):
    period_map = {"1d": "350d", "4h": "60d", "1h": "60d", "15m": "14d", "5m": "7d"}
    period = period_map.get(interval_str, "60d")
    yf_interval = {"1d": "1d", "15m": "15m", "5m": "5m"}.get(interval_str, "1h")

    df = yf.download(ticker_str, period=period, interval=yf_interval, progress=False)
    if df.empty:
        raise RuntimeError(f"Empty data from yfinance for {ticker_str}")
    if hasattr(df.columns, 'levels') and len(df.columns.levels) > 1:
        df.columns = df.columns.get_level_values(0)
    bars = [{
        "timestamp": int(idx.timestamp()),
        "open": float(row["Open"]),
        "high": float(row["High"]),
        "low": float(row["Low"]),
        "close": float(row["Close"]),
    } for idx, row in df.iterrows()]
    bars.sort(key=lambda b: b["timestamp"])
    if interval_str == "4h":
        return aggregate_1h_to_4h(bars)
    return bars


def get_bars(source, ticker, tf):
    """Загрузка свечей с кэшем. H4 для yfinance строится из уже загруженного H1 (без лишнего запроса)."""
    key = (source, ticker, tf)
    now = time.time()
    hit = _bars_cache.get(key)
    if hit and now - hit[0] < CACHE_TTL.get(tf, 55):
        return hit[1]
    if source == "yfinance" and tf == "4h":
        bars = aggregate_1h_to_4h(get_bars(source, ticker, "1h"))
    elif source == "bingx":
        bars = fetch_bingx_bars(ticker, tf)
    else:
        bars = fetch_yfinance_bars(ticker, tf)
    _bars_cache[key] = (now, bars)
    return bars


def get_macro_news_narrative():
    local_tz = LOCAL_TZ
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
    log.info("🌟 Утренний анализ по золоту (XAU/USD) и DXY...")
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
        current_gold_price = h1[-1]["close"] if h1 else 0.0

        report = (
            f"🥇 **ПРИНЦИП PRICE ACTION: GOLD (XAU/USD)** 🥇\n\n"
            f"💵 **Текущая цена:** `{current_gold_price:.2f}`\n"
            f"💵 **Индекс доллара (DXY):** `{dxy_price:.2f}`\n"
            f"📅 **Bias:** `{bias}` | ⌛ **QT:** `{qt_phase}`\n\n"
        )

        if rd:
            report += (
                f"📊 **Локальный диапазон 1H:**\n"
                f"• High: `{rd['range_high']:.2f}` | Low: `{rd['range_low']:.2f}`\n"
                f"• EQ (50%): `{rd['eq_level']:.2f}`\n\n"
            )

        if dol:
            report += f"🎯 **Цели ликвидности (DOL):**\n• PDH: `{dol.get('PDH', 0):.2f}` | PDL: `{dol.get('PDL', 0):.2f}`\n\n"

        report += f"\n{get_macro_news_narrative()}\n"
        report += (
            f"📍 **РЕЖИМ РАБОТЫ:**\n"
            f"• Стратегия **CRT + Turtle Soup**: свеча-диапазон {CRT_TF.upper()}, прокол её хая/лоя, возврат внутрь.\n"
            f"• Вход только после слома структуры на M5, лимиткой на откате. SL за проколом, цели: EQ и граница диапазона."
        )

        telegram(report)
        log.info("Отчет по золоту отправлен.")
    except Exception as e:
        log.exception(f"Ошибка анализа золота: {e}")


def send_btc_morning_analysis():
    log.info("🌟 Утренний анализ по BTC/USDT...")
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
        current_btc_price = h1[-1]["close"] if h1 else 0.0

        report = (
            f"🟠 **ПРИНЦИП PRICE ACTION: BITCOIN (BTC/USDT)** 🟠\n\n"
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


def fetch_m5_for_tracking(symbol):
    """M5 используется ТОЛЬКО чтобы честно проверить, что цена сделала после входа. На стопы/тейки не влияет."""
    info = SYMBOLS[symbol]
    return get_bars(info["source"], info["ticker"], "5m")


def scan_once():
    log.info(f"--- Starting scan cycle (CRT {CRT_TF.upper()} + M5 Turtle Soup) ---")
    now = time.time()
    if len(last_alerts) > 500:
        last_alerts.clear()

    for display_symbol, info in SYMBOLS.items():
        if display_symbol not in ACTIVE_SYMBOLS:
            continue
        try:
            if now - symbol_cooldown.get(display_symbol, 0) < COOLDOWN_SECONDS:
                continue
            if has_open_trade(display_symbol):
                continue  # пока ордер ждёт входа или сделка открыта — новых сигналов нет

            source, ticker = info["source"], info["ticker"]
            crt_bars = get_bars(source, ticker, CRT_TF)
            m5 = get_bars(source, ticker, "5m")
            if not crt_bars or not m5:
                continue

            event = detect_crt_setup(crt_bars, m5, display_symbol)
            if not event:
                continue

            key = f"{display_symbol}:{event['direction']}:{event['bar_time']}"
            if key in last_alerts:
                continue
            last_alerts.add(key)

            save_signal(display_symbol, event["tf"], event["direction"], event["entry"],
                        event["stop_loss"], event["target_1"], event["rr_ratio"], event["tp_eq"])
            telegram(format_signal(display_symbol, event, datetime.now(LOCAL_TZ).strftime("%H:%M:%S")))
            symbol_cooldown[display_symbol] = now
            time.sleep(0.2)
        except Exception as exc:
            log.warning("%s: scan failed: %s", display_symbol, exc)
            time.sleep(0.2)

    try:
        check_open_trades(fetch_m5_for_tracking, telegram)
    except Exception:
        log.exception("check_open_trades failed")
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
