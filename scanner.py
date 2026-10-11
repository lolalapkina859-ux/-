
from datetime import datetime, timezone


def _pivot_high(bars, i, left=2, right=2):
    if i < left or i + right >= len(bars):
        return False

    high = bars[i]["high"]

    return (
        all(high > bars[j]["high"] for j in range(i - left, i))
        and all(
            high >= bars[j]["high"]
            for j in range(i + 1, i + right + 1)
        )
    )


def _pivot_low(bars, i, left=2, right=2):
    if i < left or i + right >= len(bars):
        return False

    low = bars[i]["low"]

    return (
        all(low < bars[j]["low"] for j in range(i - left, i))
        and all(
            low <= bars[j]["low"]
            for j in range(i + 1, i + right + 1)
        )
    )


def detect_sweep(h1, m5):
    """
    H1 liquidity sweep + subsequent M5 structure break.
    Alerts only. No trade execution.
    Candle timestamps must be Unix seconds.
    Candles must be ordered oldest to newest.
    """

    if len(h1) < 35 or len(m5) < 35:
        return None

    # Exclude potentially unfinished candles.
    h1_closed = h1[:-1]
    m5_closed = m5[:-1]

    # Examine only the three latest closed H1 candles.
    first_sweep = max(0, len(h1_closed) - 3)

    selected = None

    # Search newest sweep first.
    for j in range(len(h1_closed) - 1, first_sweep - 1, -1):
        sweep_bar = h1_closed[j]
        candidates = []

        # A pivot must be confirmed before the sweep candle.
        start = max(2, j - 30)

        for i in range(start, j - 2):
            if _pivot_high(h1_closed, i):
                level = h1_closed[i]["high"]

                if (
                    sweep_bar["high"] > level
                    and sweep_bar["close"] < level
                ):
                    candidates.append(("SHORT", level, i))

            if _pivot_low(h1_closed, i):
                level = h1_closed[i]["low"]

                if (
                    sweep_bar["low"] < level
                    and sweep_bar["close"] > level
                ):
                    candidates.append(("LONG", level, i))

        if candidates:
            # Prefer the most recently formed liquidity level.
            direction, level, pivot_i = max(
                candidates, key=lambda item: item[2]
            )

            selected = {
                "direction": direction,
                "level": level,
                "pivot_i": pivot_i,
                "sweep_bar": sweep_bar,
            }
            break

    if selected is None:
        return None

    direction = selected["direction"]
    level = selected["level"]
    sweep_bar = selected["sweep_bar"]

    # Only use M5 candles formed after the sweep H1 candle closed.
    sweep_close_time = sweep_bar["timestamp"] + 3600

    after_sweep = [
        bar for bar in m5_closed
        if bar["timestamp"] >= sweep_close_time
    ]

    # Need enough post-sweep candles to evaluate structure.
    if len(after_sweep) < 7:
        return None

    last_m5 = after_sweep[-1]
    structure_bars = after_sweep[-7:-1]

    if direction == "SHORT":
        structure_level = min(
            bar["low"] for bar in structure_bars
        )
        confirmed = last_m5["close"] < structure_level

        # Nearest confirmed H1 pivot low below current price.
        targets = [
            h1_closed[i]["low"]
            for i in range(2, len(h1_closed) - 2)
            if _pivot_low(h1_closed, i)
            and h1_closed[i]["low"] < last_m5["close"]
        ]

        target = max(targets) if targets else None

    else:
        structure_level = max(
            bar["high"] for bar in structure_bars
        )
        confirmed = last_m5["close"] > structure_level

        # Nearest confirmed H1 pivot high above current price.
        targets = [
            h1_closed[i]["high"]
            for i in range(2, len(h1_closed) - 2)
            if _pivot_high(h1_closed, i)
            and h1_closed[i]["high"] > last_m5["close"]
        ]

        target = min(targets) if targets else None

    if not confirmed or target is None:
        return None

    return {
        "direction": direction,
        "level": level,
        "target": target,
        "structure_level": structure_level,
        "bar_time": last_m5["timestamp"],
        "last_price": last_m5["close"],
        "sweep_time": sweep_bar["timestamp"],
    }


def format_signal(symbol, event):
    ts = datetime.fromtimestamp(
        event["bar_time"], timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")

    arrow = (
        "🔴 SHORT"
        if event["direction"] == "SHORT"
        else "🟢 LONG"
    )

    return (
        f"LIQUIDITY HUNTER FX\n"
        f"{symbol} · 1H → 5M\n"
        f"{arrow} SETUP\n\n"
        f"Liquidity swept: {event['level']}\n"
        f"Potential target: {event['target']}\n"
        f"Last M5 close: {event['last_price']}\n"
        f"M5 structure break: {event['structure_level']}\n"
        f"Detected: {ts}\n\n"
        f"Status: setup detected; not an order.\n"
        f"Verify FxPro quotes, spread, news and risk manually."
    )
