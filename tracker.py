import sqlite3
import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger("liquidity-tracker")
DB_NAME = "trades_history.db"


def _connect():
    conn = sqlite3.connect(DB_NAME, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with _connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, tf TEXT, direction TEXT,
            entry_price REAL, stop_loss REAL, target REAL, rr_ratio REAL,
            status TEXT DEFAULT 'OPEN', created_at TEXT, signal_timestamp REAL,
            close_price REAL, closed_at TEXT, result_r REAL, note TEXT
        )''')
        existing = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
        additions = {
            "signal_timestamp": "REAL", "close_price": "REAL", "closed_at": "TEXT",
            "result_r": "REAL", "note": "TEXT"
        }
        for name, kind in additions.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE trades ADD COLUMN {name} {kind}")


def save_signal(symbol, tf, direction, entry_price, stop_loss, target, rr_ratio, signal_timestamp=None, note=None):
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute('''INSERT INTO trades
            (symbol, tf, direction, entry_price, stop_loss, target, rr_ratio, status, created_at, signal_timestamp, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)''',
            (symbol, tf, direction, entry_price, stop_loss, target, rr_ratio, now, signal_timestamp, note))


def check_open_trades(current_prices, telegram_callback, max_age_hours=24):
    """Track indicative levels from market bars. If both SL and TP fall inside one bar, mark AMBIGUOUS."""
    now = datetime.now(timezone.utc)
    with _connect() as conn:
        rows = conn.execute('''SELECT id, symbol, tf, direction, entry_price, stop_loss, target, rr_ratio,
            created_at, signal_timestamp FROM trades WHERE status='OPEN' ''').fetchall()
        for row in rows:
            trade_id, symbol, tf, direction, entry, sl, tp, rr, created_at, signal_ts = row
            if symbol not in current_prices:
                continue
            try:
                created = datetime.fromisoformat(created_at) if created_at else now
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
            except Exception:
                created = now
            if now - created > timedelta(hours=max_age_hours):
                conn.execute("UPDATE trades SET status='EXPIRED', closed_at=?, note=COALESCE(note,'') || ? WHERE id=?",
                             (now.isoformat(), ' | expired without verified outcome', trade_id))
                telegram_callback(f"⌛ **СЕТАП ПРОСРОЧЕН** [{symbol}]\nНаправление: `{direction}`\nСигнал не достиг TP/SL в пределах {max_age_hours} ч. Результат не засчитан.")
                continue
            p = current_prices[symbol]
            # Ignore bars that opened before the signal became actionable.
            bar_ts = p.get('bar_timestamp')
            signal_ts = signal_ts or (created.timestamp() if created else None)
            if bar_ts is not None and signal_ts is not None and float(bar_ts) < float(signal_ts):
                continue
            high, low = float(p['high']), float(p['low'])
            if direction == 'LONG':
                hit_tp, hit_sl = high >= tp, low <= sl
            else:
                hit_tp, hit_sl = low <= tp, high >= sl
            if hit_tp and hit_sl:
                status, result, label = 'AMBIGUOUS', None, '⚠️ SL и TP внутри одной свечи — порядок неизвестен'
            elif hit_tp:
                status, result, label = 'WIN', float(rr or 0), '🟢 TARGET HIT'
            elif hit_sl:
                status, result, label = 'LOSS', -1.0, '🔴 STOP LOSS HIT'
            else:
                continue
            close_price = tp if status == 'WIN' else sl if status == 'LOSS' else None
            conn.execute("UPDATE trades SET status=?, close_price=?, closed_at=?, result_r=? WHERE id=?",
                         (status, close_price, now.isoformat(), result, trade_id))
            time_str = created.astimezone(timezone(timedelta(hours=3))).strftime('%Y-%m-%d %H:%M:%S')
            result_str = f"{result:+.2f}R" if result is not None else "не учитывается в статистике"
            telegram_callback(
                f"📊 **ОТРАБОТКА СЕТАПА** [{symbol}]\n📈 Направление: `{direction}` | TF: `{tf}`\n"
                f"💵 Расчётный Entry: `{entry:.5f}`\n⏱ Время сигнала: `{time_str} UTC+3`\n"
                f"Результат: {label}\nРезультат модели: `{result_str}`\n"
                f"⚠️ Это проверка уровней по свечам, не подтверждение реального исполнения."
            )


def get_stats_summary():
    with _connect() as conn:
        wins = conn.execute("SELECT COUNT(*) FROM trades WHERE status='WIN'").fetchone()[0]
        losses = conn.execute("SELECT COUNT(*) FROM trades WHERE status='LOSS'").fetchone()[0]
        ambiguous = conn.execute("SELECT COUNT(*) FROM trades WHERE status='AMBIGUOUS'").fetchone()[0]
        expired = conn.execute("SELECT COUNT(*) FROM trades WHERE status='EXPIRED'").fetchone()[0]
        net_r = conn.execute("SELECT COALESCE(SUM(result_r),0) FROM trades WHERE status IN ('WIN','LOSS')").fetchone()[0]
    total = wins + losses
    winrate = round(wins / total * 100, 1) if total else 0.0
    return (f"📈 **СТАТИСТИКА СИГНАЛОВ (МОДЕЛЬНАЯ)**\n\n"
            f"🎯 Закрыто однозначно: {total}\n✅ WIN: {wins}\n❌ LOSS: {losses}\n"
            f"⚠️ Неоднозначные (SL и TP в одной свече): {ambiguous}\n⌛ Просрочено без результата: {expired}\n"
            f"🔥 Winrate: {winrate}%\n💰 Суммарный результат: `{net_r:+.2f}R`\n\n"
            f"*Статистика по свечным касаниям, не по реальным исполненным сделкам.*")
