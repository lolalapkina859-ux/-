import logging
from datetime import datetime, timezone

log = logging.getLogger("liquidity-hunter")


# ==========================================
# 1. ТЕХНИЧЕСКИЕ ИНДИКАТОРЫ И ВПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================

def calculate_atr(bars, period=14):
    """Расчет Average True Range (ATR)."""
    if not bars or len(bars) < period + 1:
        return 0.0
    tr_list = []
    for i in range(1, len(bars)):
        high = bars[i]["high"]
        low = bars[i]["low"]
        prev_close = bars[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)
    return sum(tr_list[-period:]) / period if tr_list else 0.0


def calculate_range_dynamics(bars, window=24):
    """Динамика диапазона (Range High, Range Low, Equilibrium)."""
    if not bars or len(bars) < window:
        return None

    recent_bars = bars[-window:]
    range_high = max(b["high"] for b in recent_bars)
    range_low = min(b["low"] for b in recent_bars)
    eq_level = (range_high + range_low) / 2.0

    return {
        "range_high": range_high,
        "range_low": range_low,
        "eq_level": eq_level
    }


def calculate_daily_bias(h4_bars):
    """Определение Daily/4H Bias по структуре рынка."""
    if not h4_bars or len(h4_bars) < 10:
        return "NEUTRAL"
    if h4_bars[-1]["close"] < h4_bars[-10]["close"]:
        return "BEARISH"
    return "BULLISH"


# ==========================================
# 2. ФИЛЬТР СТАРШЕГО МАГНИТА (HTF DOL)
# ==========================================

def check_htf_liquidity_magnet(direction, current_price, h4_bars):
    """
    Проверяет, не идет ли сделка прямо навстречу крупному несобранному пулу 4H.
    """
    if not h4_bars or len(h4_bars) < 40:
        return True

    h4_recent = h4_bars[-40:]
    htf_high = max(b["high"] for b in h4_recent)
    htf_low = min(b["low"] for b in h4_recent)

    if direction == "LONG":
        distance_to_low = current_price - htf_low
        if 0 < distance_to_low < (current_price * 0.003):
            log.info(f"🚫 [DOL Filter] LONG отфильтрован: рядом 4H магнит на {htf_low:.5f}")
            return False
    elif direction == "SHORT":
        distance_to_high = htf_high - current_price
        if 0 < distance_to_high < (current_price * 0.003):
            log.info(f"🚫 [DOL Filter] SHORT отфильтрован: рядом 4H магнит на {htf_high:.5f}")
            return False

    return True


# ==========================================
# 3. ДЕТЕКЦИЯ SWEEP + SHIFT & СТРУКТУРНЫЙ РАСЧЕТ SL/TP
# ==========================================

def detect_macro_range_sweep(h4_bars, h1_bars, m5_bars=None, symbol="UNKNOWN", current_price=None):
    """
    Чистый Price Action: Детекция снятия ликвидности (Sweep) с безопасным SL за экстремум
    и таргетом на противоположную границу (DOL).
    """
    if not h4_bars or len(h4_bars) < 20 or not h1_bars or len(h1_bars) < 24:
        return None

    active_entry = current_price if current_price is not None and current_price > 0 else h1_bars[-1]["close"]
    
    # Исключаем текущую открытую свечу из определения границ диапазона
    past_h1_bars = h1_bars[:-1] if len(h1_bars) > 24 else h1_bars
    rd = calculate_range_dynamics(past_h1_bars, window=24)
    if not rd:
        return None

    macro_high = rd["range_high"]
    macro_low = rd["range_low"]
    eq_level = rd["eq_level"]

    bias = calculate_daily_bias(h4_bars)

    last_h1 = h1_bars[-1]
    setup_type = None
    direction = None
    level = 0.0

    # Проверка закола ликвидности границы ренджа
    if last_h1["high"] >= macro_high:
        setup_type = "PRICE_ACTION_SWEEP"
        direction = "SHORT"
        level = macro_high
    elif last_h1["low"] <= macro_low:
        setup_type = "PRICE_ACTION_SWEEP"
        direction = "LONG"
        level = macro_low

    if not setup_type:
        return None

    # Проверка старшего магнита
    if not check_htf_liquidity_magnet(direction, active_entry, h4_bars):
        return None

    atr = calculate_atr(h4_bars, period=14)
    
    # Расчет буфера с защитой от микро-стопов
    sym_upper = symbol.upper()
    if "BTC" in sym_upper:
        min_risk = 150.0
    elif "ETH" in sym_upper:
        min_risk = 12.0
    elif "XAU" in sym_upper or "GC=" in sym_upper:
        min_risk = 3.0
    elif "JPY" in sym_upper:
        min_risk = 0.25
    else:
        min_risk = active_entry * 0.0015

    buffer = max(atr * 0.4, min_risk * 0.3)

    if direction == "SHORT":
        stop_loss = level + buffer
        risk = stop_loss - active_entry
        if risk < min_risk:
            risk = min_risk
            stop_loss = active_entry + risk

        # Структурный тейк к противоположной границе
        structural_target = macro_low
        if structural_target >= active_entry or (active_entry - structural_target) < (risk * 1.5):
            target_1 = active_entry - (risk * 2.0)
        else:
            target_1 = structural_target
        target_2 = active_entry - (risk * 3.5)

    else:
        stop_loss = level - buffer
        risk = active_entry - stop_loss
        if risk < min_risk:
            risk = min_risk
            stop_loss = active_entry - risk

        structural_target = macro_high
        if structural_target <= active_entry or (structural_target - active_entry) < (risk * 1.5):
            target_1 = active_entry + (risk * 2.0)
        else:
            target_1 = structural_target
        target_2 = active_entry + (risk * 3.5)

    rr_ratio = abs(target_1 - active_entry) / risk

    log.info(f"🚨 [{symbol}] SWEEP ОБНАРУЖЕН! Направление: {direction} | Entry: {active_entry:.5f} | SL: {stop_loss:.5f} | TP1: {target_1:.5f}")

    return {
        "setup_type": "PRICE_ACTION_SWEEP",
        "direction": direction,
        "tf": "1H / 4H Pure Price Action",
        "level": level,
        "eq_level": eq_level,
        "daily_bias": bias,
        "entry": active_entry,
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "rr_ratio": round(rr_ratio, 2),
        "bar_time": last_h1["timestamp"],
        "confluence": "Macro Range Liquidity Sweep + Structural SL/TP"
    }


def detect_sweep(d1_bars, h4_bars, h1_bars, symbol="UNKNOWN", current_price=None):
    return detect_macro_range_sweep(h4_bars, h1_bars, None, symbol, current_price)
