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


def detect_sweep(d1_bars, h4_bars, h1_bars, symbol="UNKNOWN", current_price=None):
    """
    Однорежимный анализ CRT / SMC:
    Исключительно Liquidity Sweep -> Разворотный сетап от снятия ликвидности (закол тенью).
    (Сигналы типа TRUE BOS отключены для исключения распила во флэте).
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
    level = 0.0
    tf = None
    bar_time = None
    htf_bars_ref = None

    # --- ПРОВЕРКА 4H SWEEP (Приоритет) ---
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

    # --- ПРОВЕРКА 1H SWEEP (Если нет 4H) ---
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

    # Если свипа не было, выходим
    if not direction:
        return None

    # Фильтр сильного контртрендового движения
    if is_strong_impulse_against(direction, htf_bars_ref):
        log.info(f"⛔️ [{symbol}] SWEEP проигнорирован: сильный контртрендовый импульс на {tf}.")
        return None

    # Вход по текущей рыночной цене
    active_entry = current_price if current_price is not None and current_price > 0 else h1_curr["close"]

    log.info(
        f"🎯 [{symbol}] ОБНАРУЖЕН SWEEP! "
        f"Таймфрейм: {tf} | Направление: {direction} | Уровень: {level:.5f} | Цена: {active_entry:.5f}"
    )

    # Расчет начального Stop-Loss & Targets для SWEEP
    if direction == "SHORT":
        stop_loss = level + (level * 0.0035)
        risk = stop_loss - active_entry
        target_1 = active_entry - (risk * 1.5)
        target_2 = active_entry - (risk * 3.5)
    else:  # LONG
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
            
            min_risk = current_price * 0.0012 if is_forex else current_price * 0.0025
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
    emoji = "🔴" if event["direction"] == "SHORT" else "🟢"
    title = "🔴 CRT LIQUIDITY SWEEP 🔴" if event["direction"] == "SHORT" else "🟢 CRT LIQUIDITY SWEEP 🟢"

    return (
        f"{title}\n\n"
        f"🪙 **Инструмент:** `{symbol}`\n"
        f"⏱ **Таймфрейм:** `{event['tf']}`\n"
        f"📊 **Тип:** `SWEEP` | **Направление:** `{event['direction']}`\n\n"
        f"📍 **Пробитый уровень:** `{event['level']:.5f}`\n"
        f"⚡️ **Текущая цена:** `{event['entry']:.5f}`\n\n"
        f"📝 **План торговли:**\n"
        f"• Ждем подтверждения Expansion-свечой на M5 уровня `{event['entry']:.5f}`.\n\n"
        f"🛑 **SL:** `{event['stop_loss']:.5f}`\n"
        f"🎯 **T1:** `{event['target_1']:.5f}` | 🚀 **T2:** `{event['target_2']:.5f}`\n"
    )


def format_confirmed_signal(symbol, event, current_price):
    emoji = "🟢" if event["direction"] == "LONG" else "🔴"
    return (
        f"✅ **CRT SWEEP EXPANSION CONFIRMED** ✅\n\n"
        f"{emoji} **Инструмент:** `{symbol}`\n"
        f"📊 **Направление:** `{event['direction']}` (База: `{event['tf']}`)\n"
        f"⚡️ **Подтверждение на M5 зафиксировано!**\n\n"
        f"📍 **Вход:** `{current_price:.5f}`\n"
        f"🛑 **Стоп-лосс:** `{event['stop_loss']:.5f}`\n"
        f"🎯 **T1:** `{event['target_1']:.5f}` | **T2:** `{event['target_2']:.5f}`\n\n"
        f"🚀 _Вход по правилам CRT & SMC готов!_"
    )
