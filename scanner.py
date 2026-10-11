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
    """Conservative MVP heuristic; alerts only, no order execution."""
    if len(h1) < 30 or len(m5) < 30:
        return None
    h1_closed = h1[:-1]
    m5_closed = m5[:-1]
    candidates = []
    start = max(2, len(h1_closed) - 30)

    for i in range(start, len(h1_closed) - 2):
        if _pivot_high(h1_closed, i):
            candidates.append(("SHORT", h1_closed[i]["high"], i))
        if _pivot_low(h1_closed, i):
            candidates.append(("LONG", h1_closed[i]["low"], i))

    candidates.sort(key=lambda x: x[2], reverse=True)
    last = h1_closed[-1]

    for direction, level, pivot_i in candidates:
        swept = any(
            (direction == "SHORT" and b["high"] > level and b["close"] < level) or
            (direction == "LONG" and b["low"] < level and b["close"] > level)
            for b in h1_closed[pivot_i + 1:]
        )
        if not swept:
            continue

        recent = m5_closed[-12:]
        prior = m5_closed[-18:-6]
        if not recent or not prior:
            continue

        if direction == "SHORT":
            structure_level = min(b["low"] for b in prior)
            confirmed = recent[-1]["close"] < structure_level
            target = min(b["low"] for b in h1_closed[-24:-1])
            target_valid = target < last["close"]
        else:
            structure_level = max(b["high"] for b in prior)
            confirmed = recent[-1]["close"] > structure_level
            target = max(b["high"] for b in h1_closed[-24:-1])
            target_valid = target > last["close"]

        if confirmed and target > 0 and target_valid:
            return {
                "direction": direction, "level": level, "target": target,
                "structure_level": structure_level, "bar_time": last["timestamp"],
                "last_price": last["close"],
            }
    return None


def format_signal(symbol, event):
    ts = datetime.fromtimestamp(event["bar_time"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    arrow = "🔴 SHORT" if event["direction"] == "SHORT" else "🟢 LONG"
    return (
        f"LIQUIDITY HUNTER FX\n{symbol} · 1H → 5M\n{arrow} SETUP\n\n"
        f"Liquidity level: {event['level']}\nPotential target: {event['target']}\n"
        f"Last H1 close: {event['last_price']}\n5M structure level: {event['structure_level']}\n"
        f"Detected: {ts}\n\nStatus: setup detected; not an order.\n"
        f"Confirm FxPro price, spread, news, invalidation and risk manually."
    )
