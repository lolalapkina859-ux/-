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

def _check_timeframe_sweep(bars, tf_name):
    if len(bars) < 30:
        return None
    
    closed_bars = bars[:-1]
    candidates = []
    start = max(2, len(closed_bars) - 30)

    for j in range(len(closed_bars) - 1, max(2, len(closed_bars) - 4), -1):
        sweep_bar = closed_bars[j]
        
        for i in range(start, j - 2):
            if _pivot_high(closed_bars, i):
                level = closed_bars[i]["high"]
                if sweep_bar["high"] > level and sweep_bar["close"] < level:
                    candidates.append(("SHORT", level, sweep_bar))
            if _pivot_low(closed_bars, i):
                level = closed_bars[i]["low"]
                if sweep_bar["low"] < level and sweep_bar["close"] > level:
                    candidates.append(("LONG", level, sweep_bar))

    if not candidates:
        return None

    direction, level, sweep_bar = candidates[0]

    # Target & Stop Loss calculation
    if direction == "SHORT":
        targets = [b["low"] for b in closed_bars[-24:-1] if b["low"] < level]
        target = min(targets) if targets else level * 0.99
        stop_loss = sweep_bar["high"] + (sweep_bar["high"] * 0.0005)
    else:
        targets = [b["high"] for b in closed_bars[-24:-1] if b["high"] > level]
        target = max(targets) if targets else level * 1.01
        stop_loss = sweep_bar["low"] - (sweep_bar["low"] * 0.0005)

    entry_price = sweep_bar["close"]
    risk = abs(entry_price - stop_loss)
    reward = abs(target - entry_price)
    rr_ratio = round(reward / risk, 1) if risk > 0 else 1.0

    return {
        "tf": tf_name,
        "direction": direction,
        "level": level,
        "target": target,
        "stop_loss": stop_loss,
        "rr_ratio": rr_ratio,
        "bar_time": sweep_bar["timestamp"],
    }

def detect_sweep(d1, h4, h1):
    """Сканирует 1D, 4H и 1H на выбор лучшего свитпа"""
    # 1. Сначала проверяем 1D (самый приоритетный)
    sweep_1d = _check_timeframe_sweep(d1, "1D")
    if sweep_1d:
        sweep_1d["quality"] = "🏆 MAJOR SETUP (1D Sweep)"
        return sweep_1d

    # 2. Затем проверяем 4H
    sweep_4h = _check_timeframe_sweep(h4, "4h")
    if sweep_4h:
        sweep_4h["quality"] = "🔥 STRONG SETUP (4h Sweep)"
        return sweep_4h

    # 3. Затем 1H
    sweep_1h = _check_timeframe_sweep(h1, "1h")
    if sweep_1h:
        recent_trend = h1[:-1][-20:]
        is_bull = recent_trend[-1]["close"] > recent_trend[0]["close"]
        aligned = (sweep_1h["direction"] == "LONG" and is_bull) or (sweep_1h["direction"] == "SHORT" and not is_bull)
        sweep_1h["quality"] = "🌟 HIGH PROBABILITY (Trend Aligned)" if aligned else "⚠️ COUNTER-TREND SETUP"
        return sweep_1h

    return None

def format_signal(symbol, event):
    target_val = event['target']
    sl_val = event['stop_loss']
    
    if target_val > 1000:
        target_str = f"{target_val:.0f}"
        sl_str = f"{sl_val:.0f}"
    else:
        target_str = f"{target_val:.5f}"
        sl_str = f"{sl_val:.5f}"

    dir_block = "🔴📉 SHORT SETUP" if event['direction'] == "SHORT" else "🟢📈 LONG SETUP"
    tf_str = event.get('tf', '1h')

    return (
        f"Kozak Trend 📈📉\n"
        f"📊 {symbol}\n"
        f"⚡️ [{tf_str}] зняття ключової ліквідності.\n"
        f"{dir_block}\n\n"
        f"🎯 Target: {target_str}\n"
        f"🛑 Invalidation (SL): {sl_str}\n"
        f"⚖️ Est. R:R: 1:{event['rr_ratio']}\n"
        f"📌 Quality: {event['quality']}\n\n"
        f"🔴 Не лімітка! Переходимо на 3-15m для пошуку моделі входу."
    )
