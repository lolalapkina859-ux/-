import sqlite3
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("liquidity-hunter")
KYIV_TZ = ZoneInfo("Europe/Kyiv")
DB_PATH = "trades_history.db"


def init_db():
    """Инициализация таблицы сделок SQLite."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                stop_loss REAL NOT NULL,
                target_1 REAL NOT NULL,
                target_2 REAL NOT NULL,
                status TEXT DEFAULT 'OPEN',
                created_at TIMESTAMP NOT NULL,
                closed_at TIMESTAMP,
                profit_r REAL DEFAULT 0.0
            )
        """)
        conn.commit()


def save_trade(symbol, direction, entry_price, stop_loss, target_1, target_2, created_at=None):
    """Сохранение нового ордера в базу данных."""
    if created_at is None:
        created_at = datetime.now(timezone.utc)

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trades (symbol, direction, entry_price, stop_loss, target_1, target_2, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN')
        """, (symbol, direction, entry_price, stop_loss, target_1, target_2, created_at.isoformat()))
        conn.commit()
        log.info(f"💾 [Tracker] Сделка сохранена в БД: {symbol} {direction} по {entry_price}")


def check_open_trades(get_latest_bars_func, send_telegram_func):
    """
    Честная проверка отработки open-сделок.
    Учитывает ТОЛЬКО свечи после момента входа (created_at).
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE status = 'OPEN'")
        open_trades = cursor.fetchall()

    if not open_trades:
        return

    for trade in open_trades:
        symbol = trade["symbol"]
        direction = trade["direction"]
        entry = trade["entry_price"]
        sl = trade["stop_loss"]
        tp = trade["target_1"]

        try:
            created_at_dt = datetime.fromisoformat(trade["created_at"])
            if created_at_dt.tzinfo is None:
                created_at_dt = created_at_dt.replace(tzinfo=timezone.utc)
        except Exception:
            created_at_dt = datetime.now(timezone.utc)

        # Запрашиваем свечи (M5 или H1)
        recent_bars = get_latest_bars_func(symbol)
        if not recent_bars:
            continue

        for bar in recent_bars:
            bar_time = bar.get("timestamp")
            if isinstance(bar_time, (int, float)):
                bar_dt = datetime.fromtimestamp(bar_time, tz=timezone.utc)
            elif isinstance(bar_time, datetime):
                bar_dt = bar_time if bar_time.tzinfo else bar_time.replace(tzinfo=timezone.utc)
            else:
                continue

            # ФИЛЬТР: Пропускаем всё, что было ДО или во время открытия сделки
            if bar_dt <= created_at_dt:
                continue

            high = bar["high"]
            low = bar["low"]

            sl_hit = False
            tp_hit = False

            if direction == "LONG":
                if low <= sl:
                    sl_hit = True
                if high >= tp:
                    tp_hit = True
            else:
                if high >= sl:
                    sl_hit = True
                if low <= tp:
                    tp_hit = True

            # Консервативное правило: если задеты оба уровня в одном баре -> STOP LOSS
            if sl_hit and tp_hit:
                close_trade(trade["id"], "CLOSED_SL", -1.0, send_telegram_func, trade, bar_dt)
                break
            elif sl_hit:
                close_trade(trade["id"], "CLOSED_SL", -1.0, send_telegram_func, trade, bar_dt)
                break
            elif tp_hit:
                risk = abs(entry - sl)
                profit_r = abs(tp - entry) / risk if risk > 0 else 2.0
                close_trade(trade["id"], "CLOSED_TP", round(profit_r, 1), send_telegram_func, trade, bar_dt)
                break


def close_trade(trade_id, status, profit_r, send_telegram_func, trade, closed_at_dt):
    """Фиксация закрытия сделки в БД и отправка уведомления."""
    now_utc = datetime.now(timezone.utc)
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE trades SET status = ?, closed_at = ?, profit_r = ? WHERE id = ?
        """, (status, now_utc.isoformat(), profit_r, trade_id))
        conn.commit()

    try:
        created_dt = datetime.fromisoformat(trade["created_at"]).astimezone(KYIV_TZ)
        created_str = created_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        created_str = trade["created_at"]

    result_emoji = "🟢 TARGET HIT! ✅" if profit_r > 0 else "🔴 STOP LOSS HIT! ❌"
    msg = (
        f"📊 **ОТРАБОТКА СЕТАПА {trade['symbol']}**\n"
        f"📈 **Направление:** `{trade['direction']}`\n"
        f"💵 **Цена входа:** `{trade['entry_price']:.5f}`\n"
        f"⏱ **Время сигнала:** `{created_str}` (Kyiv)\n"
        f"Результат: {result_emoji}\n"
        f"Профит: `{profit_r:+.1f}R`"
    )
    send_telegram_func(msg)
