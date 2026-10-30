import os
import logging
from datetime import datetime, timezone

log = logging.getLogger("liquidity-hunter")

# ==========================================
# НАСТРОЙКИ (можно менять через Railway Variables)
# ==========================================
SL_ATR_MULT = float(os.getenv("SL_ATR_MULT", "0.4"))          # буфер стопа за уровнем = 0.4 * ATR(H4)
MIN_RR = float(os.getenv("MIN_RR", "1.5"))                    # сигналы с RR ниже — не отправляются
TP_MODE = os.getenv("TP_MODE", "range").strip().lower()       # "range" | "nearest"
MAGNET_ATR_MULT = float(os.getenv("MAGNET_ATR_MULT", "1.0"))  # радиус HTF-магнита в ATR(H4)
SWEEP_TOLERANCE = 0.0002                                      # допуск касания границы диапазона (0.02%)


# ==========================================
# 1. ИНДИКАТОРЫ И ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================

def calculate_atr(bars, period=14):
    """Average True Range."""
    if len(bars) < period + 1:
        return 0.0
    tr_list = []
    for i in range(1, len(bars)):
        high = bars[i]["high"]
        low = bars[i]["low"]
        prev_close = bars[i - 1]["close"]
        tr_list.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(tr_list[-period:]) / period


def fmt_price(p):
    p = float(p)
    if abs(p) >= 1000:
        return f"{p:.2f}"
    if abs(p) >= 10:
        return f"{p:.3f}"
    return f"{p:.5f}"


# ==========================================
# 2. DOL / ДИАПАЗОН / BIAS / QT
# ==========================================

def calculate_dol_levels(d1_bars, w1_bars=None):
    levels = {}
    if d1_bars and len(d1_bars) >= 2:
        prev_day = d1_bars[-2]
        levels["PDH"] = prev_day["high"]
        levels["PDL"] = prev_day["low"]
    if w1_bars and len(w1_bars) >= 2:
        prev_week = w1_bars[-2]
        levels["PWH"] = prev_week["high"]
        levels["PWL"] = prev_week["low"]
    return levels


def calculate_range_dynamics(bars, window=20):
    if not bars or len(bars) < window:
        return None
    recent = bars[-window:]
    range_high = max(b["high"] for b in recent)
    range_low = min(b["low"] for b in recent)
    return {"range_high": range_high, "range_low": range_low,
            "eq_level": (range_high + range_low) / 2.0}


def get_quarterly_phase(dt=None):
    if dt is None:
        dt = datetime.now(timezone.utc)
    hour = dt.hour
    if 0 <= hour < 6:
        return "Q1 (Accumulation)"
    elif 6 <= hour < 12:
        return "Q2 (Manipulation)"
    elif 12 <= hour < 18:
        return "Q3 (Distribution)"
    return "Q4 (Reversal/Continuation)"


def calculate_daily_bias(d1_bars, h4_bars):
    if not h4_bars or len(h4_bars) < 20:
        return "NEUTRAL"
    if h4_bars[-1]["close"] < h4_bars[-10]["close"]:
        return "BEARISH"
    return "BULLISH"


# ==========================================
# 3. ПУЛЫ ЛИКВИДНОСТИ (свинги H1/H4, PDH/PDL)
# ==========================================

def find_swing_pools(bars, left=2, right=2, lookback=60):
    """
    Нетронутые (unswept) свинг-хаи и свинг-лоу — это и есть пулы ликвидности.
    Свинг считается снятым, если любая более поздняя свеча зашла за него.
    """
    if not bars or len(bars) < left + right + 1:
        return [], []
    start = max(left, len(bars) - lookback)
    highs, lows = [], []
    for i in range(start, len(bars) - right):
        hi, lo = bars[i]["high"], bars[i]["low"]
        left_bars = bars[i - left:i]
        right_bars = bars[i + 1:i + 1 + right]
        later = bars[i + 1:]
        if all(hi > b["high"] for b in left_bars) and all(hi >= b["high"] for b in right_bars):
            if max(b["high"] for b in later) <= hi:
                highs.append(hi)
        if all(lo < b["low"] for b in left_bars) and all(lo <= b["low"] for b in right_bars):
            if min(b["low"] for b in later) >= lo:
                lows.append(lo)
    return highs, lows


def collect_pools(h4_bars, h1_bars=None, d1_bars=None):
    h4_hi, h4_lo = find_swing_pools(h4_bars, 2, 2, 60)
    htf_highs = [(p, "H4 swing high") for p in h4_hi]
    htf_lows = [(p, "H4 swing low") for p in h4_lo]

    if d1_bars and len(d1_bars) >= 2:
        pdh, pdl = d1_bars[-2]["high"], d1_bars[-2]["low"]
        today = d1_bars[-1]
        if today["high"] <= pdh:
            htf_highs.append((pdh, "PDH"))
        if today["low"] >= pdl:
            htf_lows.append((pdl, "PDL"))

    h1_highs, h1_lows = [], []
    if h1_bars:
        hi, lo = find_swing_pools(h1_bars, 3, 3, 72)
        h1_highs = [(p, "H1 swing high") for p in hi]
        h1_lows = [(p, "H1 swing low") for p in lo]

    return {"htf_highs": htf_highs, "htf_lows": htf_lows,
            "h1_highs": h1_highs, "h1_lows": h1_lows}


def check_htf_liquidity_magnet(direction, level, atr, pools):
    """
    ФИЛЬТР СТАРШЕГО МАГНИТА (HTF DOL).
    SHORT после снятия хая: если ВЫШЕ уровня закола лежит нетронутый HTF-пул
    (H4 swing high / PDH) ближе MAGNET_ATR_MULT * ATR(H4) — цена, скорее всего,
    тянется к нему, а стоп стоит прямо на пути. Сигнал блокируется.
    LONG — зеркально по лоу.
    Возвращает (ok, причина).
    """
    radius = atr * MAGNET_ATR_MULT
    if direction == "SHORT":
        above = [(p, lbl) for p, lbl in pools["htf_highs"] if p > level * (1 + SWEEP_TOLERANCE)]
        if above:
            p, lbl = min(above, key=lambda x: x[0])
            if p - level <= radius:
                return False, f"магнит сверху: {lbl} {fmt_price(p)}"
    else:
        below = [(p, lbl) for p, lbl in pools["htf_lows"] if p < level * (1 - SWEEP_TOLERANCE)]
        if below:
            p, lbl = max(below, key=lambda x: x[0])
            if level - p <= radius:
                return False, f"магнит снизу: {lbl} {fmt_price(p)}"
    return True, None


# ==========================================
# 4. ВЫБОР ТЕЙКА
# ==========================================

def choose_target(direction, entry, risk, range_low, range_high, pools):
    """
    TP_MODE="range"   -> противоположная граница диапазона 24H (классика).
    TP_MODE="nearest" -> ближайший нетронутый пул ликвидности (H1/H4 swing, PDH/PDL),
                         дающий RR >= MIN_RR; если такого нет — граница диапазона.
    Возвращает (tp, подпись, tp2).
    """
    if direction == "SHORT":
        boundary = range_low if range_low < entry else None
        cands = [(p, l) for p, l in pools["htf_lows"] + pools["h1_lows"] if p < entry]
        cands.sort(key=lambda x: -x[0])      # ближайшие к цене первыми
        label = "нижняя граница диапазона 24H"
    else:
        boundary = range_high if range_high > entry else None
        cands = [(p, l) for p, l in pools["htf_highs"] + pools["h1_highs"] if p > entry]
        cands.sort(key=lambda x: x[0])
        label = "верхняя граница диапазона 24H"

    if TP_MODE == "nearest":
        for price, lbl in cands:
            if abs(price - entry) / risk >= MIN_RR:
                far = boundary if boundary is not None and (
                    (direction == "SHORT" and boundary < price) or
                    (direction == "LONG" and boundary > price)) else None
                return price, lbl, far

    if boundary is not None:
        return boundary, label, None
    return None, None, None


# ==========================================
# 5. ДЕТЕКТОР: SWEEP -> СТОП ЗА УРОВНЕМ -> ТЕЙК К ЛИКВИДНОСТИ
# ==========================================

def detect_macro_range_sweep(h4_bars, h1_bars, symbol="UNKNOWN", current_price=None, d1_bars=None):
    if not h4_bars or len(h4_bars) < 20 or not h1_bars or len(h1_bars) < 24:
        return None

    h4_curr = h4_bars[-1]
    entry = current_price if current_price and current_price > 0 else h1_bars[-1]["close"]

    rd = calculate_range_dynamics(h1_bars, window=24)
    if not rd:
        return None
    macro_high, macro_low, eq_level = rd["range_high"], rd["range_low"], rd["eq_level"]

    if h4_curr["high"] >= macro_high * (1 - SWEEP_TOLERANCE):
        direction, level = "SHORT", macro_high
    elif h4_curr["low"] <= macro_low * (1 + SWEEP_TOLERANCE):
        direction, level = "LONG", macro_low
    else:
        return None

    atr = calculate_atr(h4_bars, 14)
    if atr <= 0:
        atr = level * 0.003

    pools = collect_pools(h4_bars, h1_bars, d1_bars)

    ok, reason = check_htf_liquidity_magnet(direction, level, atr, pools)
    if not ok:
        log.info(f"🛡 [{symbol}] {direction} отфильтрован HTF-магнитом ({reason})")
        return None

    # СТОП СТРОГО ЗА УРОВНЕМ ЗАКОЛА + буфер на ATR(H4)
    buffer = atr * SL_ATR_MULT
    if direction == "SHORT":
        stop_loss = level + buffer
        risk = stop_loss - entry
    else:
        stop_loss = level - buffer
        risk = entry - stop_loss
    if risk <= 0:
        return None

    tp, tp_label, tp2 = choose_target(direction, entry, risk, macro_low, macro_high, pools)
    if tp is None:
        log.info(f"[{symbol}] нет цели с нужной стороны от входа — пропуск")
        return None

    rr = abs(tp - entry) / risk
    if rr < MIN_RR:
        log.info(f"[{symbol}] {direction}: RR {rr:.2f} < {MIN_RR} — пропуск")
        return None

    log.info(f"🚨 [{symbol}] SWEEP {direction} уровень {fmt_price(level)} RR {rr:.2f}")

    return {
        "setup_type": "PRICE_ACTION_SWEEP",
        "direction": direction,
        "tf": "1H / 4H Price Action",
        "level": level,
        "eq_level": eq_level,
        "daily_bias": calculate_daily_bias(d1_bars, h4_bars) if d1_bars else "NEUTRAL",
        "qt_phase": get_quarterly_phase(),
        "entry": entry,
        "stop_loss": stop_loss,
        "target_1": tp,
        "target_2": tp2,
        "tp_label": tp_label,
        "rr_ratio": round(rr, 2),
        "atr_h4": atr,
        "bar_time": h4_curr["timestamp"],
    }


def detect_sweep(d1_bars, h4_bars, h1_bars, symbol="UNKNOWN", current_price=None):
    return detect_macro_range_sweep(h4_bars, h1_bars, symbol, current_price, d1_bars)


# ==========================================
# 6. ФОРМАТ СООБЩЕНИЯ
# ==========================================

def format_signal(symbol, event, time_str=""):
    msg = (
        f"🚨 **PRICE ACTION SIGNAL: {symbol}** 🚨\n\n"
        f"📈 Направление: `{event['direction']}`\n"
        f"🎯 Триггер: Снятие ликвидности диапазона 24H (H1/H4)\n"
        f"📍 Уровень закола: `{fmt_price(event['level'])}`\n"
        f"💵 Цена входа: `{fmt_price(event['entry'])}`\n"
        f"🛡 Стоп-Лосс (SL): `{fmt_price(event['stop_loss'])}` (за уровнем + {SL_ATR_MULT} ATR H4)\n"
        f"🎯 Тейк-Профит (TP): `{fmt_price(event['target_1'])}` ({event['tp_label']})\n"
    )
    if event.get("target_2") is not None:
        msg += f"🚀 TP2: `{fmt_price(event['target_2'])}` (граница диапазона)\n"
    msg += (
        f"⚖️ RR: `1:{event['rr_ratio']}`\n"
        f"📅 Bias: `{event['daily_bias']}` | ⌛ QT: `{event['qt_phase']}`\n"
    )
    if time_str:
        msg += f"⏱ Время: {time_str}"
    return msg
