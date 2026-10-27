import logging
from datetime import datetime, timezone

log = logging.getLogger("liquidity-hunter")


def _closed(bars, timeframe_seconds, now_ts=None):
    """Return only candles whose full timeframe has elapsed."""
    if not bars:
        return []
    now_ts = now_ts or datetime.now(timezone.utc).timestamp()
    return [b for b in bars if float(b.get("timestamp", 0)) + timeframe_seconds <= now_ts]


def calculate_atr(bars, period=14):
    if not bars or len(bars) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        high, low, prev_close = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs[-period:]) / min(period, len(trs))


def calculate_dol_levels(d1_bars, w1_bars=None):
    levels = {}
    d1 = _closed(d1_bars or [], 86400)
    w1 = _closed(w1_bars or [], 604800)
    if d1:
        levels["PDH"], levels["PDL"] = d1[-1]["high"], d1[-1]["low"]
    if w1:
        levels["PWH"], levels["PWL"] = w1[-1]["high"], w1[-1]["low"]
    return levels


def calculate_range_dynamics(bars, window=20):
    closed = _closed(bars or [], 3600)
    if len(closed) < window:
        return None
    recent = closed[-window:]
    high = max(b["high"] for b in recent)
    low = min(b["low"] for b in recent)
    return {"range_high": high, "range_low": low, "eq_level": (high + low) / 2}


def get_quarterly_phase(dt=None):
    dt = dt or datetime.now(timezone.utc)
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
    delta = bars[-1]["close"] - bars[-10]["close"]
    return "BEARISH" if delta < 0 else "BULLISH" if delta > 0 else "NEUTRAL"


def _confirmed_pivots(bars, left=2, right=2):
    highs, lows = [], []
    for i in range(left, len(bars) - right):
        hi, lo = bars[i]["high"], bars[i]["low"]
        if all(hi > bars[j]["high"] for j in range(i-left, i)) and all(hi >= bars[j]["high"] for j in range(i+1, i+right+1)):
            highs.append({"price": hi, "timestamp": bars[i]["timestamp"], "index": i})
        if all(lo < bars[j]["low"] for j in range(i-left, i)) and all(lo <= bars[j]["low"] for j in range(i+1, i+right+1)):
            lows.append({"price": lo, "timestamp": bars[i]["timestamp"], "index": i})
    return highs, lows


def _find_h1_sweep(h1_bars, lookback=24, max_age_bars=3):
    bars = _closed(h1_bars or [], 3600)
    if len(bars) < lookback + 3:
        return None
    # Newest eligible closed H1 candle first. Reference window excludes sweep candle.
    start = max(lookback, len(bars) - max_age_bars)
    for i in range(len(bars) - 1, start - 1, -1):
        candle = bars[i]
        prior = bars[i-lookback:i]
        prior_high = max(b["high"] for b in prior)
        prior_low = min(b["low"] for b in prior)
        if candle["high"] > prior_high and candle["close"] < prior_high:
            return {"direction":"SHORT", "level":prior_high, "sweep_extreme":candle["high"], "sweep_time":candle["timestamp"], "sweep_close_time":candle["timestamp"]+3600, "range_high":prior_high, "range_low":prior_low}
        if candle["low"] < prior_low and candle["close"] > prior_low:
            return {"direction":"LONG", "level":prior_low, "sweep_extreme":candle["low"], "sweep_time":candle["timestamp"], "sweep_close_time":candle["timestamp"]+3600, "range_high":prior_high, "range_low":prior_low}
    return None


def _find_m5_choch(m5_bars, sweep, pivot_left=2, pivot_right=2, max_age_seconds=600):
    """Return only a recent, closed M5 structure break after the H1 sweep closed."""
    bars = _closed(m5_bars or [], 300)
    if len(bars) < 10:
        return None
    now = datetime.now(timezone.utc).timestamp()
    highs, lows = _confirmed_pivots(bars, pivot_left, pivot_right)
    direction = sweep["direction"]
    # Inspect newest candles first; do not revive an old CHoCH from the history.
    for i in range(len(bars) - 1, max(0, len(bars) - 4) - 1, -1):
        candle = bars[i]
        if candle["timestamp"] < sweep["sweep_close_time"] or now - (candle["timestamp"] + 300) > max_age_seconds:
            continue
        pivots = lows if direction == "SHORT" else highs
        prior = [p for p in pivots if p["index"] + pivot_right < i and p["timestamp"] >= sweep["sweep_close_time"] - 6*3600]
        if not prior:
            continue
        # Most recent confirmed pivot before this candle.
        pivot = max(prior, key=lambda p: p["index"])
        # Require a cross on this candle, not merely a close that remained beyond the level.
        prev_close = bars[i-1]["close"] if i > 0 else candle["open"]
        if direction == "SHORT" and prev_close >= pivot["price"] and candle["close"] < pivot["price"]:
            return {"bar":candle, "bar_time":candle["timestamp"], "entry":candle["close"], "choch_level":pivot["price"]}
        if direction == "LONG" and prev_close <= pivot["price"] and candle["close"] > pivot["price"]:
            return {"bar":candle, "bar_time":candle["timestamp"], "entry":candle["close"], "choch_level":pivot["price"]}
    return None


def _select_htf_target(direction, entry, h1_bars, h4_bars, d1_bars, fallback_range):
    candidates = []
    for tf, source_bars, seconds, limit in (("H1", h1_bars, 3600, 120), ("H4", h4_bars, 14400, 60)):
        bars = _closed(source_bars or [], seconds)[-limit:]
        ph, pl = _confirmed_pivots(bars, 2, 2)
        for p in (ph if direction == "LONG" else pl):
            price = p["price"]
            if (direction == "LONG" and price > entry) or (direction == "SHORT" and price < entry):
                candidates.append({"price":price, "source":f"{tf} swing liquidity", "timestamp":p["timestamp"]})
    d1 = _closed(d1_bars or [], 86400)
    if d1:
        daily_level = d1[-1]["high"] if direction == "LONG" else d1[-1]["low"]
        if (direction == "LONG" and daily_level > entry) or (direction == "SHORT" and daily_level < entry):
            candidates.append({"price":daily_level, "source":"PDH" if direction == "LONG" else "PDL", "timestamp":d1[-1]["timestamp"]})
    if fallback_range:
        price = fallback_range["range_high"] if direction == "LONG" else fallback_range["range_low"]
        if (direction == "LONG" and price > entry) or (direction == "SHORT" and price < entry):
            candidates.append({"price":price, "source":"H1 range boundary", "timestamp":0})
    return min(candidates, key=lambda c: abs(c["price"]-entry)) if candidates else None


def classify_bias_alignment(direction, bias):
    if bias not in ("BULLISH", "BEARISH"):
        return "NEUTRAL BIAS"
    trend = "LONG" if bias == "BULLISH" else "SHORT"
    return "TREND-FOLLOWING" if direction == trend else "COUNTERTREND"


def check_htf_liquidity_magnet(direction, entry, target, stop_loss, min_rr=1.0):
    risk = abs(entry-stop_loss)
    if not target or risk <= 0:
        return False
    valid = target["price"] > entry if direction == "LONG" else target["price"] < entry
    return valid and abs(target["price"]-entry)/risk >= min_rr


def detect_macro_range_sweep(h4_bars, h1_bars, symbol="UNKNOWN", current_price=None, d1_bars=None, m5_bars=None, spread_buffer=0.0):
    sweep = _find_h1_sweep(h1_bars)
    if not sweep:
        return None
    confirmation = _find_m5_choch(m5_bars or [], sweep)
    if not confirmation:
        return None
    direction, entry = sweep["direction"], confirmation["entry"]
    h1_closed = _closed(h1_bars or [], 3600)
    atr = calculate_atr(h1_closed[-30:], 14)
    volatility_buffer = max(atr*0.05, abs(entry)*0.00005)
    spread_buffer = max(0.0, float(spread_buffer or 0.0))
    stop = sweep["sweep_extreme"] + volatility_buffer + spread_buffer if direction == "SHORT" else sweep["sweep_extreme"] - volatility_buffer - spread_buffer
    if (direction == "LONG" and stop >= entry) or (direction == "SHORT" and stop <= entry):
        return None
    # Fail closed if caller supplied a quote and it already crossed SL/TP or entry drift is excessive.
    if current_price is not None:
        cp = float(current_price)
        if direction == "LONG" and cp <= stop or direction == "SHORT" and cp >= stop:
            log.info("Reject %s: current price has already crossed SL", symbol)
            return None
        if direction == "LONG" and cp <= entry or direction == "SHORT" and cp >= entry:
            log.info("Reject %s: price has moved back through the signal entry; wait for a new setup", symbol)
            return None
        max_drift = max(calculate_atr(_closed(m5_bars or [], 300)[-20:], 14)*0.75, abs(entry)*0.0002)
        if abs(cp-entry) > max_drift:
            log.info("Reject %s: entry is stale (quote drift %.8f > %.8f)", symbol, abs(cp-entry), max_drift)
            return None
    target = _select_htf_target(direction, entry, h1_bars, h4_bars, d1_bars or [], sweep)
    if not target or not check_htf_liquidity_magnet(direction, entry, target, stop):
        return None
    rr = abs(target["price"]-entry)/abs(entry-stop)
    bias = calculate_daily_bias(d1_bars or [], h4_bars or [])
    alignment = classify_bias_alignment(direction, bias)
    return {
        "setup_type":"H1_SWEEP_M5_CHOCH", "direction":direction, "bias_alignment":alignment,
        "countertrend_confirmed":alignment == "COUNTERTREND", "dol_direction":direction,
        "tf":"H1 Sweep / M5 CHoCH", "confirmation_tf":"M5", "level":sweep["level"],
        "sweep_extreme":sweep["sweep_extreme"], "sweep_time":sweep["sweep_time"],
        "choch_level":confirmation["choch_level"], "bar_time":confirmation["bar_time"],
        "eq_level":(sweep["range_high"]+sweep["range_low"])/2, "daily_bias":bias,
        "qt_phase":get_quarterly_phase(), "entry":entry, "stop_loss":stop,
        "target_1":target["price"], "target_2":target["price"],
        "volatility_buffer":volatility_buffer, "spread_buffer":spread_buffer,
        "target_source":target["source"], "rr_ratio":round(rr,2),
        "confluence":"H1 sweep + fresh closed M5 CHoCH + HTF liquidity target",
        "fvg_zone":"Not required", "signal_timestamp":confirmation["bar_time"]+300,
        "quote_at_signal":current_price
    }


def detect_sweep(d1_bars, h4_bars, h1_bars, symbol="UNKNOWN", current_price=None, m5_bars=None, spread_buffer=0.0):
    return detect_macro_range_sweep(h4_bars, h1_bars, symbol, current_price, d1_bars, m5_bars, spread_buffer)


def format_signal(symbol, event):
    emoji = "🟢" if event["direction"] == "LONG" else "🔴"
    signal_time = datetime.fromtimestamp(event.get("signal_timestamp", 0), timezone.utc).strftime("%H:%M:%S UTC") if event.get("signal_timestamp") else "unknown"
    return (
        f"{emoji} **LIQUIDITY HUNTER FX — CONFIRMED SETUP**\n\n"
        f"🪙 **Инструмент:** `{symbol}`\n📊 **Направление:** `{event['direction']}`\n"
        f"🧭 **HTF Bias:** `{event['daily_bias']}`\n🧩 **Alignment:** `{event['bias_alignment']}`\n"
        f"🎯 **HTF Target:** `{event['target_source']}`\n🕒 **Время CHoCH:** `{signal_time}`\n\n"
        f"🕐 **H1 Sweep level:** `{event['level']:.5f}`\n🪝 **Sweep extreme:** `{event['sweep_extreme']:.5f}`\n"
        f"🔁 **M5 CHoCH:** `{event['choch_level']:.5f}`\n💵 **Entry (M5 close):** `{event['entry']:.5f}`\n"
        f"🛑 **SL:** `{event['stop_loss']:.5f}`\n↔️ **Spread reserve:** `{event['spread_buffer']:.5f}` (estimate, not live broker spread)\n"
        f"🎯 **TP1:** `{event['target_1']:.5f}`\n📐 **Potential RR:** `{event['rr_ratio']:.2f}R`\n\n"
        f"⚠️ Indicative signal only; verify current broker quote before entry."
    )


def format_confirmed_signal(symbol, event, current_price=None):
    return format_signal(symbol, event)
