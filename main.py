from datetime import datetime, timezone

def _pivot_high(bars, i, left=2, right=2):
    if i < left or i + right >= len(bars):
        return False
    high = bars[i]["high"]
    return all(high > bars[j]["high"] for j in range(i-left, i)) and all(
        high >= bars[j]["high"] for j in range(i+1, i+right+1)
    )

def _pivot_low(bars, i, left=2, right=2):
    if i < left or i + right >= len(bars):
        return False
    low = bars[i]["low"]
    return all(low < bars[j]["low"] for j in range(i-left, i)) and all(
        low <= bars[j]["low"] for j in range(i+1, i+right+1)
    )

def detect_sweep(h1, m5):
    """Detects 1H liquidity sweep and prepares setup details."""
    if len(h1) < 30:
        return None
    
    h1_closed = h1[:-1]  # exclude active forming bar
    candidates = []
    start = max(2, len(h1_closed) - 30)

    # Scan last H1 candles for a sweep of recent pivot levels
    for j in range(len(h1_closed) - 1, max(2, len(h1_closed) - 4), -1):
        sweep_bar = h1_closed[j]
        
        for i in range(start, j - 2):
            if _pivot_high(h1_closed, i):
                level = h1_closed[i]["high"]
                if sweep_bar["high"] > level and sweep_bar["close"] < level:
                    candidates.append(("SHORT", level, sweep_bar))
            if _pivot_low(h1_closed, i):
                level = h1_closed[i]["low"]
                if sweep_bar["low"] < level and sweep_bar["close"] > level:
                    candidates.append(("LONG", level, sweep_bar))

    if not candidates:
        return None

    # Take the most recent detected sweep
    direction, level, sweep_bar = candidates[0]
    last = h1_closed[-1]

    # Define target based on recent H1 extremes
    if direction == "SHORT":
        targets = [b["low"] for b in h1_closed[-24:-1] if b["low"] < level]
        target = min(targets) if targets else level * 0.99
    else:
        targets = [b["high"] for b in h1_closed[-24:-1] if b["high"] > level]
        target = max(targets) if targets else level * 1.01

    return {
        "direction": direction,
        "level": level,
        "target": target,
        "bar_time": sweep_bar["timestamp"],
        "last_price": last["close"],
    }

def format_signal(symbol, event):
    ts = datetime.fromtimestamp(event["bar_time"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    arrow = "🔴 SHORT" if event["direction"] == "SHORT" else "🟢 LONG"
    
    # Format matching your channel style
    formatted_symbol = symbol.replace("USD", " / USD").replace("XAU", "XAU / ")
    
    return (
        f"Kozak Trend 📈📉\n"
        f"📊 {formatted_symbol}\n"
        f"⚡️ 1h зняття ключової ліквідності.\n"
        f"{arrow} SETUP\n\n"
        f"🎯 Target: {event['target']}\n"
        f"Liquidity level: {event['level']}\n"
        f"Detected: {ts}\n\n"
        f"🔴 Не лімітка! Переходимо на 3-15m для пошуку моделі входу."
    )
