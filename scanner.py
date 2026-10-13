import logging

# Настройка логгера для модуля Liquidity Hunter FX
log = logging.getLogger("liquidity-hunter")


def detect_sweep(d1_bars, h4_bars, h1_bars, symbol="UNKNOWN"):
    """
    Поиск ликвидности (Liquidity Sweep) независимо по таймфреймам 1D, 4H, 1H.
    """
    if not d1_bars or not h4_bars or not h1_bars:
        log.warning(f"[{symbol}] Отсутствуют бары для одного или нескольких ТФ (1D, 4H, 1H). Пропуск анализа.")
        return None

    # Берем последние закрытые свечи для каждого таймфрейма
    d1_curr = d1_bars[-1]
    h4_curr = h4_bars[-1]
    h1_curr = h1_bars[-1]

    # Исторические экстремумы для поиска ликвидности (без учета текущей свечи)
    d1_high = max([b["high"] for b in d1_bars[-21:-1]])
    d1_low = min([b["low"] for b in d1_bars[-21:-1]])

    h4_high = max([b["high"] for b in h4_bars[-16:-1]])
    h4_low = min([b["low"] for b in h4_bars[-16:-1]])

    h1_high = max([b["high"] for b in h1_bars[-20:-1]])
    h1_low = min([b["low"] for b in h1_bars[-20:-1]])

    direction = None
    level = 0.0
    tf = None
    current_close = 0.0
    bar_time = None

    # 1. Проверяем старший таймфрейм 1D (снятие дневного пула)
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

    # 2. Проверяем 4H
    elif h4_curr["high"] > h4_high and h4_curr["close"] < h4_high:
        direction = "SHORT"
        level = h4_high
        tf = "4H"
        current_close = h4_curr["close"]
        bar_time = h4_curr["timestamp"]
    elif h4_curr["low"] < h4_low and h4_curr["close"] > h4_low:
        direction = "LONG"
        level = h4_low
        tf = "4H"
        current_close = h4_curr["close"]
        bar_time = h4_curr["timestamp"]

    # 3. Проверяем 1H
    elif h1_curr["high"] > h1_high and h1_curr["close"] < h1_high:
        direction = "SHORT"
        level = h1_high
        tf = "1H"
        current_close = h1_curr["close"]
        bar_time = h1_curr["timestamp"]
    elif h1_curr["low"] < h1_low and h1_curr["close"] > h1_low:
        direction = "LONG"
        level = h1_low
        tf = "1H"
        current_close = h1_curr["close"]
        bar_time = h1_curr["timestamp"]

    if not direction:
        log.debug(f"[{symbol}] Свипов ликвидности не обнаружено.")
        return None

    log.info(
        f"🎯 [{symbol}] Обнаружен LIQUIDITY SWEEP! "
        f"Таймфрейм: {tf} | Направление: {direction} | Уровень: {level:.5f} | Закрытие: {current_close:.5f}"
    )

    # Расчет стоп-лосса и целей
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

    log.debug(
        f"[{symbol}] Расчетные параметры: SL={stop_loss:.5f}, T1={target_1:.5f}, T2={target_2:.5f}, Risk={risk:.5f}"
    )

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


def format_signal(symbol, event):
    """Форматирование расширенного сообщения с SMC-инструкцией для Telegram"""
    log.debug(f"[{symbol}] Форматирование SMC-алерта для Telegram...")

    emoji = "🔴" if event["direction"] == "SHORT" else "🟢"
    action_verb = (
        "пробоя вниз и закрепа"
        if event["direction"] == "SHORT"
        else "пробоя вверх и закрепа"
    )
    cisd_type = "+CISD" if event["direction"] == "SHORT" else "-CISD"

    msg = (
        f"{emoji} **LIQUIDITY SWEEP ALERT** {emoji}\n\n"
        f"🪙 **Инструмент:** `{symbol}`\n"
        f"⏱ **Таймфрейм:** `{event['tf']}`\n"
        f"📊 **Направление:** `{event['direction']}`\n\n"
        f"📍 **Уровень снятия:** `{event['level']:.5f}`\n"
        f"⚡️ **Ключевой уровень структуры ({cisd_type}):** `{event['entry']:.5f}`\n\n"
        f"📝 **SMC Резюме и Инструкция:**\n"
        f"⚠️ _Не входите вслепую! Ждем реакции:_\n"
        f"• Дождитесь {action_verb} (закрытие M5/M15) уровня `{event['entry']:.5f}`.\n"
        f"• Входите только после подтверждения структуры.\n\n"
        f"🛑 **Стоп-лосс (SL):** `{event['stop_loss']:.5f}`\n"
        f"🎯 **T1 (Частичная фиксация / БУ):** `{event['target_1']:.5f}` _(R:R 1.5)_\n"
        f"🚀 **T2 (Глобальная цель):** `{event['target_2']:.5f}` _(R:R 3.5)_\n"
    )
    
    log.info(f"[{symbol}] Сформировано сообщение для Telegram (длина: {len(msg)} симв.).")
    return msg
