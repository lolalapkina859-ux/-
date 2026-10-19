import logging

log = logging.getLogger("liquidity-hunter")


def is_strong_impulse_against(direction, htf_bars):
    """
    Проверка на агрессивный трендовый импульс против предполагаемой сделки.
    """
    if len(htf_bars) < 3:
        return False

    recent_bars = htf_bars[-3:-1]
    avg_body = sum([abs(b["close"] - b["open"]) for b in recent_bars]) / len(recent_bars)
    
    if direction == "LONG":
        bearish_count = sum(1 for b in recent_bars if b["close"] < b["open"])
        if bearish_count >= 2 and avg_body > (recent_bars[-1]["high"] - recent_bars[-1]["low"]) * 0.65:
            return True
            
    elif direction == "SHORT":
        bullish_count = sum(1 for b in recent_bars if b["close"] > b["open"])
        if bullish_count >= 2 and avg_body > (recent_bars[-1]["high"] - recent_bars[-1]["low"]) * 0.65:
            return True

    return False


def detect_macro_range_sweep(h4_bars, h1_bars, symbol="UNKNOWN", current_price=None):
    """
    Улучшенный поиск макро-свипов границ торгового диапазона (Macro Range High/Low Sweep)
    на старших таймфреймах (4H) с железобетонной проверкой истинных экстремумов.
    """
    if not h4_bars or len(h4_bars) < 80:
        return None

    # Берем широкий срез (последние 120 баров на 4H) для поиска ИСТИННЫХ границ диапазона
    macro_slice = h4_bars[-120:-1]
    macro_high = max([b["high"] for b in macro_slice])
    macro_low = min([b["low"] for b in macro_slice])
    eq_level = (macro_high + macro_low) / 2.0

    h4_curr = h4_bars[-1]
    active_entry = current_price if current_price is not None and current_price > 0 else h4_curr["close"]

    setup_type = None
    direction = None
    level = 0.0

    # 1. Свип ИСТИННОЙ макро-вершины (Range High Sweep -> Шорт)
    # Строгое условие: хай свечи ДОЛЖЕН превышать абсолютный макро-хай
    if h4_curr["high"] > macro_high and h4_curr["close"] <= macro_high:
        upper_wick = h4_curr["high"] - max(h4_curr["open"], h4_curr["close"])
        body_size = abs(h4_curr["close"] - h4_curr["open"])
        if upper_wick > 0 and (upper_wick >= body_size * 0.25):
            setup_type = "MACRO_RANGE_SWEEP"
            direction = "SHORT"
            level = macro_high

    # 2. Свип ИСТИННОГО макро-низа (Range Low Sweep -> Лонг)
    # Строгое условие: лоу свечи ДОЛЖЕН быть ниже абсолютного макро-лоу
    elif h4_curr["low"] < macro_low and h4_curr["close"] >= macro_low:
        lower_wick = min(h4_curr["open"], h4_curr["close"]) - h4_curr["low"]
        body_size = abs(h4_curr["close"] - h4_curr["open"])
        if lower_wick > 0 and (lower_wick >= body_size * 0.25):
            setup_type = "MACRO_RANGE_SWEEP"
            direction = "LONG"
            level = macro_low

    if not setup_type:
        return None

    log.info(
        f"🏛 [{symbol}] НАСТОЯЩИЙ MACRO RANGE SWEEP! "
        f"Направление: {direction} | Истинная граница: {level:.5f} | EQ: {eq_level:.5f}"
    )

    if direction == "SHORT":
        stop_loss = level + (level * 0.005)
        target_1 = eq_level
        target_2 = macro_low
    else:
        stop_loss = level - (level * 0.005)
        target_1 = eq_level
        target_2 = macro_high

    return {
        "setup_type": "MACRO_RANGE_SWEEP",
        "direction": direction,
        "tf": "4H (Macro Range)",
        "level": level,
        "eq_level": eq_level,
        "entry": active_entry,
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "rr_ratio": 3.0,
        "bar_time": h4_curr["timestamp"],
    }


def detect_sweep(d1_bars, h4_bars, h1_bars, symbol="UNKNOWN", current_price=None):
    """
    Сначала проверяем глобальный Macro Range Sweep, если нет — ищем локальный CRT Sweep.
    """
    # Сначала пробуем найти макро-свип диапазона на 4H
    macro_event = detect_macro_range_sweep(h4_bars, h1_bars, symbol, current_price)
    if macro_event:
        return macro_event

    # Локальный поиск по аналогии (1H / 4H CRT Sweep)
    if not h4_bars or not h1_bars:
        return None

    h4_curr = h4_bars[-1]
    h1_curr = h1_bars[-1]

    h4_high = max([b["high"] for b in h4_bars[-16:-1]])
    h4_low = min([b["low"] for b in h4_bars[-16:-1]])
    h1_high = max([b["high"] for b in h1_bars[-20:-1]])
    h1_low = min([b["low"] for b in h1_bars[-20:-1]])

    direction = None
    level = 0.0
    tf = None
    bar_time = None
    htf_bars_ref = None

    if h4_curr["high"] > h4_high and h4_curr["close"] <= h4_high:
        upper_wick = h4_curr["high"] - max(h4_curr["open"], h4_curr["close"])
        body_size = abs(h4_curr["close"] - h4_curr["open"])
        if upper_wick > 0 and (upper_wick >= body_size * 0.3):
            level = h4_high
            tf = "4H (CRT)"
            bar_time = h4_curr["timestamp"]
            htf_bars_ref = h4_bars
            direction = "SHORT"

    elif h4_curr["low"] < h4_low and h4_curr["close"] >= h4_low:
        lower_wick = min(h4_curr["open"], h4_curr["close"]) - h4_curr["low"]
        body_size = abs(h4_curr["close"] - h4_curr["open"])
        if lower_wick > 0 and (lower_wick >= body_size * 0.3):
            level = h4_low
            tf = "4H (CRT)"
            bar_time = h4_curr["timestamp"]
            htf_bars_ref = h4_bars
            direction = "LONG"

    if not direction:
        if h1_curr["high"] > h1_high and h1_curr["close"] <= h1_high:
            upper_wick = h1_curr["high"] - max(h1_curr["open"], h1_curr["close"])
            body_size = abs(h1_curr["close"] - h1_curr["open"])
            if upper_wick > 0 and (upper_wick >= body_size * 0.3):
                level = h1_high
                tf = "1H (CRT)"
                bar_time = h1_curr["timestamp"]
                htf_bars_ref = h1_bars
                direction = "SHORT"

        elif h1_curr["low"] < h1_low and h1_curr["close"] >= h1_low:
            lower_wick = min(h1_curr["open"], h1_curr["close"]) - h1_curr["low"]
            body_size = abs(h1_curr["close"] - h1_curr["open"])
            if lower_wick > 0 and (lower_wick >= body_size * 0.3):
                level = h1_low
                tf = "1H (CRT)"
                bar_time = h1_curr["timestamp"]
                htf_bars_ref = h1_bars
                direction = "LONG"

    if not direction:
        return None

    if is_strong_impulse_against(direction, htf_bars_ref):
        return None

    active_entry = current_price if current_price is not None and current_price > 0 else h1_curr["close"]

    if direction == "SHORT":
        stop_loss = level + (level * 0.0035)
        risk = stop_loss - active_entry
        target_1 = active_entry - (risk * 1.5)
        target_2 = active_entry - (risk * 3.5)
    else:
        stop_loss = level - (level * 0.0035)
        risk = active_entry - stop_loss
        target_1 = active_entry + (risk * 1.5)
        target_2 = active_entry + (risk * 3.5)

    return {
        "setup_type": "SWEEP",
        "direction": direction,
        "tf": tf,
        "level": level,
        "entry": active_entry,
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "rr_ratio": 3.5,
        "bar_time": bar_time,
    }


def optimize_sl_tp_on_confirmation(direction, current_price, m5_bars, h1_bars, symbol="UNKNOWN"):
    try:
        last_candle = m5_bars[-1]
        candle_range = last_candle["high"] - last_candle["low"]
        body_size = abs(last_candle["close"] - last_candle["open"])
        
        if candle_range > 0 and (body_size / candle_range) < 0.50:
            return None, None, None, 3.5

        is_forex = any(pair in symbol for pair in ["EUR", "GBP", "USD", "AUD", "NZD", "CAD", "JPY"]) and "USDT" not in symbol
        buffer_pct = 0.0015 if is_forex else 0.0008

        if direction == "LONG":
            local_low = min([b["low"] for b in m5_bars[-8:]])
            stop_loss = local_low * (1.0 - buffer_pct)
            risk = current_price - stop_loss
            
            min_risk = current_price * 0.0012 if is_forex else current_price * 0.0025
            if risk < min_risk:
                local_low_h1 = min([b["low"] for b in h1_bars[-3:]])
                stop_loss = local_low_h1 * (1.0 - buffer_pct)
                risk = current_price - stop_loss

            target_1 = current_price + (risk * 2.0)
            target_2 = current_price + (risk * 4.0)
        else:
            local_high = max([b["high"] for b in m5_bars[-8:]])
            stop_loss = local_high * (1.0 + buffer_pct)
            risk = stop_loss - current_price
            
            min_risk = current_price * 0.0012 if is_forex else current_price * 0.0025
            if risk < min_risk:
                local_high_h1 = max([b["high"] for b in h1_bars[-3:]])
                stop_loss = local_high_h1 * (1.0 + buffer_pct)
                risk = stop_loss - current_price

            target_1 = current_price - (risk * 2.0)
            target_2 = current_price - (risk * 4.0)

        return stop_loss, target_1, target_2, 3.5
    except Exception as e:
        log.warning(f"Ошибка оптимизации стопа: {e}")
        return None, None, None, 3.5


def format_signal(symbol, event):
    if event.get("setup_type") == "MACRO_RANGE_SWEEP":
        title = "🏛 MACRO RANGE ZONE SWEEP 🏛"
        plan_note = f"Макро-свип границы диапазона `{event['level']:.5f}`. Ждем подтверждения на M5."
    else:
        title = "🔴 CRT LIQUIDITY SWEEP 🔴" if event["direction"] == "SHORT" else "🟢 CRT LIQUIDITY SWEEP 🟢"
        plan_note = f"Ждем подтверждения Expansion-свечой на M5 уровня `{event['entry']:.5f}`."

    return (
        f"{title}\n\n"
        f"🪙 **Инструмент:** `{symbol}`\n"
        f"⏱ **Таймфрейм:** `{event['tf']}`\n"
        f"📊 **Тип:** `{event.get('setup_type', 'SWEEP')}` | **Направление:** `{event['direction']}`\n\n"
        f"📍 **Ключевой уровень:** `{event['level']:.5f}`\n"
        f"⚡️ **Текущая цена:** `{event['entry']:.5f}`\n\n"
        f"📝 **План торговли:**\n"
        f"• {plan_note}\n\n"
        f"🛑 **SL:** `{event['stop_loss']:.5f}`\n"
        f"🎯 **T1 (EQ):** `{event['target_1']:.5f}` | 🚀 **T2:** `{event['target_2']:.5f}`\n"
    )


def format_confirmed_signal(symbol, event, current_price):
    emoji = "🟢" if event["direction"] == "LONG" else "🔴"
    setup_name = "MACRO RANGE SWEEP CONFIRMED" if event.get("setup_type") == "MACRO_RANGE_SWEEP" else "CRT SWEEP EXPANSION"
    return (
        f"✅ **{setup_name}** ✅\n\n"
        f"{emoji} **Инструмент:** `{symbol}`\n"
        f"📊 **Направление:** `{event['direction']}` (База: `{event['tf']}`)\n"
        f"⚡️ **Подтверждение на M5 зафиксировано!**\n\n"
        f"📍 **Вход:** `{current_price:.5f}`\n"
        f"🛑 **Стоп-лосс:** `{event['stop_loss']:.5f}`\n"
        f"🎯 **T1:** `{event['target_1']:.5f}` | **T2:** `{event['target_2']:.5f}`\n\n"
        f"🚀 _Вход по правилам Macro Range & SMC готов!_"
    )
