def detect_macro_range_sweep(h4_bars, h1_bars, symbol="UNKNOWN", current_price=None, d1_bars=None):
    """
    Упрощенный и чувствительный детектор свипов ликвидности с жесткой защитой от микро-стопов.
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

    # ЖЕСТКИЕ МИНИМАЛЬНЫЕ ОТСТУПЫ ДЛЯ СТОП-ЛОССА (в зависимости от инструмента)
    sym_upper = symbol.upper()
    if "BTC" in sym_upper:
        min_risk_points = 250.0   лья минимум $250 движения для BTC
    elif "ETH" in sym_upper:
        min_risk_points = 20.0    лья минимум $20 движения для ETH
    elif "XAU" in sym_upper or "GOLD" in sym_upper:
        min_risk_points = 5.0     лья минимум $5 для золота
    elif "JPY" in sym_upper:
        min_risk_points = 0.35    лья минимум по йеновым парам
    else:
        min_risk_points = active_entry * 0.0015  # Общий минимум 0.15% от цены

    atr = calculate_atr(h4_bars, period=14)
    calculated_buffer = atr * 0.6 if atr > 0 else min_risk_points
    
    # Итоговый риск берется как максимум между расчетом и жестким лимитом
    risk = max(calculated_buffer, min_risk_points)

    if direction == "SHORT":
        stop_loss = active_entry + risk
        target_1 = active_entry - (risk * 2.0)
        target_2 = active_entry - (risk * 3.5)
    else:
        stop_loss = active_entry - risk
        target_1 = active_entry + (risk * 2.0)
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
        "rr_ratio": 2.0,
        "bar_time": h4_curr["timestamp"],
        "confluence": "Liquidity Sweep + Hard Risk Floor + Structural TP",
        "fvg_zone": "Price Action Entry"
    }
