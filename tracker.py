import sqlite3
import logging
from datetime import datetime, timezone

log = logging.getLogger("liquidity-tracker")
DB_NAME = "trades_history.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            tf TEXT,
            direction TEXT,
            entry_price REAL,
            stop_loss REAL,
            target REAL,
            rr_ratio REAL,
            status TEXT DEFAULT 'OPEN',
            created_at TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def save_signal(symbol, tf, direction, entry_price, stop_loss, target, rr_ratio):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO trades (symbol, tf, direction, entry_price, stop_loss, target, rr_ratio, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
    ''', (symbol, tf, direction, entry_price, stop_loss, target, rr_ratio, datetime.now(timezone.utc)))
    conn.commit()
    conn.close()

def check_open_trades(current_prices, telegram_callback):
    """
    current_prices = {"BTC / USDT": {"high": 84000, "low": 83000, "close": 83500}, ...}
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, symbol, tf, direction, stop_loss, target, rr_ratio FROM trades WHERE status = 'OPEN'")
    open_trades = cursor.fetchall()

    for trade in open_trades:
        trade_id, symbol, tf, direction, sl, tp, rr = trade
        if symbol not in current_prices:
            continue

        price_data = current_prices[symbol]
        high = price_data['high']
        low = price_data['low']

        status_update = None

        if direction == "LONG":
            if high >= tp:
                status_update = "WIN"
            elif low <= sl:
                status_update = "LOSS"
        elif direction == "SHORT":
            if low <= tp:
                status_update = "WIN"
            elif high >= sl:
                status_update = "LOSS"

        if status_update:
            cursor.execute("UPDATE trades SET status = ? WHERE id = ?", (status_update, trade_id))
            conn.commit()
            
            # Отправляем уведомление об отработке в Telegram
            icon = "🟢 TARGET HIT! ✅" if status_update == "WIN" else "🔴 STOP LOSS HIT ❌"
            profit_str = f"+{rr}R" if status_update == "WIN" else "-1.0R"
            
            msg = (
                f"📊 **ОТРАБОТКА СЕТАПА** [{symbol}]\n"
                f"Таймфрейм: {tf} | {direction}\n"
                f"Результат: {icon}\n"
                f"Профит: {profit_str}"
            )
            telegram_callback(msg)

    conn.close()

def get_stats_summary():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM trades WHERE status = 'WIN'")
    wins = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM trades WHERE status = 'LOSS'")
    losses = cursor.fetchone()[0]
    
    cursor.execute("SELECT SUM(rr_ratio) FROM trades WHERE status = 'WIN'")
    win_rr = cursor.fetchone()[0] or 0.0

    total = wins + losses
    winrate = round((wins / total) * 100, 1) if total > 0 else 0.0
    total_r = round(win_rr - losses, 1)

    conn.close()

    return (
        f"📈 **СТАТИСТИКА ОТРАБОТКИ СИГНАЛОВ**\n\n"
        f"🎯 Всего закрыто сетапов: {total}\n"
        f"✅ Успешных (WIN): {wins}\n"
        f"❌ Неуспешных (LOSS): {losses}\n"
        f"🔥 **Винрейт (Winrate): {winrate}%**\n"
        f"💰 **Суммарный профит: +{total_r}R**"
    )
