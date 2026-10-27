import logging
from datetime import datetime, timezone

log = logging.getLogger("liquidity-hunter")


# ==========================================
# 1. ТЕХНИЧЕСКИЕ ИНДИКАТОРЫ И ВПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================

def calculate_atr(bars, period=14):
    """Расчет Average True Range (ATR) для динамических стопов."""
    if len(bars) < period + 1:
        return 0.0
    tr_list = []
    for i in range(1, len(bars)):
        high = bars[i]["high"]
        low = bars[i]["low"]
        prev_close = bars[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)
    return sum(tr_list[-period:]) / period


# ==========================================
# 2. DRAW ON LIQUIDITY (DOL) & RANGE DYNAMICS
# ==========================================

def calculate_dol_levels(d1_bars, w1_bars=None):
    """Расчет ключевых уровней внешней ликвидности (ERL): PDH / PDL."""
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
    """Динамика диапазона: Range High, Range Low и Equilibrium (EQ 50%)."""
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


# ==========================================
# 3. DAILY BIAS & QUARTERLY THEORY (QT)
# ==========================================

def get_quarterly_phase(dt=None):
    """Определение фазы Quarterly Theory (QT)."""
    if dt is None:
        dt = datetime.now(timezone.utc)

    hour = dt.hour
    if 0 <= hour < 6:
        return "Q1 (Accumulation)"
    elif 6 <= hour < 12:
        return "Q2 (Manipulation)"
    elif 12 <= hour < 18:
        return "Q3 (Distribution)"
    else:
        return "Q4 (Reversal/Continuation)"


def calculate_daily_bias(d1_bars, h4_bars):
    """Определение Daily Bias по структуре рынка."""
    if not h4_bars or len(h4_bars) < 20:
        return "NEUTRAL"
    if h4_bars[-1]["close"] < h4_bars[-10]["close"]:
        return "BEARISH"
    return "BULLISH"


def check_htf_liquidity_magnet(direction, current_price, h4_bars):
    """
    ФИЛЬТР СТАРШЕГО МАГНИТА (HTF DOL):
    Проверяет, не идет ли сделка прямо навстречу ближайшему крупному пулу ликвидности 4H.
    Если до магнита ликвидности очень близко и цена движется к нему, возвращает False (фильтруем сигнал).
    """
    if not h4_bars or len(h4_bars) < 50:
        return True  # Слишком длинная история для анализа, пропускаем фильтр

    h4_recent = h4_bars[-50:]
    htf_high = max(b["high"] for b in h4_recent)
    htf_low = min(b["low"] for b in h4_recent)

    if direction == "LONG":
        distance_to_low = current_price - htf_low
        if 0 < distance_to_low < (current_price * 0.003):
            log.info(f"🚫 Фильтр 4H Магнита заблокировал LONG: сильный пулл ликвидности снизу на {htf_low:.5f}")
            return False

    elif direction == "SHORT":
        distance_to_high = htf_high - current_price
        if 0 < distance_to_high < (current_price * 0.003):
            log.info(f"🚫 Фильтр 4H Магнита заблокировал SHORT: сильный пулл ликвидности сверху на {htf_high:.5f}")
            return False

    return True


# ==========================================
# 4. ЧИСТЫЙ PRICE ACTION: ДЕТЕКЦИЯ SWEEP + SHIFT
# ==========================================

def detect_macro_range_sweep(h4_bars, h1_bars, symbol="UNKNOWN", current_price=None, d1_bars=None):
    """
    Детектор свипов ликвидности: стоп за уровень закола, тейк к противоположному магниту (граница ренджа).
    """
    if not h4_bars or len(h4_bars) < 20:
        return None

    h4_curr = h4_bars[-1]
    active_entry = current_price if current_price is not None and current_price > 0 else h4_curr["close"]

    rd = calculate_range_dynamics(h1_bars if h1_bars and len(h1_bars) >= 24 else h4_bars, window=24)
    if not rd:
        return None

    macro_high = rd["range_high"]
    macro_low = rd["range_low"]
    eq_level = rd["eq_level"]

    bias = calculate_daily_bias(d1_bars, h4_bars) if d1_bars else "NEUTRAL"
    qt_phase = get_quarterly_phase()

    setup_type = None
    direction = None
    level = 0.0

    if h4_curr["high"] >= macro_high - (macro_high * 0.0002):
        setup_type = "PRICE_ACTION_SWEEP"
        direction = "SHORT"
        level = macro_high
    elif h4_curr["low"] <= macro_low + (macro_low * 0.0002):
        setup_type = "PRICE_ACTION_SWEEP"
        direction = "LONG"
        level = macro_low

    if not setup_type:
        return None

    # ПРИМЕНЯЕМ ФИЛЬТР СТАРШЕГО МАГНИТА 4H
    if not check_htf_liquidity_magnet(direction, active_entry, h4_bars):
        log.info(f"🛡 [{symbol}] Сигнал {direction} отфильтрован из-за близости 4H магнита ликвидности.")
        return None

    log.info(f"🚨 [{symbol}] SWEEP ОБНАРУЖЕН! Направление: {direction} по цене {active_entry:.5f}")

    atr = calculate_atr(h4_bars, period=14)
    buffer = atr * 0.4 if atr > 0 else level * 0.0015

    if direction == "SHORT":
        # Стоп-лосс строго за уровень закола + буфер
        stop_loss = level + buffer
        risk = stop_loss - active_entry
        if risk <= 0:
            risk = active_entry * 0.003
            stop_loss = active_entry + risk

        # Тейк-профит целим к противоположному магниту (нижняя граница диапазона macro_low)
        structural_target = macro_low if macro_low < active_entry else active_entry - (risk * 2.0)
        target_1 = structural_target
        target_2 = active_entry - (risk * 3.5)
    else:
        # Стоп-лосс строго под уровень закола - буфер
        stop_loss = level - buffer
        risk = active_entry - stop_loss
        if risk <= 0:
            risk = active_entry * 0.003
            stop_loss = active_entry - risk

        # Тейк-профит целим к верхней границе диапазона macro_high
        structural_target = macro_high if macro_high > active_entry else active_entry + (risk * 2.0)
        target_1 = structural_target
        target_2 = active_entry + (risk * 3.5)

    return {
        "setup_type": "PRICE_ACTION_SWEEP",
        "direction": direction,
        "tf": "1H / 4H Pure Price Action",
        "level": level,
        "eq_level": eq_level,
        "daily_bias": bias,
        "qt_phase": qt_phase,
        "entry": active_entry,
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "rr_ratio": round(abs(target_1 - active_entry) / risk, 1),
        "bar_time": h4_curr["timestamp"],
        "confluence": "Liquidity Sweep + Structure Stop + Structural Magnet TP",
        "fvg_zone": "Price Action Entry"
    }


def detect_sweep(d1_bars, h4_bars, h1_bars, symbol="UNKNOWN", current_price=None):
    return detect_macro_range_sweep(h4_bars, h1_bars, symbol, current_price, d1_bars)


def optimize_sl_tp_on_confirmation(direction, current_price, m5_bars, h1_bars=None, symbol="UNKNOWN"):
    """Быстрая оптимизация целей."""
    try:
        if len(m5_bars) < 5:
            return None, None, None, 2.0

        buffer_pct = 0.0010
        if direction == "LONG":
            local_low = min([b["low"] for b in m5_bars[-5:]])
            stop_loss = local_low * (1.0 - buffer_pct)
            risk = current_price - stop_loss
            target_1 = current_price + (risk * 1.5)
            target_2 = current_price + (risk * 3.0)
        else:
            local_high = max([b["high"] for b in m5_bars[-5:]])
            stop_loss = local_high * (1.0 + buffer_pct)
            risk = stop_loss - current_price
            target_1 = current_price - (risk * 1.5)
            target_2 = current_price - (risk * 3.0)

        return stop_loss, target_1, target_2, 2.0
    except Exception:
        return None, None, None, 2.0


# ==========================================
# 5. ФОРМАТИРОВАНИЕ СООБЩЕНИЙ В TELEGRAM
# ==========================================

def format_signal(symbol, event):
    bias = event.get("daily_bias", "NEUTRAL")
    qt = event.get("qt_phase", "Q3")

    return (
        f"🎯 **PRICE ACTION SIGNAL DETECTED** 🎯\n\n"
        f"🪙 **Инструмент:** `{symbol}`\n"
        f"⏱ **Таймфрейм:** `{event['tf']}`\n"
        f"📊 **Направление:** `{event['direction']}`\n"
        f"📅 **Bias:** `{bias}` | ⌛ **QT:** `{qt}`\n\n"
        f"📍 **Уровень закола:** `{event['level']:.5f}`\n"
        f"💵 **Цена входа:** `{event['entry']:.5f}`\n\n"
        f"🛑 **SL:** `{event['stop_loss']:.5f}`\n"
        f"🎯 **T1:** `{event['target_1']:.5f}` | 🚀 **T2:** `{event['target_2']:.5f}`\n"
    )


def format_confirmed_signal(symbol, event, current_price):
    emoji = "🟢" if event["direction"] == "LONG" else "🔴"
    return (
        f"✅ **CONFIRMED SETUP** ✅\n\n"
        f"{emoji} **Инструмент:** `{symbol}`\n"
        f"📊 **Направление:** `{event['direction']}`\n\n"
        f"📍 **Точка входа:** `{current_price:.5f}`\n"
        f"🛑 **Стоп-лосс:** `{event['stop_loss']:.5f}`\n"
        f"🎯 **Тейк-профит 1:** `{event['target_1']:.5f}`\n"
        f"🚀 **Тейк-профит 2:** `{event['target_2']:.5f}`\n"
    )
