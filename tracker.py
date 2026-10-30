import os
import sqlite3
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from scanner import fmt_price

log = logging.getLogger("liquidity-tracker")
# Для Railway: подключи Volume и поставь DB_PATH=/data/trades_history.db, иначе БД стирается при деплое
DB_NAME = os.getenv("DB_PATH", "trades_history.db")
LOCAL_TZ = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Kyiv"))


def init_db():
    conn = sqlite3.connect(DB_NAME)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, tf TEXT, direction TEXT,
            entry_price REAL, stop_loss REAL, target REAL, rr_ratio REAL,
            status TEXT DEFAULT 'OPEN',
            created_at TEXT
        )
    ''')
    conn.commit()
    conn.close()


def save_signal(symbol, tf, direction, entry_price, stop_loss, target, rr_ratio):
    conn = sqlite3.connect(DB_NAME)
    conn.execute('''
        INSERT INTO trades (symbol, tf, direction, entry_price, stop_loss, target, rr_ratio, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
    ''', (symbol, tf, direction, entry_price, stop_loss, target, rr_ratio,
          datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def has_open_trade(symbol):
    conn = sqlite3.connect(DB_NAME)
    n = conn.execute("SELECT COUNT(*) FROM trades WHERE symbol = ? AND status = 'OPEN'", (symbol,)).fetchone()[0]
    conn.close()
    return n > 0


def _parse_dt(value):
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def check_open_trades(get_bars_fn, telegram_callback):
    """
    get_bars_fn(symbol) -> список свечей M5 (только для проверки исхода, не для стопов).
    Учитываются ТОЛЬКО свечи, открывшиеся после времени сигнала.
    Если в одной свече задеты и SL, и TP — засчитывается стоп (консервативно).
    """
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id, symbol, tf, direction, entry_price, stop_loss, target, rr_ratio, created_at "
                "FROM trades WHERE status = 'OPEN'")
    open_trades = cur.fetchall()
    bars_cache = {}

    for trade_id, symbol, tf, direction, entry, sl, tp, rr, created_at in open_trades:
        try:
            created_dt = _parse_dt(created_at)
        except Exception:
            log.warning("Bad created_at for trade %s: %s", trade_id, created_at)
            continue
        created_ts = created_dt.timestamp()

        if symbol not in bars_cache:
            try:
                bars_cache[symbol] = get_bars_fn(symbol)
            except Exception as exc:
                log.warning("%s: tracking fetch failed: %s", symbol, exc)
                bars_cache[symbol] = None
        bars = bars_cache[symbol]
        if not bars:
            continue

        outcome = None
        for b in bars:
            if b["timestamp"] < created_ts:
                continue
            if direction == "LONG":
                hit_sl, hit_tp = b["low"] <= sl, b["high"] >= tp
            else:
                hit_sl, hit_tp = b["high"] >= sl, b["low"] <= tp
            if hit_sl:
                outcome = "LOSS"
                break
            if hit_tp:
                outcome = "WIN"
                break

        if not outcome:
            continue

        cur.execute("UPDATE trades SET status = ? WHERE id = ?", (outcome, trade_id))
        conn.commit()

        time_str = created_dt.astimezone(LOCAL_TZ).strftime('%Y-%m-%d %H:%M:%S')
        icon = "🟢 TARGET HIT! ✅" if outcome == "WIN" else "🔴 STOP LOSS HIT ❌"
        profit = f"+{rr}R" if outcome == "WIN" else "-1.0R"
        telegram_callback(
            f"📊 **ОТРАБОТКА СЕТАПА** [{symbol}]\n"
            f"📈 Направление: `{direction}` | TF: `{tf}`\n"
            f"💵 Цена входа: `{fmt_price(entry)}`\n"
            f"⏱ Время сигнала: `{time_str}`\n"
            f"Результат: {icon}\n"
            f"Профит: `{profit}`"
        )

    conn.close()


def get_stats_summary():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    wins = cur.execute("SELECT COUNT(*) FROM trades WHERE status = 'WIN'").fetchone()[0]
    losses = cur.execute("SELECT COUNT(*) FROM trades WHERE status = 'LOSS'").fetchone()[0]
    win_rr = cur.execute("SELECT SUM(rr_ratio) FROM trades WHERE status = 'WIN'").fetchone()[0] or 0.0
    conn.close()
    total = wins + losses
    winrate = round((wins / total) * 100, 1) if total else 0.0
    total_r = round(win_rr - losses, 2)
    return (
        f"📈 **СТАТИСТИКА ОТРАБОТКИ СИГНАЛОВ**\n\n"
        f"🎯 Закрыто сетапов: {total}\n"
        f"✅ WIN: {wins}\n"
        f"❌ LOSS: {losses}\n"
        f"🔥 **Winrate: {winrate}%**\n"
        f"💰 **Суммарно: {total_r:+}R**"
    )
