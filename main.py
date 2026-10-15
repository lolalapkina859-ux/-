import os
import time
import logging
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
import yfinance as yf
from scanner import (
    detect_sweep, 
    format_signal, 
    format_confirmed_signal, 
    optimize_sl_tp_on_confirmation
)
from tracker import init_db, save_signal, check_open_trades

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("liquidity-hunter")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SCAN_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))  # 60 секунд сканирования

SYMBOLS = {
    # Crypto (BingX)
    "BTC / USDT": {"source": "bingx", "ticker": "BTC-USDT"},
    "ETH / USDT": {"source": "bingx", "ticker": "ETH-USDT"},
    "SOL / USDT": {"source": "bingx", "ticker": "SOL-USDT"},

    # Commodities & Metals (yfinance)
    "XAU / USD": {"source": "yfinance", "ticker": "GC=F"},
    "XAG / USD": {"source": "yfinance", "ticker": "SI=F"},
    "USOIL / USD": {"source": "yfinance", "ticker": "CL=F"},

    # Indices (yfinance)
    "US500 / USD": {"source": "yfinance", "ticker": "SPY"},
    "NAS100 / USD": {"source": "yfinance", "ticker": "NQ=F"},

    # Forex Majors & Crosses (yfinance)
    "EUR / USD": {"source": "yfinance", "ticker": "EURUSD=X"},
    "GBP / USD": {"source": "yfinance", "ticker": "GBPUSD=X"},
    "USD / JPY": {"source": "yfinance", "ticker": "USDJPY=X"},
    "GBP / JPY": {"source": "yfinance", "ticker": "GBPJPY=X"},
    "EUR / JPY": {"source": "yfinance", "ticker": "EURJPY=X"},
    "USD / CAD": {"source": "yfinance", "ticker": "USDCAD=X"},
    "NZD / USD": {"source": "yfinance", "ticker": "NZDUSD=X"},
    "AUD / USD": {"source": "yfinance", "ticker": "AUDUSD=X"},
    "EUR / GBP": {"source": "yfinance", "ticker": "EURGBP=X"},
}

last_alerts = set()
symbol_cooldown = {}
COOLDOWN_SECONDS = 10800  # 3 часа перерыва после подтвержденного сигнала
PENDING_TIMEOUT = 7200     # 2 часа таймаут для ожидания подтверждения на M5

# Ожидающие подтверждения закрепа на M5
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
    """Группировка 1H баров от yfinance в 4H бары."""
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
    period_map = {"1d": "350d", "4h": "60d", "1h": "60d", "5m": "7d"}
    period = period_map.get(interval_str, "60d")
    
    yf_interval = "1d" if interval_str == "1d" else ("5m" if interval_str == "5m" else "1h")
    
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


def cleanup_expired_pending(current_time):
    """Удаление устаревших сетапов, которые не подтвердились за PENDING_TIMEOUT."""
    to_delete = []
    for symbol, setup in pending_setups.items():
        created_at = setup.get("created_at", current_time)
        if current_time - created_at > PENDING_TIMEOUT:
            to_delete.append(symbol)
            log.info(f"⏳ [{symbol}] Сетап отменен: превышено время ожидания M5 подтверждения.")
            
    for symbol in to_delete:
        del pending_setups[symbol]


def scan_once():
    log.info("--- Starting scan cycle (Hybrid BingX + YFinance) ---")
    current_time = time.time()
    current_prices = {}

    cleanup_expired_pending(current_time)

    for display_symbol, info in SYMBOLS.items():
        try:
            source = info["source"]
            ticker = info["ticker"]

            # 1. ПРОВЕРКА ПОДТВЕРЖДЕНИЯ НА M5 (Быстрый вход и оптимизация стопа)
            if display_symbol in pending_setups:
                log.info("Checking M5 confirmation for %s...", display_symbol)
                m5 = fetch_bingx_bars(ticker, "5m") if source == "bingx" else fetch_yfinance_bars(ticker, "5m")
                h1 = fetch_bingx_bars(ticker, "1h") if source == "bingx" else fetch_yfinance_bars(ticker, "1h")
                
                if m5 and h1:
                    # Защита от фантомных шпилек yfinance
                    if len(m5) >= 2:
                        raw_m5_close = m5[-1]["close"]
                        prev_m5_close = m5[-2]["close"]
                        
                        price_diff_pct = abs(raw_m5_close - prev_m5_close) / prev_m5_close
                        if price_diff_pct > 0.015:
                            log.warning(f"⚠️ [{display_symbol}] Прорыв спреда yfinance! Цена {raw_m5_close} отличается от предыдущей на {price_diff_pct*100:.2f}%. Используем безопасную цену.")
                            last_m5_close = prev_m5_close
                        else:
                            last_m5_close = raw_m5_close
                    else:
                        last_m5_close = m5[-1]["close"]

                    current_prices[display_symbol] = {
                        "high": m5[-1]["high"],
                        "low": m5[-1]["low"],
                        "close": last_m5_close
                    }
                    
                    setup = pending_setups[display_symbol]
                    level = setup["entry"]  # Ключевой уровень структуры (+CISD / -CISD)
                    direction = setup["direction"]

                    confirmed = False
                    if direction == "LONG" and last_m5_close > level:
                        confirmed = True
                    elif direction == "SHORT" and last_m5_close < level:
                        confirmed = True

                    if confirmed:
                        log.info(f"⚡️ [{display_symbol}] M5 CONFIRMED! Закрытие {last_m5_close} за уровнем {level}")
                        
                        # Пересчитываем локальный стоп-лосс с учетом буфера на спред и Expansion свечи M5
                        opt_sl, opt_t1, opt_t2, opt_rr = optimize_sl_tp_on_confirmation(
                            direction, last_m5_close, m5, h1, symbol=display_symbol
                        )
                        
                        if opt_sl:
                            setup["stop_loss"] = opt_sl
                            setup["target_1"] = opt_t1
                            setup["target_2"] = opt_t2
                            setup["rr_ratio"] = opt_rr

                            # Отправка Telegram алерта с ЧЕСТНОЙ ценой входа (last_m5_close)
                            telegram(format_confirmed_signal(display_symbol, setup, last_m5_close))
                            
                            # Сохранение в БД с ЧЕСТНОЙ ценой входа
                            save_signal(
                                symbol=display_symbol,
                                tf=setup['tf'],
                                direction=direction,
                                entry_price=last_m5_close,
                                stop_loss=setup['stop_loss'],
                                target=setup['target_2'],
                                rr_ratio=setup['rr_ratio']
                            )
                            
                            del pending_setups[display_symbol]
                            symbol_cooldown[display_symbol] = current_time
                            continue
                        else:
                            log.info(f"[{display_symbol}] M5 закрепление пропущено: слабый корпус свечи (не Expansion).")

            # 2. ПОИСК СВИПОВ ПО HTF (4H, 1H)
            last_alert_time = symbol_cooldown.get(display_symbol, 0)
            if current_time - last_alert_time < COOLDOWN_SECONDS or display_symbol in pending_setups:
                continue

            if source == "bingx":
                d1 = fetch_bingx_bars(ticker, "1d")
                time.sleep(0.15)
                h4 = fetch_bingx_bars(ticker, "4h")
                time.sleep(0.15)
                h1 = fetch_bingx_bars(ticker, "1h")
            else:
                d1 = fetch_yfinance_bars(ticker, "1d")
                h4 = fetch_yfinance_bars(ticker, "4h")
                h1 = fetch_yfinance_bars(ticker, "1h")

            if h1 and display_symbol not in current_prices:
                current_prices[display_symbol] = {
                    "high": h1[-1]["high"],
                    "low": h1[-1]["low"],
                    "close": h1[-1]["close"]
                }

            event = detect_sweep(d1, h4, h1, display_symbol)
            if not event:
                time.sleep(0.15)
                continue

            key = f"{display_symbol}:{event['tf']}:{event['direction']}:{event['level']:.8f}:{event['bar_time']}"
            if key in last_alerts:
                time.sleep(0.15)
                continue

            last_alerts.add(key)
            
            # Отправляем первичный алерт о свипе
            telegram(format_signal(display_symbol, event))

            # Переводим сетап в режим ожидания M5-закрепа с отметкой времени
            event["created_at"] = current_time
            pending_setups[display_symbol] = event
            log.info(f"⏳ [{display_symbol}] Свип обнаружен. Ожидаем закрепления на M5...")

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
    
    while True:
        scan_once()
        time.sleep(SCAN_SECONDS)


if __name__ == "__main__":
    main()
