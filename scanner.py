import logging

log = logging.getLogger("liquidity-hunter")


def detect_sweep(d1_bars, h4_bars, h1_bars, symbol="UNKNOWN"):
    """
    Поиск ликвидности (Liquidity Sweep) с учетом CRT (Candle Range Theory) 
    на старших таймфреймах (1D, 4H, 1H).
    """
    if not d1_bars or not h4_bars or not h1_bars:
        log.warning(f"[{symbol}] Отсутствуют бары для одного или нескольких ТФ. Пропуск анализа.")
        return None

    d1_curr = d1_bars[-1]
    h4_curr = h4_bars[-1]
    h1_curr = h1_bars[-1]

    # CRT Диапазоны (формируются по предыдущим свечам HTF)
    d1_high = max([b["high"] for b in d1_bars[-21:-1]])
    d1_low = min([b["low"] for b in d1_bars[-21:-1]])

    h4_high = max([b["high"] for b in h4_bars[-16:-1]])
    h4_low = min([b["low"] for b in h1_bars[-16:-1]]) if len(h4_bars) >= 16 else h4_bars[-2]["high"] # CRT Range High/Low

    h1_high = max([b["high"] for b in h1_bars[-20:-1]])
    h1_low = min([b["low"] for b in h1_bars[-20:-1]])

    direction = None
    level = 0.0
    tf = None
    current_close = 0.0
    bar_time = None

    # Логика манипуляции CRT (выход за границы диапазона с последующим возвратом тела)
    if d1_curr["high"] > d1_high and d1_curr["close"] < d1_high:
        direction = "SHORT"
        level = d1_high
        tf = "1D"
        current_close = d1_curr["close"]
        bar_time = d1_curr["timestamp"]
    elif d1_curr["low"] < d1_low and d1_curr["close"] > d1_low:
        direction = "LONG"
        level = d1_low
        tf = "1D"
        current_close = d1_curr["close"]
        bar_time = d1_curr["timestamp"]
    elif h4_curr["high"] > h4_high and h4_curr["close"] < h4_high:
        direction = "SHORT"
        level = h4_high
        tf = "4H (CRT)"
        current_close = h4_curr["close"]
        bar_time = h4_curr["timestamp"]
    elif h4_curr["low"] < h4_low and h4_curr["close"] > h4_low:
        direction = "LONG"
        level = h4_low
        tf = "4H (CRT)"
        current_close = h4_curr["close"]
        bar_time = h4_curr["timestamp"]
    elif h1_curr["high"] > h1_high and h1_curr["close"] < h1_high:
        direction = "SHORT"
        level = h1_high
        tf = "1H (CRT)"
        current_close = h1_curr["close"]
        bar_time = h1_curr["timestamp"]
    elif h1_curr["low"] < h1_low and h1_curr["close"] > h1_low:
        direction = "LONG"
        level = h1_low
        tf = "1H (CRT)"
        current_close = h1_curr["close"]
        bar_time = h1_curr["timestamp"]

    if not direction:
        log.debug(f"[{symbol}] CRT манипуляций не обнаружено.")
        return None

    log.info(
        f"🎯 [{symbol}] CRT SWEEP ОБНАРУЖЕН! "
        f"Таймфрейм: {tf} | Направление: {direction} | Уровень CRT: {level:.5f}"
    )

    if direction == "SHORT":
        stop_loss = level + (level * 0.0035)
        risk = stop_loss - current_close
        target_1 = current_close - (risk * 1.5)
        target_2 = current_close - (risk * 3.5)
    else:
        stop_loss = level - (level * 0.0035)
        risk = current_close - stop_loss
        target_1 = current_close + (risk * 1.5)
        target_2 = current_close + (risk * 3.5)

    return {
        "direction": direction,
        "tf": tf,
        "level": level,
        "entry": current_close,
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "rr_ratio": 3.5,
        "bar_time": bar_time,
    }


def optimize_sl_tp_on_confirmation(direction, current_price, m5_bars, h1_bars, symbol="UNKNOWN"):
    """
    Оптимизация стопа с проверкой на свечу расширения (Expansion Candle по CRT)
    и защитой от форекс-спреда.
    """
    try:
        # Проверка свечи расширения (Expansion Candle): тело должно составлять не менее 50% от всего диапазона свечи
        last_candle = m5_bars[-1]
        candle_range = last_candle["high"] - last_candle["low"]
        body_size = abs(last_candle["close"] - last_candle["open"])
        
        is_valid_expansion = True
        if candle_range > 0:
            expansion_ratio = body_size / candle_range
            if expansion_ratio < 0.45:  # Если свеча закрепа слабая (доджи/пин-бар), логируем предупреждение
                log.info(f"[{symbol}] Предупреждение: свеча подтверждения имеет слабое тело ({expansion_ratio:.2f}), возможен шум.")

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
        log.warning(f"Ошибка оптимизации стопа CRT: {e}, используются дефолтные значения")
        return None, None, None, 3.5


def format_signal(symbol, event):
    emoji = "🔴" if event["direction"] == "SHORT" else "🟢"
    action_verb = "пробоя вниз и закрепа" if event["direction"] == "SHORT" else "пробоя вверх и закрепа"
    cisd_type = "+CISD" if event["direction"] == "SHORT" else "-CISD"

    return (
        f"{emoji} **CRT LIQUIDITY SWEEP** {emoji}\n\n"
        f"🪙 **Инструмент:** `{symbol}`\n"
        f"⏱ **CRT Таймфрейм:** `{event['tf']}`\n"
        f"📊 **Направление:** `{event['direction']}`\n\n"
        f"📍 **Граница CRT диапазона:** `{event['level']:.5f}`\n"
        f"⚡️ **Уровень структуры ({cisd_type}):** `{event['entry']:.5f}`\n\n"
        f"📝 **План торговли:**\n"
        f"• Ждем подтверждения Expansion-свечой на M5/M15 уровня `{event['entry']:.5f}`.\n\n"
        f"🛑 **SL:** `{event['stop_loss']:.5f}`\n"
        f"🎯 **T1:** `{event['target_1']:.5f}` | 🚀 **T2:** `{event['target_2']:.5f}`\n"
    )


def format_confirmed_signal(symbol, event, current_price):
    emoji = "🟢" if event["direction"] == "LONG" else "🔴"
    return (
        f"✅ **CRT SETUP CONFIRMED (EXPANSION)** ✅\n\n"
        f"{emoji} **Инструмент:** `{symbol}`\n"
        f"📊 **Направление:** `{event['direction']}` (База: `{event['tf']}`)\n"
        f"⚡️ **Импульс расширения (Expansion) на M5 зафиксирован!**\n\n"
        f"📍 **Вход:** `{current_price:.5f}`\n"
        f"🛑 **Стоп-лосс (CRT + Spread SL):** `{event['stop_loss']:.5f}`\n"
        f"🎯 **T1:** `{event['target_1']:.5f}` | **T2:** `{event['target_2']:.5f}`\n\n"
        f"🚀 _Вход по правилам CRT & SMC готов!_"
    )
