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
    """Detects 1H liquidity sweep."""
    if len(h1) < 30:
        return None
    
    h1_closed = h1[:-1]
    candidates = []
    start = max(2, len(h1_closed) - 30)

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

    direction, level, sweep_bar = candidates[0]

    # Calculate target
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
    }

def format_signal(symbol, event):
    # Formats number cleanly (e.g., 83500 or 1.41138)
    target_val = event['target']
    if target_val > 1000:
        target_str = f"{target_val:.0f}"
    else:
        target_str = f"{target_val:.5f}"

    return (
        f"Kozak Trend 📈📉\n"
        f"📊 {symbol}\n"
        f"⚡️ 1h зняття ключової ліквідності.\n"
        f"🎯 Target: {target_str}\n\n"
        f"🔴 Не лімітка! Переходимо на 3-15m для пошуку моделі входу."
    )
