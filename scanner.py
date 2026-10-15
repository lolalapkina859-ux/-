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


def detect_sweep(d1_bars, h4_bars, h1_bars, symbol="UNKNOWN"):
    """
    Двухрежимный анализ CRT / SMC:
    1. Liquidity Sweep -> Контртрендовый сетап (разворот от зачистки ликвидности).
    2. True BOS -> Трендовый сетап (вход на продолжение пробоя после отката).
    """
    if not h4_bars or not h1_bars:
        log.warning(f"[{symbol}] Отсутствуют бары для 4H или 1H. Пропуск анализа.")
        return None

    h4_curr = h4_bars[-1]
    h1_curr = h1_bars[-1]

    # Границы CRT диапазонов (4H и 1H)
    h4_high = max([b["high"] for b in h4_bars[-16:-1]])
    h4_low = min([b["low"] for b in h4_bars[-16:-1]])

    h1_high = max([b["high"] for b in h1_bars[-20:-1]])
    h1_low = min([b["low"] for b in h1_bars[-20:-1]])

    direction = None
    setup_type = None  # "SWEEP" или "BOS"
    level = 0.0
    tf = None
    current_close = 0.0
    bar_time = None
    htf_bars_ref = None

    # --- ПРОВЕРКА 4H (Приоритет) ---
    if h4_curr["high"] > h4_high:
        level = h4_high
        tf = "4H (CRT)"
        current_close = h4_curr["close"]
        bar_time = h4_curr["timestamp"]
        htf_bars_ref = h4_bars

        if h4_curr["close"] > h4_high:
            # Истинный пробой верхнего уровня -> Трендовый LONG
            setup_type = "BOS"
            direction = "LONG"
        else:
            # Сбор ликвидности сверху -> Контртрендовый SHORT
            upper_wick = h4_curr["high"] - max(h4_curr["open"], h4_curr["close"])
            body_size = abs(h4_curr["close"] - h4_curr["open"])
            if upper_wick > 0 and (upper_wick >= body_size * 0.3):
                setup_type = "SWEEP"
                direction = "SHORT"

    elif h4_curr["low"] < h4_low:
        level = h4_low
        tf = "4H (CRT)"
        current_close = h4_curr["close"]
        bar_time = h4_curr["timestamp"]
        htf_bars_ref = h4_bars

        if h4_curr["close"] < h4_low:
            # Истинный пробой нижнего уровня -> Трендовый SHORT
            setup_type = "BOS"
            direction = "SHORT"
        else:
            # Сбор ликвидности снизу -> Контртрендовый LONG
            lower_wick = min(h4_curr["open"], h4_curr["close"]) - h4_curr["low"]
            body_size = abs(h4_curr["close"] - h4_curr["open"])
            if lower_wick > 0 and (lower_wick >= body_size * 0.3):
                setup_type = "SWEEP"
                direction = "LONG"

    # --- ПРОВЕРКА 1H ---
    if not setup_type:
        if h1_curr["high"] > h1_high:
            level = h1_high
            tf = "1H (CRT)"
            current_close = h1_curr["close"]
            bar_time = h1_curr["timestamp"]
            htf_bars_ref = h1_bars

            if h1_curr["close"] > h1_high:
                setup_type = "BOS"
                direction = "LONG"
            else:
                upper_wick = h1_curr["high"] - max(h1_curr["open"], h1_curr["close"])
                body_size = abs(h1_curr["close"] - h1_curr["open"])
                if upper_wick > 0 and (upper_wick >= body_size * 0.3):
                    setup_type = "SWEEP"
                    direction = "SHORT"

        elif h1_curr["low"] < h1_low:
            level = h1_low
            tf = "1H (CRT)"
            current_close = h1_curr["close"]
            bar_time = h1_curr["timestamp"]
            htf_bars_ref = h1_bars

            if h1_curr["close"] < h1_low:
                setup_type = "BOS"
                direction = "SHORT"
            else:
                lower_wick = min(h1_curr["open"], h1_curr["close"]) - h1_curr["low"]
                body_size = abs(h1_curr["close"] - h1_curr["open"])
                if lower_wick > 0 and (lower_wick >= body_size * 0.3):
                    setup_type = "SWEEP"
                    direction = "LONG"

    if not setup_type:
        return None

    # Фильтр сильного контртрендового движения (для SWEEP сетапов)
    if setup_type == "SWEEP" and is_strong_impulse_against(direction, htf_bars_ref):
        log.info(f"⛔️ [{symbol}] SWEEP проигнорирован: сильный контртрендовый импульс на {tf}.")
        return None

    log.info(
        f"🎯 [{symbol}] ОБНАРУЖЕН {setup_type}! "
        f"Таймфрейм: {tf} | Направление: {direction} | Уровень: {level:.5f} | Цена: {current_close:.5f}"
    )

    # Расчет Stop-Loss & Targets
    if direction == "SHORT":
        stop_loss = level + (level * 0.0035) if setup_type == "SWEEP" else current_close + (current_close * 0.004)
        risk = stop_loss - current_close
        target_1 = current_close - (risk * 1.5)
        target_2 = current_close - (risk * 3.5)
    else:  # LONG
        stop_loss = level - (level * 0.0035) if setup_type == "SWEEP" else current_close - (current_close * 0.004)
        risk = current_close - stop_loss
        target_1 = current_close + (risk * 1.5)
        target_2 = current_close + (risk * 3.5)

    return {
        "setup_type": setup_type,  # SWEEP или BOS
        "direction": direction,
        "tf": tf,
        "level": level,
        "entry": current_close,    # Актуальная цена закрытия свечи
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "rr_ratio": 3.5,
        "bar_time": bar_time,
    }


def optimize_sl_tp_on_confirmation(direction, current_price, m5_bars, h1_bars, symbol="UNKNOWN"):
    """
    Оптимизация стопа с проверкой на Expansion Candle (свечу расширения M5).
    """
    try:
        last_candle = m5_bars[-1]
        candle_range = last_candle["high"] - last_candle["low"]
        body_size = abs(last_candle["close"] - last_candle["open"])
        
        if candle_range > 0 and (body_size / candle_range) < 0.50:
            log.info(f"[{symbol}] Отклонено: свеча M5 не является Expansion (слабое тело).")
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
            
            min_risk = current_price * 0.012 if is_forex else current_price * 0.0025
            if risk < min_risk:
                local_high_h1 = max([b["high"] for b in h1_bars[-3:]])
                stop_loss = local_high_h1 * (1.0 + buffer_pct)
                risk = stop_loss - current_price

            target_1 = current_price - (risk * 2.0)
            target_2 = current_price - (risk * 4.0)

        return stop_loss, target_1, target_2, 3.5
    except Exception as e:
        log.warning(f"Ошибка оптимизации стопа CRT: {e}")
        return None, None, None, 3.5


def format_signal(symbol, event):
    emoji = "🚀" if event["setup_type"] == "BOS" else ("🔴" if event["direction"] == "SHORT" else "🟢")
    title = f"⚡️ TRUE BOS BREAKOUT ⚡️" if event["setup_type"] == "BOS" else "🔴 CRT LIQUIDITY SWEEP 🔴"
    plan_note = "Ожидаем ретеста/закрепления на M5 по тренду." if event["setup_type"] == "BOS" else f"Ждем подтверждения Expansion-свечой на M5 уровня {event['entry']:.5f}."

    return (
        f"{title}\n\n"
        f"🪙 **Инструмент:** `{symbol}`\n"
        f"⏱ **Таймфрейм:** `{event['tf']}`\n"
        f"📊 **Тип:** `{event['setup_type']}` | **Направление:** `{event['direction']}`\n\n"
        f"📍 **Пробитый уровень:** `{event['level']:.5f}`\n"
        f"⚡️ **Текущая цена:** `{event['entry']:.5f}`\n\n"
        f"📝 **План торговли:**\n"
        f"• {plan_note}\n\n"
        f"🛑 **SL:** `{event['stop_loss']:.5f}`\n"
        f"🎯 **T1:** `{event['target_1']:.5f}` | 🚀 **T2:** `{event['target_2']:.5f}`\n"
    )


def format_confirmed_signal(symbol, event, current_price):
    emoji = "🟢" if event["direction"] == "LONG" else "🔴"
    setup_name = "TRUE BOS TREND ENTRY" if event.get("setup_type") == "BOS" else "CRT SWEEP EXPANSION"
    return (
        f"✅ **{setup_name} CONFIRMED** ✅\n\n"
        f"{emoji} **Инструмент:** `{symbol}`\n"
        f"📊 **Направление:** `{event['direction']}` (База: `{event['tf']}`)\n"
        f"⚡️ **Подтверждение на M5 зафиксировано!**\n\n"
        f"📍 **Вход:** `{current_price:.5f}`\n"
        f"🛑 **Стоп-лосс:** `{event['stop_loss']:.5f}`\n"
        f"🎯 **T1:** `{event['target_1']:.5f}` | **T2:** `{event['target_2']:.5f}`\n\n"
        f"🚀 _Вход по правилам CRT & SMC готов!_"
    )
