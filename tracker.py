import os
import time
import sqlite3
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from scanner import fmt_price

log = logging.getLogger("liquidity-tracker")
# Для Railway: подключи Volume и поставь DB_PATH=/data/trades_history.db, иначе БД стирается при деплое
DB_NAME = os.getenv("DB_PATH", "trades_history.db")
LOCAL_TZ = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Kyiv"))
PENDING_TTL_SECONDS = int(float(os.getenv("PENDING_TTL_HOURS", "4")) * 3600)  # сколько ждём откат в зону входа
M5 = 300

# Статусы: PENDING (ждём вход) -> OPEN (вошли) -> WIN | LOSS ; PENDING -> CANCELLED (не дошли до входа)


def init_db():
    conn = sqlite3.connect(DB_NAME)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, tf TEXT, direction TEXT,
            entry_price REAL, stop_loss REAL, target REAL, rr_ratio REAL,
            status TEXT DEFAULT 'PENDING',
            created_at TEXT
        )
    ''')
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
    for name, ddl in (("tp_eq", "REAL"), ("tp_eq_hit", "INTEGER DEFAULT 0"), ("fill_ts", "INTEGER")):
        if name not in cols:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {name} {ddl}")
    # сделки старой версии (вход по рынку) считаем уже открытыми
    conn.execute("UPDATE trades SET fill_ts = 0 WHERE status = 'OPEN' AND fill_ts IS NULL")
    conn.commit()
    conn.close()


def save_signal(symbol, tf, direction, entry_price, stop_loss, target, rr_ratio, tp_eq=None):
    conn = sqlite3.connect(DB_NAME)
    conn.execute('''
        INSERT INTO trades (symbol, tf, direction, entry_price, stop_loss, target, rr_ratio, status, created_at, tp_eq)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
    ''', (symbol, tf, direction, entry_price, stop_loss, target, rr_ratio,
          datetime.now(timezone.utc).isoformat(), tp_eq))
    conn.commit()
    conn.close()


def has_open_trade(symbol):
    conn = sqlite3.connect(DB_NAME)
    n = conn.execute("SELECT COUNT(*) FROM trades WHERE symbol = ? AND status IN ('PENDING','OPEN')",
                     (symbol,)).fetchone()[0]
    conn.close()
    return n > 0


def _parse_dt(value):
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _evaluate(direction, entry, sl, tp, tp_eq, status, tp_eq_hit, fill_ts, created_ts, bars, now):
    """
    Проходим M5-свечи после сигнала. Консервативные правила:
    - вход засчитывается, когда цена коснулась лимитки;
    - в свече входа проверяется только SL (порядок внутри свечи неизвестен);
    - если в одной свече задеты и SL, и TP — засчитывается стоп;
    - если до входа цена дошла до EQ (TP1) — сетап отменён, ордер не сработал.
    Возвращает (status, tp_eq_hit, fill_ts).
    """
    short = direction == "SHORT"
    for b in bars:
        if b["timestamp"] + M5 <= created_ts:
            continue  # свеча закрылась до сигнала

        if status == "PENDING":
            if b["timestamp"] - created_ts > PENDING_TTL_SECONDS:
                return "CANCELLED", tp_eq_hit, fill_ts
            touched = b["high"] >= entry if short else b["low"] <= entry
            if touched:
                status, fill_ts = "OPEN", b["timestamp"]
                if (b["high"] >= sl) if short else (b["low"] <= sl):
                    return "LOSS", tp_eq_hit, fill_ts
                continue  # в свече входа TP не проверяем
            if tp_eq is not None and ((b["low"] <= tp_eq) if short else (b["high"] >= tp_eq)):
                return "CANCELLED", tp_eq_hit, fill_ts
            continue

        # status == OPEN
        if fill_ts is not None and b["timestamp"] <= fill_ts:
            continue
        hit_sl = (b["high"] >= sl) if short else (b["low"] <= sl)
        hit_tp = (b["low"] <= tp) if short else (b["high"] >= tp)
        if hit_sl:
            return "LOSS", tp_eq_hit, fill_ts
        if tp_eq is not None and ((b["low"] <= tp_eq) if short else (b["high"] >= tp_eq)):
            tp_eq_hit = 1
        if hit_tp:
            return "WIN", 1, fill_ts

    if status == "PENDING" and now - created_ts > PENDING_TTL_SECONDS:
        return "CANCELLED", tp_eq_hit, fill_ts
    return status, tp_eq_hit, fill_ts


def check_open_trades(get_bars_fn, telegram_callback):
    """get_bars_fn(symbol) -> список свечей M5 (только проверка исхода, на стопы/тейки не влияет)."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id, symbol, tf, direction, entry_price, stop_loss, target, rr_ratio, created_at, "
                "status, tp_eq, tp_eq_hit, fill_ts FROM trades WHERE status IN ('PENDING','OPEN')")
    trades = cur.fetchall()
    bars_cache = {}
    now = time.time()

    for (trade_id, symbol, tf, direction, entry, sl, tp, rr, created_at,
         status, tp_eq, tp_eq_hit, fill_ts) in trades:
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

        tp_eq_hit = tp_eq_hit or 0
        new_status, new_eq_hit, new_fill = _evaluate(
            direction, entry, sl, tp, tp_eq, status, tp_eq_hit, fill_ts, created_ts, bars, now)

        if (new_status, new_eq_hit, new_fill) == (status, tp_eq_hit, fill_ts):
            continue

        cur.execute("UPDATE trades SET status = ?, tp_eq_hit = ?, fill_ts = ? WHERE id = ?",
                    (new_status, new_eq_hit, new_fill, trade_id))
        conn.commit()

        time_str = created_dt.astimezone(LOCAL_TZ).strftime('%Y-%m-%d %H:%M:%S')
        head = f"[{symbol}] `{direction}` | TF: `{tf}`\n💵 Вход: `{fmt_price(entry)}`\n⏱ Сигнал: `{time_str}`\n"

        if status == "PENDING" and new_status == "OPEN":
            telegram_callback(f"✅ **ОРДЕР АКТИВИРОВАН** {head}Сделка в рынке, SL: `{fmt_price(sl)}`")
        elif new_status == "CANCELLED":
            telegram_callback(f"⚪ **СЕТАП ОТМЕНЁН** {head}Цена не вернулась в зону входа (или ушла к EQ без входа). Не считается ни плюсом, ни минусом.")
        elif new_status in ("WIN", "LOSS"):
            if status == "PENDING":  # вход и выход в одном проходе — сначала сообщим об активации
                telegram_callback(f"✅ **ОРДЕР АКТИВИРОВАН** {head}Сделка в рынке, SL: `{fmt_price(sl)}`")
            icon = "🟢 TARGET HIT! ✅" if new_status == "WIN" else "🔴 STOP LOSS HIT ❌"
            profit = f"+{rr}R" if new_status == "WIN" else "-1.0R"
            eq_note = "\nEQ (TP1) по пути был достигнут." if (new_status == "LOSS" and new_eq_hit) else ""
            telegram_callback(
                f"📊 **ОТРАБОТКА СЕТАПА** {head}"
                f"Результат: {icon}\n"
                f"Профит: `{profit}`{eq_note}"
            )

    conn.close()


def get_stats_summary():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    wins = cur.execute("SELECT COUNT(*) FROM trades WHERE status = 'WIN'").fetchone()[0]
    losses = cur.execute("SELECT COUNT(*) FROM trades WHERE status = 'LOSS'").fetchone()[0]
    cancelled = cur.execute("SELECT COUNT(*) FROM trades WHERE status = 'CANCELLED'").fetchone()[0]
    eq_hits = cur.execute("SELECT COUNT(*) FROM trades WHERE status IN ('WIN','LOSS') AND tp_eq_hit = 1").fetchone()[0]
    win_rr = cur.execute("SELECT SUM(rr_ratio) FROM trades WHERE status = 'WIN'").fetchone()[0] or 0.0
    conn.close()
    total = wins + losses
    winrate = round((wins / total) * 100, 1) if total else 0.0
    total_r = round(win_rr - losses, 2)
    return (
        f"📈 **СТАТИСТИКА ОТРАБОТКИ СИГНАЛОВ**\n\n"
        f"🎯 Закрыто сделок: {total}\n"
        f"✅ WIN (TP2): {wins}\n"
        f"❌ LOSS: {losses}\n"
        f"✔️ Доходили до EQ (TP1): {eq_hits}\n"
        f"⚪ Отменено (не вошли): {cancelled}\n"
        f"🔥 **Winrate: {winrate}%**\n"
        f"💰 **Суммарно: {total_r:+}R**"
    )
