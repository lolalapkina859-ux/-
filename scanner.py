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
    """Detects 1H liquidity sweep with HTF context and R:R calculation."""
    if len(h1) < 40:
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

    # Расчет цели (Target)
    if direction == "SHORT":
        targets = [b["low"] for b in h1_closed[-24:-1] if b["low"] < level]
        target = min(targets) if targets else level * 0.99
        stop_loss = sweep_bar["high"] + (sweep_bar["high"] * 0.0005) # буфер за хай свитпа
    else:
        targets = [b["high"] for b in h1_closed[-24:-1] if b["high"] > level]
        target = max(targets) if targets else level * 1.01
        stop_loss = sweep_bar["low"] - (sweep_bar["low"] * 0.0005) # буфер за лоу свитпа

    # Расчет риска к прибыли (Risk / Reward)
    entry_price = sweep_bar["close"]
    risk = abs(entry_price - stop_loss)
    reward = abs(target - entry_price)
    rr_ratio = round(reward / risk, 1) if risk > 0 else 1.0

    # Оценка контекста старшего таймфрейма (HTF Trend по последним 20 барам)
    recent_trend_bars = h1_closed[-20:]
    htf_direction = "BULLISH 📈" if recent_trend_bars[-1]["close"] > recent_trend_bars[0]["close"] else "BEARISH 📉"
    
    # Качество сетапа (если направление совпадает с трендом HTF — высокая вероятность)
    is_trend_aligned = (direction == "LONG" and "BULLISH" in htf_direction) or (direction == "SHORT" and "BEARISH" in htf_direction)
    quality = "🌟 HIGH PROBABILITY (Trend Aligned)" if is_trend_aligned else "⚠️ COUNTER-TREND SETUP"

    return {
        "direction": direction,
        "level": level,
        "target": target,
        "stop_loss": stop_loss,
        "rr_ratio": rr_ratio,
        "htf_trend": htf_direction,
        "quality": quality,
        "bar_time": sweep_bar["timestamp"],
    }

def format_signal(symbol, event):
    target_val = event['target']
    sl_val = event['stop_loss']
    
    if target_val > 1000:
        target_str = f"{target_val:.0f}"
        sl_str = f"{sl_val:.0f}"
    else:
        target_str = f"{target_val:.5f}"
        sl_str = f"{sl_val:.5f}"

    if event['direction'] == "SHORT":
        dir_block = "🔴📉 SHORT SETUP"
    else:
        dir_block = "🟢📈 LONG SETUP"

    return (
        f"Kozak Trend 📈📉\n"
        f"📊 {symbol}\n"
        f"⚡️ 1h зняття ключової ліквідності.\n"
        f"{dir_block}\n\n"
        f"🌐 HTF Trend: {event['htf_trend']}\n"
        f"🎯 Target: {target_str}\n"
        f"🛑 Invalidation (SL): {sl_str}\n"
        f"⚖️ Est. R:R: 1:{event['rr_ratio']}\n"
        f"📌 Quality: {event['quality']}\n\n"
        f"🔴 Не лімітка! Переходимо на 3-15m для пошуку моделі входу."
    )
