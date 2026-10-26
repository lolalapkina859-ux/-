import logging
from datetime import datetime, timezone

log = logging.getLogger("liquidity-hunter")


# ==========================================
# 1. ТЕХНИЧЕСКИЕ ИНДИКАТОРЫ И ВПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================

def calculate_ema(bars, period=200):
    """Расчет экспоненциальной скользящей средней (EMA) для трендового фильтра."""
    if not bars or len(bars) < period:
        return None
    closes = [b["close"] for b in bars]
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for close in closes[period:]:
        ema = (close - ema) * multiplier + ema
    return ema


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
    """
    Расчет ключевых уровней внешней ликвидности (ERL):
    - PDH / PDL (Previous Day High / Low)
    - PWH / PWL (Previous Week High / Low)
    """
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
    """
    Динамика диапазона (Range Dynamics):
    Определение Range High, Range Low и Equilibrium (EQ 50%).
    """
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


def find_fvg(bars):
    """
    Поиск зон дисбаланса (Internal Range Liquidity - IRL):
    Bullish & Bearish Fair Value Gaps (FVG).
    """
    fvgs = []
    if len(bars) < 3:
        return fvgs

    for i in range(2, len(bars)):
        if bars[i]["low"] > bars[i - 2]["high"]:
            fvgs.append({
                "type": "Bullish FVG",
                "top": bars[i]["low"],
                "bottom": bars[i - 2]["high"],
                "mid": (bars[i]["low"] + bars[i - 2]["high"]) / 2.0,
                "timestamp": bars[i]["timestamp"]
            })
        elif bars[i]["high"] < bars[i - 2]["low"]:
            fvgs.append({
                "type": "Bearish FVG",
                "top": bars[i - 2]["low"],
                "bottom": bars[i]["high"],
                "mid": (bars[i - 2]["low"] + bars[i]["high"]) / 2.0,
                "timestamp": bars[i]["timestamp"]
            })
    return fvgs


def find_order_blocks(bars):
    """Поиск Order Block (последняя противоположная свеча перед импульсом)."""
    obs = []
    if len(bars) < 4:
        return obs

    for i in range(3, len(bars) - 1):
        body_prev = abs(bars[i - 1]["close"] - bars[i - 1]["open"])
        body_curr = abs(bars[i]["close"] - bars[i]["open"])

        if bars[i]["close"] > bars[i]["open"] and body_curr > body_prev * 1.5:
            for j in range(i - 1, max(0, i - 4), -1):
                if bars[j]["close"] < bars[j]["open"]:
                    obs.append({
                        "type": "Bullish OB",
                        "top": bars[j]["high"],
                        "bottom": bars[j]["low"],
                        "timestamp": bars[j]["timestamp"]
                    })
                    break

        elif bars[i]["close"] < bars[i]["open"] and body_curr > body_prev * 1.5:
            for j in range(i - 1, max(0, i - 4), -1):
                if bars[j]["close"] > bars[j]["open"]:
                    obs.append({
                        "type": "Bearish OB",
                        "top": bars[j]["high"],
                        "bottom": bars[j]["low"],
                        "timestamp": bars[j]["timestamp"]
                    })
                    break
    return obs


# ==========================================
# 3. МЕТОДОЛОГИЯ ALCHEMIST & MSNR
# ==========================================

def find_ocl_levels(bars, depth=20):
    """Поиск OCL (Open-Close Level) малайзийских уровней."""
    ocl_levels = []
    if not bars or len(bars) < depth:
        return ocl_levels

    recent_bars = bars[-depth:]
    for i in range(1, len(recent_bars)):
        prev_close = recent_bars[i - 1]["close"]
        curr_open = recent_bars[i]["open"]

        if abs(prev_close - curr_open) / curr_open < 0.0005:
            ocl_price = (prev_close + curr_open) / 2.0
            ocl_type = "Support OCL" if recent_bars[i]["close"] > curr_open else "Resistance OCL"
            ocl_levels.append({
                "type": ocl_type,
                "price": ocl_price,
                "timestamp": recent_bars[i]["timestamp"]
            })
    return ocl_levels


def find_inducement(bars):
    """Поиск Inducement (IDM)."""
    idms = []
    if len(bars) < 4:
        return idms

    for i in range(2, len(bars) - 1):
        if bars[i]["low"] < bars[i - 1]["low"]:
            idms.append({
                "type": "Bearish IDM",
                "price": bars[i]["high"],
                "timestamp": bars[i]["timestamp"]
            })
        elif bars[i]["high"] > bars[i - 1]["high"]:
            idms.append({
                "type": "Bullish IDM",
                "price": bars[i]["low"],
                "timestamp": bars[i]["timestamp"]
            })
    return idms


def detect_quasimodo_patterns(bars):
    """Поиск паттернов Quasimodo (QMR)."""
    qm_patterns = []
    if len(bars) < 5:
        return qm_patterns

    for i in range(4, len(bars)):
        b0, b1, b2, b3, b4 = bars[i - 4], bars[i - 3], bars[i - 2], bars[i - 1], bars[i]
        if b1["low"] < b0["low"] and b2["high"] > b0["high"] and b3["low"] < b1["low"] and b4["high"] > b2["high"]:
            qm_patterns.append({
                "type": "Bearish QMR",
                "qml_level": b1["low"],
                "entry_zone": b2["high"],
                "timestamp": b4["timestamp"]
            })
    return qm_patterns


# ==========================================
# 4. DAILY BIAS & QUARTERLY THEORY (QT)
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
    """Определение Daily Bias по структуре рынка (Lower Lows / Lower Highs)."""
    if not h4_bars or len(h4_bars) < 20:
        return "NEUTRAL"

    recent_bars = h4_bars[-20:]
    half = len(recent_bars) // 2
    
    first_half_high = max(b["high"] for b in recent_bars[:half])
    second_half_high = max(b["high"] for b in recent_bars[half:])
    
    first_half_low = min(b["low"] for b in recent_bars[:half])
    second_half_low = min(b["low"] for b in recent_bars[half:])

    if second_half_high <= first_half_high and second_half_low < first_half_low:
        return "BEARISH"
    elif second_half_high > first_half_high and second_half_low >= first_half_low:
        return "BULLISH"
        
    if h4_bars[-1]["close"] < h4_bars[-10]["close"]:
        return "BEARISH"
        
    return "BULLISH"


# ==========================================
# 5. ДЕТЕКЦИЯ СЕТАПОВ С УЧЕТОМ FVG И SWEEP
# ==========================================

def find_nearest_local_target(direction, entry_price, bars_list):
    """Находит ближайший локальный экстремум для T1."""
    if not bars_list:
        return None
    if direction == "LONG":
        highs = [b["high"] for b in bars_list if b["high"] > entry_price]
        return min(highs) if highs else None
    else:
        lows = [b["low"] for b in bars_list if b["low"] < entry_price]
        return max(lows) if lows else None


def detect_macro_range_sweep(h4_bars, h1_bars, symbol="UNKNOWN", current_price=None, d1_bars=None):
    """Поиск макро-свипов с интеграцией FVG и Order Block зон для входа."""
    if not h4_bars or len(h4_bars) < 50:
        return None

    h4_curr = h4_bars[-1]
    active_entry = current_price if current_price is not None and current_price > 0 else h4_curr["close"]

    rd = calculate_range_dynamics(h4_bars, window=50)
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

    # Свип сверху (Short из зоны Premium)
    if h4_curr["high"] > macro_high and h4_curr["close"] <= macro_high:
        if active_entry < eq_level:
            return None
        setup_type = "MACRO_RANGE_SWEEP"
        direction = "SHORT"
        level = macro_high

    # Свип снизу (Long из зоны Discount)
    elif h4_curr["low"] < macro_low and h4_curr["close"] >= macro_low:
        if active_entry > eq_level:
            return None
        setup_type = "MACRO_RANGE_SWEEP"
        direction = "LONG"
        level = macro_low

    if not setup_type:
        return None

    # Ищем актуальные FVG и Order Blocks на H1/H4 для точного входа (как на вашем скриншоте 15M/1H)
    fvgs = find_fvg(h1_bars) if h1_bars else []
    obs = find_order_blocks(h4_bars) if h4_bars else []
    ocl_levels = find_ocl_levels(h4_bars) if h4_bars else []

    fvg_info = "Не обнаружено"
    relevant_fvg = None

    if direction == "SHORT":
        bearish_fvgs = [f for f in fvgs if f["type"] == "Bearish FVG"]
        if bearish_fvgs:
            relevant_fvg = bearish_fvgs[-1]
            fvg_info = f"Bearish FVG [{relevant_fvg['bottom']:.2f} — {relevant_fvg['top']:.2f}] (Mid: {relevant_fvg['mid']:.2f})"
    else:
        bullish_fvgs = [f for f in fvgs if f["type"] == "Bullish FVG"]
        if bullish_fvgs:
            relevant_fvg = bullish_fvgs[-1]
            fvg_info = f"Bullish FVG [{relevant_fvg['bottom']:.2f} — {relevant_fvg['top']:.2f}] (Mid: {relevant_fvg['mid']:.2f})"

    confluence_note = f"DOL Sweep | Bias: {bias} | FVG Zone: {fvg_info}"
    if obs:
        confluence_note += f" | OB: {obs[-1]['type']}"

    log.info(f"🏛 [{symbol}] DOL SWEEP + FVG! Направление: {direction} | FVG: {fvg_info}")

    atr = calculate_atr(h4_bars, period=14)
    buffer = atr * 0.5 if atr > 0 else level * 0.0035

    if direction == "SHORT":
        stop_loss = level + buffer
        risk = stop_loss - active_entry
        min_safe_t1 = active_entry - (risk * 1.5)
        local_t1 = find_nearest_local_target("SHORT", active_entry, h1_bars)
        
        target_1 = local_t1 if (local_t1 and local_t1 <= (active_entry - risk)) else min_safe_t1
        target_2 = macro_low
    else:
        stop_loss = level - buffer
        risk = active_entry - stop_loss
        min_safe_t1 = active_entry + (risk * 1.5)
        local_t1 = find_nearest_local_target("LONG", active_entry, h1_bars)
        
        target_1 = local_t1 if (local_t1 and local_t1 >= (active_entry + risk)) else min_safe_t1
        target_2 = macro_high

    return {
        "setup_type": "MACRO_RANGE_SWEEP",
        "direction": direction,
        "tf": "4H + 1H (ICT FVG Model)",
        "level": level,
        "eq_level": eq_level,
        "daily_bias": bias,
        "qt_phase": qt_phase,
        "entry": active_entry,
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "rr_ratio": 3.0,
        "bar_time": h4_curr["timestamp"],
        "confluence": confluence_note,
        "fvg_zone": fvg_info
    }


def detect_sweep(d1_bars, h4_bars, h1_bars, symbol="UNKNOWN", current_price=None):
    return detect_macro_range_sweep(h4_bars, h1_bars, symbol, current_price, d1_bars)


def optimize_sl_tp_on_confirmation(direction, current_price, m5_bars, h1_bars=None, symbol="UNKNOWN"):
    """Оптимизация стопа и тейка при подтверждении CHoCH на M5."""
    try:
        if len(m5_bars) < 5:
            return None, None, None, 3.5

        recent_m5 = m5_bars[-5:]
        if direction == "LONG":
            local_m5_high = max([b["high"] for b in recent_m5[:-1]])
            if recent_m5[-1]["close"] <= local_m5_high:
                return None, None, None, 3.5
        else:
            local_m5_low = min([b["low"] for b in recent_m5[:-1]])
            if recent_m5[-1]["close"] >= local_m5_low:
                return None, None, None, 3.5

        last_candle = m5_bars[-1]
        candle_range = last_candle["high"] - last_candle["low"]
        body_size = abs(last_candle["close"] - last_candle["open"])
        
        if candle_range > 0 and (body_size / candle_range) < 0.45:
            return None, None, None, 3.5

        is_forex = any(pair in symbol for pair in ["EUR", "GBP", "USD", "AUD", "NZD", "CAD", "JPY"]) and "USDT" not in symbol
        buffer_pct = 0.0018 if is_forex else 0.0010

        if direction == "LONG":
            local_low = min([b["low"] for b in m5_bars[-8:]])
            stop_loss = local_low * (1.0 - buffer_pct)
            risk = current_price - stop_loss
            min_safe_t1 = current_price + (risk * 1.5)
            
            local_t1 = find_nearest_local_target("LONG", current_price, h1_bars) if h1_bars else None
            target_1 = local_t1 if (local_t1 and local_t1 >= (current_price + risk)) else min_safe_t1
            target_2 = current_price + (risk * 3.5)
        else:
            local_high = max([b["high"] for b in m5_bars[-8:]])
            stop_loss = local_high * (1.0 + buffer_pct)
            risk = stop_loss - current_price
            min_safe_t1 = current_price - (risk * 1.5)
            
            local_t1 = find_nearest_local_target("SHORT", current_price, h1_bars) if h1_bars else None
            target_1 = local_t1 if (local_t1 and local_t1 <= (current_price - risk)) else min_safe_t1
            target_2 = current_price - (risk * 3.5)

        return stop_loss, target_1, target_2, 3.5
    except Exception as e:
        log.warning(f"Ошибка оптимизации стопа с CHoCH: {e}")
        return None, None, None, 3.5


# ==========================================
# 6. ФОРМАТИРОВАНИЕ СООБЩЕНИЙ В TELEGRAM
# ==========================================

def format_signal(symbol, event):
    conf = event.get("confluence", "Стандартный макро-свип DOL")
    bias = event.get("daily_bias", "NEUTRAL")
    qt = event.get("qt_phase", "Q3 (Distribution)")
    fvg_z = event.get("fvg_zone", "Не указана")

    return (
        f"🏛 **ICT & ALCHEMIST FVG MODEL** 🏛\n\n"
        f"🪙 **Инструмент:** `{symbol}`\n"
        f"⏱ **Таймфрейм:** `{event['tf']}`\n"
        f"📊 **Направление:** `{event['direction']}`\n"
        f"📅 **Daily Bias:** `{bias}` | ⌛ **QT:** `{qt}`\n\n"
        f"📍 **Уровень закола (Sweep):** `{event['level']:.5f}`\n"
        f"📦 **Зона FVG (Лимитный вход):** `{fvg_z}`\n"
        f"⚖️ **EQ Диапазона:** `{event['eq_level']:.5f}`\n"
        f"🔍 **Конфлюэнция:** _{conf}_\n\n"
        f"📝 **План:** Ожидание возврата в FVG-зону и слома структуры (CHoCH) на M5.\n\n"
        f"🛑 **SL:** `{event['stop_loss']:.5f}`\n"
        f"🎯 **T1:** `{event['target_1']:.5f}` | 🚀 **T2:** `{event['target_2']:.5f}`\n"
    )


def format_confirmed_signal(symbol, event, current_price):
    emoji = "🟢" if event["direction"] == "LONG" else "🔴"
    return (
        f"✅ **ICT FVG ENTRY & CHoCH CONFIRMED** ✅\n\n"
        f"{emoji} **Инструмент:** `{symbol}`\n"
        f"📊 **Направление:** `{event['direction']}`\n"
        f"⚡️ **Цена отработала FVG и подтвердила слом структуры на M5!**\n\n"
        f"📍 **Точка входа:** `{current_price:.5f}`\n"
        f"🛑 **Стоп-лосс:** `{event['stop_loss']:.5f}`\n"
        f"🎯 **Тейк-профит 1:** `{event['target_1']:.5f}`\n"
        f"🚀 **Тейк-профит 2:** `{event['target_2']:.5f}`\n\n"
        f"🛡 _Исполнено по концепции Smart Money (Sweep + FVG + CHoCH)._"
    )
