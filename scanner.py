import logging

log = logging.getLogger("liquidity-hunter")


def calculate_ema(bars, period=200):
    """Расчет экспоненциальной скользящей средней (EMA) для трендового фильтра."""
    if not bars or len(bars) < period:
        return None
    closes = [b["close"] for b in bars]
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period  начинаем с SMA для первого значения
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


def find_bill_williams_fractals(bars, n=2):
    """
    Поиск классических 5-барочных фракталов Билла Уильямса на старшем таймфрейме (4H).
    Возвращает списки подтвержденных фрактальных вершин (High) и низин (Low).
    """
    fractal_highs = []
    fractal_lows = []

    if len(bars) < (n * 2 + 1):
        return fractal_highs, fractal_lows

    for i in range(n, len(bars) - n):
        # 5-барочный фрактал High (Вершина)
        is_fractal_high = (
            bars[i]["high'] > bars[i - 2]["high"]
            and bars[i]["high"] > bars[i - 1]["high"]
            and bars[i]["high"] > bars[i + 1]["high"]
            and bars[i]["high"] > bars[i + 2]["high"]
        )
        if is_fractal_high:
            fractal_highs.append({"price": bars[i]["high"], "timestamp": bars[i]["timestamp"]})

        # 5-барочный фрактал Low (Низина)
        is_fractal_low = (
            bars[i]["low"] < bars[i - 2]["low"]
            and bars[i]["low"] < bars[i - 1]["low"]
            and bars[i]["low"] < bars[i + 1]["low"]
            and bars[i]["low"] < bars[i + 2]["low"]
        )
        if is_fractal_low:
            fractal_lows.append({"price": bars[i]["low"], "timestamp": bars[i]["timestamp"]})

    return fractal_highs, fractal_lows


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
    Поиск макро-свипов по фрактальным уровням Билла Уильямса + Трендовый фильтр (EMA 200).
    """
    if not h4_bars or len(h4_bars) < 50:
        return None

    # 1. Трендовый фильтр: запрещаем контртренд против EMA 200 на 4H
    ema200 = calculate_ema(h4_bars, period=200)
    h4_curr = h4_bars[-1]
    active_entry = current_price if current_price is not None and current_price > 0 else h4_curr["close"]

    if ema200:
        if active_entry > ema200:
            # Тренд глобально восходящий -> Шорты по макро-свипам блокируем
            pass  # Можно жестко заблокировать: return None (оставляем гибко или фильтруем шорты)

    # 2. Ищем фрактальные уровни по Биллу Уильямсу
    fractal_highs, fractal_lows = find_bill_williams_fractals(h4_bars, n=2)
    if not fractal_highs or not fractal_lows:
        return None

    # Берем самый свежий значимый фрактальный уровень как границу диапазона
    macro_high = fractal_highs[-1]["price"]
    macro_low = fractal_lows[-1]["price"]
    
    # Также рассчитываем EQ (середину диапазона) по последним экстремумам
    eq_level = (macro_high + macro_low) / 2.0

    setup_type = None
    direction = None
    level = 0.0

    # Жесткий трендовый фильтр: если цена выше EMA 200, шортить макро-свип запрещено
    if ema200 and active_entry > ema200:
        # Разрешаем только LONG свипы по тренду
        pass
    
    # 3. Свип фрактальной вершины (Short Setup)
    if h4_curr["high"] > macro_high and h4_curr["close"] <= macro_high:
        if ema200 and active_entry > ema200:
            return None  # Отсекаем шорт против бычьего тренда EMA 200
            
        upper_wick = h4_curr["high"] - max(h4_curr["open"], h4_curr["close"])
        body_size = abs(h4_curr["close"] - h4_curr["open"])
        if upper_wick > 0 and (upper_wick >= body_size * 0.25):
            setup_type = "MACRO_RANGE_SWEEP"
            direction = "SHORT"
            level = macro_high

    # 4. Свип фрактальной низины (Long Setup)
    elif h4_curr["low"] < macro_low and h4_curr["close"] >= macro_low:
        if ema200 and active_entry < ema200:
            return None  # Отсекаем лонг против медвежьего тренда EMA 200
            
        lower_wick = min(h4_curr["open"], h4_curr["close"]) - h4_curr["low"]
        body_size = abs(h4_curr["close"] - h4_curr["open"])
        if lower_wick > 0 and (lower_wick >= body_size * 0.25):
            setup_type = "MACRO_RANGE_SWEEP"
            direction = "LONG"
            level = macro_low

    if not setup_type:
        return None

    log.info(
        f"🏛 [{symbol}] ФРАКТАЛЬНЫЙ MACRO SWEEP! "
        f"Направление: {direction} | Фрактал: {level:.5f} | EQ: {eq_level:.5f}"
    )

    atr = calculate_atr(h4_bars, period=14)
    buffer = atr * 0.5 if atr > 0 else level * 0.0035

    if direction == "SHORT":
        stop_loss = level + buffer
        target_1 = eq_level
        target_2 = macro_low
    else:
        stop_loss = level - buffer
        target_1 = eq_level
        target_2 = macro_high

    return {
        "setup_type": "MACRO_RANGE_SWEEP",
        "direction": direction,
        "tf": "4H (Fractal Macro)",
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
    Основной диспетчер поиска свипов с фрактальной фильтрацией.
    """
    macro_event = detect_macro_range_sweep(h4_bars, h1_bars, symbol, current_price)
    if macro_event:
        return macro_event

    if not h4_bars or not h1_bars:
        return None

    h4_curr = h4_bars[-1]
    h1_curr = h1_bars[-1]

    # Локальные фрактальные уровни для CRT
    f_highs_4h, f_lows_4h = find_bill_williams_fractals(h4_bars, n=2)
    f_highs_1h, f_lows_1h = find_bill_williams_fractals(h1_bars, n=2)

    h4_high = f_highs_4h[-1]["price"] if f_highs_4h else max(b["high"] for b in h4_bars[-16:-1])
    h4_low = f_lows_4h[-1]["price"] if f_lows_4h else min(b["low"] for b in h4_bars[-16:-1])
    
    h1_high = f_highs_1h[-1]["price"] if f_highs_1h else max(b["high"] for b in h1_bars[-20:-1])
    h1_low = f_lows_1h[-1]["price"] if f_lows_1h else min(b["low"] for b in h1_bars[-20:-1])

    direction = None
    level = 0.0
    tf = None
    bar_time = None
    htf_bars_ref = None

    ema200 = calculate_ema(h4_bars, period=200)
    active_entry = current_price if current_price is not None and current_price > 0 else h1_curr["close"]

    # Проверка 4H CRT Sweep
    if h4_curr["high"] > h4_high and h4_curr["close"] <= h4_high:
        if not (ema200 and active_entry > ema200):  # Фильтр тренда
            upper_wick = h4_curr["high"] - max(h4_curr["open"], h4_curr["close"])
            body_size = abs(h4_curr["close"] - h4_curr["open"])
            if upper_wick > 0 and (upper_wick >= body_size * 0.3):
                level = h4_high
                tf = "4H (Fractal CRT)"
                bar_time = h4_curr["timestamp"]
                htf_bars_ref = h4_bars
                direction = "SHORT"

    elif h4_curr["low"] < h4_low and h4_curr["close"] >= h4_low:
        if not (ema200 and active_entry < ema200):
            lower_wick = min(h4_curr["open"], h4_curr["close"]) - h4_curr["low"]
            body_size = abs(h4_curr["close"] - h4_curr["open"])
            if lower_wick > 0 and (lower_wick >= body_size * 0.3):
                level = h4_low
                tf = "4H (Fractal CRT)"
                bar_time = h4_curr["timestamp"]
                htf_bars_ref = h4_bars
                direction = "LONG"

    # Проверка 1H CRT Sweep если на 4H пусто
    if not direction:
        if h1_curr["high"] > h1_high and h1_curr["close"] <= h1_high:
            if not (ema200 and active_entry > ema200):
                upper_wick = h1_curr["high"] - max(h1_curr["open"], h1_curr["close"])
                body_size = abs(h1_curr["close"] - h1_curr["open"])
                if upper_wick > 0 and (upper_wick >= body_size * 0.3):
                    level = h1_high
                    tf = "1H (Fractal CRT)"
                    bar_time = h1_curr["timestamp"]
                    htf_bars_ref = h1_bars
                    direction = "SHORT"

        elif h1_curr["low"] < h1_low and h1_curr["close"] >= h1_low:
            if not (ema200 and active_entry < ema200):
                lower_wick = min(h1_curr["open"], h1_curr["close"]) - h1_curr["low"]
                body_size = abs(h1_curr["close"] - h1_curr["open"])
                if lower_wick > 0 and (lower_wick >= body_size * 0.3):
                    level = h1_low
                    tf = "1H (Fractal CRT)"
                    bar_time = h1_curr["timestamp"]
                    htf_bars_ref = h1_bars
                    direction = "LONG"

    if not direction:
        return None

    if is_strong_impulse_against(direction, htf_bars_ref):
        return None

    atr = calculate_atr(h4_bars, period=14)
    buffer = atr * 0.4 if atr > 0 else level * 0.003

    if direction == "SHORT":
        stop_loss = level + buffer
        risk = stop_loss - active_entry
        target_1 = active_entry - (risk * 1.5)
        target_2 = active_entry - (risk * 3.5)
    else:
        stop_loss = level - buffer
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
        buffer_pct = 0.0018 if is_forex else 0.0010

        if direction == "LONG":
            local_low = min([b["low"] for b in m5_bars[-8:]])
            stop_loss = local_low * (1.0 - buffer_pct)
            risk = current_price - stop_loss
            
            min_risk = current_price * 0.0015 if is_forex else current_price * 0.003
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
            
            min_risk = current_price * 0.0015 if is_forex else current_price * 0.003
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
        title = "🏛 FRACTAL MACRO RANGE SWEEP 🏛"
        plan_note = f"Фрактальный макро-свип уровня `{event['level']:.5f}`. Ждем подтверждения на M5."
    else:
        title = "🔴 FRACTAL CRT SWEEP 🔴" if event["direction"] == "SHORT" else "🟢 FRACTAL CRT SWEEP 🟢"
        plan_note = f"Ждем подтверждения Expansion-свечой на M5 уровня `{event['entry']:.5f}`."

    return (
        f"{title}\n\n"
        f"🪙 **Инструмент:** `{symbol}`\n"
        f"⏱ **Таймфрейм:** `{event['tf']}`\n"
        f"📊 **Тип:** `{event.get('setup_type', 'SWEEP')}` | **Направление:** `{event['direction']}`\n\n"
        f"📍 **Фрактальный уровень:** `{event['level']:.5f}`\n"
        f"⚡️ **Текущая цена:** `{event['entry']:.5f}`\n\n"
        f"📝 **План торговли:**\n"
        f"• {plan_note}\n\n"
        f"🛑 **SL (с ATR буфером):** `{event['stop_loss']:.5f}`\n"
        f"🎯 **T1 (EQ):** `{event['target_1']:.5f}` | 🚀 **T2:** `{event['target_2']:.5f}`\n"
    )


def format_confirmed_signal(symbol, event, current_price):
    emoji = "🟢" if event["direction"] == "LONG" else "🔴"
    setup_name = "FRACTAL RANGE SWEEP CONFIRMED" if event.get("setup_type") == "MACRO_RANGE_SWEEP" else "FRACTAL CRT EXPANSION"
    return (
        f"✅ **{setup_name}** ✅\n\n"
        f"{emoji} **Инструмент:** `{symbol}`\n"
        f"📊 **Направление:** `{event['direction']}` (База: `{event['tf']}`)\n"
        f"⚡️ **Подтверждение на M5 зафиксировано!**\n\n"
        f"📍 **Вход:** `{current_price:.5f}`\n"
        f"🛑 **Стоп-лосс:** `{event['stop_loss']:.5f}`\n"
        f"🎯 **T1:** `{event['target_1']:.5f}` | **T2:** `{event['target_2']:.5f}`\n\n"
        f"🚀 _Вход по фрактальным уровням и SMC готов!_"
    )
