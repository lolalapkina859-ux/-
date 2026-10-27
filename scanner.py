import logging
from datetime import datetime, timezone

log = logging.getLogger("liquidity-hunter")


def calculate_atr(bars, period=14):
    if not bars or len(bars) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        high, low, prev_close = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs[-period:]) / period


def _closed(bars, timeframe_seconds):
    """Drop the most recent candle because providers commonly include a live candle."""
    if not bars:
        return []
    now = datetime.now(timezone.utc).timestamp()
    # Timestamps are candle-open timestamps for both supported providers.
    return [b for b in bars if float(b.get("timestamp", 0)) + timeframe_seconds <= now]


def calculate_dol_levels(d1_bars, w1_bars=None):
    levels = {}
    d1 = _closed(d1_bars, 86400)
    w1 = _closed(w1_bars, 604800) if w1_bars else []
    if d1:
        levels["PDH"] = d1[-1]["high"]
        levels["PDL"] = d1[-1]["low"]
    if w1:
        levels["PWH"] = w1[-1]["high"]
        levels["PWL"] = w1[-1]["low"]
    return levels


def calculate_range_dynamics(bars, window=20):
    if not bars or len(bars) < window:
        return None
    recent = bars[-window:]
    high = max(b["high"] for b in recent)
    low = min(b["low"] for b in recent)
    return {"range_high": high, "range_low": low, "eq_level": (high + low) / 2.0}


def get_quarterly_phase(dt=None):
    if dt is None:
        dt = datetime.now(timezone.utc)
    hour = dt.hour
    if hour < 6:
        return "Q1 (Accumulation)"
    if hour < 12:
        return "Q2 (Manipulation)"
    if hour < 18:
        return "Q3 (Distribution)"
    return "Q4 (Reversal/Continuation)"


def calculate_daily_bias(d1_bars, h4_bars):
    bars = _closed(h4_bars or [], 14400)
    if len(bars) < 20:
        return "NEUTRAL"
    recent = bars[-1]["close"]
    prior = bars[-10]["close"]
    return "BEARISH" if recent < prior else "BULLISH" if recent > prior else "NEUTRAL"


def _confirmed_pivots(bars, left=2, right=2):
    highs, lows = [], []
    for i in range(left, len(bars) - right):
        hi = bars[i]["high"]
        lo = bars[i]["low"]
        if all(hi > bars[j]["high"] for j in range(i-left, i)) and all(hi >= bars[j]["high"] for j in range(i+1, i+right+1)):
            highs.append({"price": hi, "timestamp": bars[i]["timestamp"], "index": i})
        if all(lo < bars[j]["low"] for j in range(i-left, i)) and all(lo <= bars[j]["low"] for j in range(i+1, i+right+1)):
            lows.append({"price": lo, "timestamp": bars[i]["timestamp"], "index": i})
    return highs, lows


def _find_h1_sweep(h1_bars, lookback=24, max_age_bars=3):
    bars = _closed(h1_bars or [], 3600)
    if len(bars) < lookback + 3:
        return None
    # Search only the latest few closed H1 candles; the reference range excludes the sweep candle.
    start = max(lookback, len(bars) - max_age_bars)
    for i in range(len(bars) - 1, start - 1, -1):
        candle = bars[i]
        prior = bars[i-lookback:i]
        prior_high = max(b["high"] for b in prior)
        prior_low = min(b["low"] for b in prior)
        if candle["high"] > prior_high and candle["close"] < prior_high:
            return {"direction": "SHORT", "level": prior_high, "sweep_extreme": candle["high"], "sweep_time": candle["timestamp"], "sweep_close_time": candle["timestamp"] + 3600, "range_high": prior_high, "range_low": prior_low, "sweep_bar": candle}
        if candle["low"] < prior_low and candle["close"] > prior_low:
            return {"direction": "LONG", "level": prior_low, "sweep_extreme": candle["low"], "sweep_time": candle["timestamp"], "sweep_close_time": candle["timestamp"] + 3600, "range_high": prior_high, "range_low": prior_low, "sweep_bar": candle}
    return None


def _find_m15_choch(m15_bars, sweep, pivot_left=2, pivot_right=2):
    bars = _closed(m15_bars or [], 900)
    # Require confirmation candles to close after the H1 sweep candle has closed.
    post = [(i, b) for i, b in enumerate(bars) if b["timestamp"] >= sweep["sweep_close_time"]]
    if len(post) < 5:
        return None
    highs, lows = _confirmed_pivots(bars, pivot_left, pivot_right)
    direction = sweep["direction"]
    for original_i, candle in post:
        prior_pivots = [p for p in (lows if direction == "SHORT" else highs) if p["index"] + pivot_right < original_i and p["timestamp"] >= sweep["sweep_close_time"] - 6 * 3600]
        if not prior_pivots:
            continue
        pivot = prior_pivots[-1]
        if direction == "SHORT" and candle["close"] < pivot["price"]:
            return {"bar": candle, "bar_time": candle["timestamp"], "entry": candle["close"], "choch_level": pivot["price"]}
        if direction == "LONG" and candle["close"] > pivot["price"]:
            return {"bar": candle, "bar_time": candle["timestamp"], "entry": candle["close"], "choch_level": pivot["price"]}
    return None


def _select_htf_target(direction, entry, h1_bars, h4_bars, d1_bars, fallback_range):
    candidates = []
    h1 = _closed(h1_bars or [], 3600)
    h4 = _closed(h4_bars or [], 14400)
    d1 = _closed(d1_bars or [], 86400)
    for tf, bars in (("H1", h1[-120:]), ("H4", h4[-60:])):
        ph, pl = _confirmed_pivots(bars, 2, 2)
        pivots = ph if direction == "LONG" else pl
        for p in pivots:
            price = p["price"]
            if (direction == "LONG" and price > entry) or (direction == "SHORT" and price < entry):
                candidates.append({"price": price, "source": f"{tf} swing liquidity", "timestamp": p["timestamp"]})
    if len(d1) >= 2:
        # Previous fully closed daily candle is the second-last item if the current daily candle is present.
        prev_day = d1[-1]
        daily_level = prev_day["high"] if direction == "LONG" else prev_day["low"]
        if (direction == "LONG" and daily_level > entry) or (direction == "SHORT" and daily_level < entry):
            candidates.append({"price": daily_level, "source": "PDH" if direction == "LONG" else "PDL", "timestamp": prev_day["timestamp"]})
    if fallback_range:
        price = fallback_range["range_high"] if direction == "LONG" else fallback_range["range_low"]
        if (direction == "LONG" and price > entry) or (direction == "SHORT" and price < entry):
            candidates.append({"price": price, "source": "H1 range boundary", "timestamp": 0})
    if not candidates:
        return None
    # Nearest valid liquidity pool in the trade direction.
    return min(candidates, key=lambda c: abs(c["price"] - entry))


def check_htf_liquidity_magnet(direction, entry, target, stop_loss):
    """Reject setups where the nearest HTF target leaves less than 1R reward."""
    risk = abs(entry - stop_loss)
    reward = abs(target["price"] - entry) if target else 0
    if risk <= 0 or reward < risk:
        log.info("HTF DOL filter: rejected, target reward is below 1R")
        return False
    return True


def detect_macro_range_sweep(h4_bars, h1_bars, symbol="UNKNOWN", current_price=None, d1_bars=None, m15_bars=None):
    """H1 liquidity sweep -> post-sweep M15 CHoCH -> entry at closed confirmation candle."""
    sweep = _find_h1_sweep(h1_bars)
    if not sweep:
        return None
    confirmation = _find_m15_choch(m15_bars or [], sweep)
    if not confirmation:
        return None

    direction = sweep["direction"]
    entry = confirmation["entry"]
    h1_closed = _closed(h1_bars or [], 3600)
    h4_closed = _closed(h4_bars or [], 14400)
    d1_closed = _closed(d1_bars or [], 86400)
    atr = calculate_atr(h1_closed[-30:], 14)
    # Small volatility-based safety buffer beyond the actual wick extreme; never substitute another timeframe's extrema.
    buffer = max(atr * 0.05, abs(entry) * 0.00005)
    stop = sweep["sweep_extreme"] + buffer if direction == "SHORT" else sweep["sweep_extreme"] - buffer
    risk = abs(entry - stop)
    if risk <= 0 or (direction == "LONG" and stop >= entry) or (direction == "SHORT" and stop <= entry):
        return None

    target = _select_htf_target(direction, entry, h1_bars, h4_bars, d1_bars, sweep)
    if not target or not check_htf_liquidity_magnet(direction, entry, target, stop):
        return None
    reward = abs(target["price"] - entry)
    rr = reward / risk
    bias = calculate_daily_bias(d1_bars, h4_bars)
    return {
        "setup_type": "H1_SWEEP_M15_CHOCH", "direction": direction,
        "tf": "H1 Sweep / M15 CHoCH", "level": sweep["level"],
        "sweep_extreme": sweep["sweep_extreme"], "sweep_time": sweep["sweep_time"],
        "choch_level": confirmation["choch_level"], "bar_time": confirmation["bar_time"],
        "eq_level": (sweep["range_high"] + sweep["range_low"]) / 2,
        "daily_bias": bias, "qt_phase": get_quarterly_phase(), "entry": entry,
        "stop_loss": stop, "target_1": target["price"], "target_2": target["price"],
        "target_source": target["source"], "rr_ratio": round(rr, 2),
        "confluence": "H1 liquidity sweep + M15 close-confirmed CHoCH + HTF DOL target",
        "fvg_zone": "Not required"
    }


def detect_sweep(d1_bars, h4_bars, h1_bars, symbol="UNKNOWN", current_price=None, m15_bars=None):
    return detect_macro_range_sweep(h4_bars, h1_bars, symbol, current_price, d1_bars, m15_bars)


def format_signal(symbol, event):
    emoji = "🟢" if event["direction"] == "LONG" else "🔴"
    return (
        f"{emoji} **LIQUIDITY HUNTER FX — CONFIRMED SETUP**\n\n"
        f"🪙 **Инструмент:** `{symbol}`\n"
        f"📊 **Направление:** `{event['direction']}`\n"
        f"🧭 **HTF Bias:** `{event['daily_bias']}`\n"
        f"🎯 **HTF DOL / TP:** `{event['target_source']}`\n\n"
        f"🕐 **Sweep:** H1 | Уровень: `{event['level']:.5f}`\n"
        f"🪝 **Экстремум закола:** `{event['sweep_extreme']:.5f}`\n"
        f"🔁 **CHoCH:** M15 close за `{event['choch_level']:.5f}`\n"
        f"💵 **Вход по закрытию M15:** `{event['entry']:.5f}`\n\n"
        f"🛑 **SL за экстремумом:** `{event['stop_loss']:.5f}`\n"
        f"🎯 **TP — ближайшая ликвидность:** `{event['target_1']:.5f}`\n"
        f"📐 **Потенциальный RR:** `{event['rr_ratio']:.2f}R`\n"
    )


def format_confirmed_signal(symbol, event, current_price=None):
    return format_signal(symbol, event)
