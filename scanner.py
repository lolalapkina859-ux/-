import logging

log = logging.getLogger("liquidity-hunter")


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


def find_bill_williams_fractals(bars, n=2):
    """Поиск классических 5-барочных фракталов Билла Уильямса."""
    fractal_highs = []
    fractal_lows = []

    if len(bars) < (n * 2 + 1):
        return fractal_highs, fractal_lows

    for i in range(n, len(bars) - n):
        is_fractal_high = (
            bars[i]["high"] > bars[i - 2]["high"]
            and bars[i]["high"] > bars[i - 1]["high"]
            and bars[i]["high"] > bars[i + 1]["high"]
            and bars[i]["high"] > bars[i + 2]["high"]
        )
        if is_fractal_high:
            fractal_highs.append({"price": bars[i]["high"], "timestamp": bars[i]["timestamp"]})

        is_fractal_low = (
            bars[i]["low"] < bars[i - 2]["low"]
            and bars[i]["low"] < bars[i - 1]["low"]
            and bars[i]["low"] < bars[i + 1]["low"]
            and bars[i]["low"] < bars[i + 2]["low"]
        )
        if is_fractal_low:
            fractal_lows.append({"price": bars[i]["low"], "timestamp": bars[i]["timestamp"]})

    return fractal_highs, fractal_lows


def detect_macro_range_sweep(h4_bars, h1_bars, symbol="UNKNOWN", current_price=None):
    """
    Поиск макро-свипов с фильтрацией по тренду (EMA 200) и зонам Premium / Discount.
    """
    if not h4_bars or len(h4_bars) < 50:
        return None

    ema200 = calculate_ema(h4_bars, period=200)
    h4_curr = h4_bars[-1]
    active_entry = current_price if current_price is not None and current_price > 0 else h4_curr["close"]

    # 1. Жесткий трендовый фильтр по EMA 200
    if ema200:
        if active_entry > ema200 and h4_curr["high"] > max([b["high"] for b in h4_bars[-30:-1]]):
            # Если цена выше EMA 200, шортить запрещено
            pass

    fractal_highs, fractal_lows = find_bill_williams_fractals(h4_bars, n=2)
    if not fractal_highs or not fractal_lows:
        return None

    macro_high = fractal_highs[-1]["price"]
    macro_low = fractal_lows[-1]["price"]
    eq_level = (macro_high + macro_low) / 2.0

    setup_type = None
    direction = None
    level = 0.0

    # 2. Свип фрактального верха -> ШОРТ (обязательно из зоны PREMIUM: цена > EQ и выше EMA 200)
    if h4_curr["high"] > macro_high and h4_curr["close"] <= macro_high:
        if ema200 and active_entry > ema200:
            return None  # Блокируем шорт против бычьего тренда EMA 200
        
        if active_entry < eq_level:
            return None  # Отсекаем шорт в зоне Discount (нельзя шортить снизу)

        upper_wick = h4_curr["high"] - max(h4_curr["open"], h4_curr["close"])
        body_size = abs(h4_curr["close"] - h4_curr["open"])
        if upper_wick > 0 and (upper_wick >= body_size * 0.25):
            setup_type = "MACRO_RANGE_SWEEP"
            direction = "SHORT"
            level = macro_high

    # 3. Свип фрактального низа -> ЛОНГ (обязательно из зоны DISCOUNT: цена < EQ и ниже EMA 200)
    elif h4_curr["low"] < macro_low and h4_curr["close"] >= macro_low:
        if ema200 and active_entry < ema200:
            return None  # Блокируем лонг против медвежьего тренда EMA 200
            
        if active_entry > eq_level:
            return None  # Отсекаем лонг в зоне Premium (нельзя покупать на макушке)

        lower_wick = min(h4_curr["open"], h4_curr["close"]) - h4_curr["low"]
        body_size = abs(h4_curr["close"] - h4_curr["open"])
        if lower_wick > 0 and (lower_wick >= body_size * 0.25):
            setup_type = "MACRO_RANGE_SWEEP"
            direction = "LONG"
            level = macro_low

    if not setup_type:
        return None

    log.info(
        f"🏛 [{symbol}] SMART MONEY FRACTAL SWEEP! "
        f"Направление: {direction} | Уровень: {level:.5f} | EQ (Зона): {eq_level:.5f}"
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
        "tf": "4H (Smart Money Macro)",
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
    return detect_macro_range_sweep(h4_bars, h1_bars, symbol, current_price)


def optimize_sl_tp_on_confirmation(direction, current_price, m5_bars, h1_bars, symbol="UNKNOWN"):
    """
    Проверка подтверждения через локальный слом структуры (CHoCH) на M5 + оптимизация стопа.
    """
    try:
        if len(m5_bars) < 5:
            return None, None, None, 3.5

        # Проверка слома структуры (CHoCH) на последних барах M5
        recent_m5 = m5_bars[-5:]
        if direction == "LONG":
            # Нужен слом локального хая на M5 (импульсный выход вверх)
            local_m5_high = max([b["high"] for b in recent_m5[:-1]])
            if recent_m5[-1]["close"] <= local_m5_high:
                return None, None, None, 3.5  # Нет слома структуры в лонг
        else:
            # Нужен слом локального лоу на M5 (импульсный выход вниз)
            local_m5_low = min([b["low"] for b in recent_m5[:-1]])
            if recent_m5[-1]["close"] >= local_m5_low:
                return None, None, None, 3.5  # Нет слома структуры в шорт

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
            
            target_1 = current_price + (risk * 2.0)
            target_2 = current_price + (risk * 4.0)
        else:
            local_high = max([b["high"] for b in m5_bars[-8:]])
            stop_loss = local_high * (1.0 + buffer_pct)
            risk = stop_loss - current_price
            
            target_1 = current_price - (risk * 2.0)
            target_2 = current_price - (risk * 4.0)

        return stop_loss, target_1, target_2, 3.5
    except Exception as e:
        log.warning(f"Ошибка оптимизации стопа с CHoCH: {e}")
        return None, None, None, 3.5 


def format_signal(symbol, event):
    return (
        f"🏛 **SMART MONEY RANGE SWEEP** 🏛\n\n"
        f"🪙 **Инструмент:** `{symbol}`\n"
        f"⏱ **Таймфрейм:** `{event['tf']}`\n"
        f"📊 **Направление:** `{event['direction']}` (Зона Premium/Discount учтена)\n\n"
        f"📍 **Уровень закола:** `{event['level']:.5f}`\n"
        f"⚖️ **EQ (Середина диапазона):** `{event['eq_level']:.5f}`\n\n"
        f"📝 **План:** Ждем подтверждения слома структуры (CHoCH) на M5.\n\n"
        f"🛑 **SL:** `{event['stop_loss']:.5f}`\n"
        f"🎯 **T1 (EQ):** `{event['target_1']:.5f}` | 🚀 **T2:** `{event['target_2']:.5f}`\n"
    )


def format_confirmed_signal(symbol, event, current_price):
    emoji = "🟢" if event["direction"] == "LONG" else "🔴"
    return (
        f"✅ **SMART MONEY CHoCH CONFIRMED** ✅\n\n"
        f"{emoji} **Инструмент:** `{symbol}`\n"
        f"📊 **Направление:** `{event['direction']}`\n"
        f"⚡️ **Слом структуры (CHoCH) на M5 подтвержден!**\n\n"
        f"📍 **Вход:** `{current_price:.5f}`\n"
        f"🛑 **Стоп-лосс:** `{event['stop_loss']:.5f}`\n"
        f"🎯 **T1:** `{event['target_1']:.5f}` | **T2:** `{event['target_2']:.5f}`\n\n"
        f"🚀 _Сделка открыта по строгим правилам SMC._"
    )
