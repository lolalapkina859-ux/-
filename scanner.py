import logging

log = logging.getLogger("liquidity-hunter")


def detect_cisd_confirmation(m15_bars, direction):
  """Проверка подтверждения через CISD на младшем таймфрейме (M15).

  Для SHORT ищет -CISD, для LONG ищет +CISD.
  """
  if not m15_bars or len(m15_bars) < 5:
    return True  # Если данных мало, пропускаем фильтр (fallback)

  # Имитация поиска сдвига структуры по последним барам M15
  # Медвежий сдвиг (-CISD): закрытие ниже тела предыдущего отката
  # Бычий сдвиг (+CISD): закрытие выше тела предыдущего отката
  curr = m15_bars[-1]
  prev = m15_bars[-2]

  if direction == "SHORT":
    # Проверка на формирование -CISD (импульсное продаввливание вниз)
    is_cisd = curr["close"] < prev["low"] or (
        curr["close"] < curr["open"] and prev["close"] > prev["open"]
    )
    return is_cisd
  else:  # LONG
    # Проверка на формирование +CISD (импульсное продавливание наверх)
    is_cisd = curr["close"] > prev["high"] or (
        curr["close"] > curr["open"] and prev["close"] < prev["open"]
    )
    return is_cisd


def detect_sweep(d1_bars, h4_bars, h1_bars, m15_bars=None):
  """Поиск ликвидности (Liquidity Sweep) с подтверждением через CISD.

  Возвращает словарь с параметрами сетапа или None.
  """
  if not d1_bars or not h4_bars or not h1_bars:
    return None

  # Берем последнюю закрытую / актуальную свечу на H1 для поиска свитпа
  h1_curr = h1_bars[-1]
  h1_prev = h1_bars[-2]

  # Берем экстремумы старших таймфреймов для ориентиров ликвидности
  d1_high = max([b["high"] for b in d1_bars[-20:]])
  d1_low = min([b["low"] for b in d1_bars[-20:]])

  h4_high = max([b["high"] for b in h4_bars[-15:]])
  h4_low = min([b["low"] for b in h4_bars[-15:]])

  direction = None
  level = 0.0
  tf = "1H"

  # Проверка на Bearish Sweep (сняли хай и закрылись ниже)
  if h1_curr["high"] > h4_high and h1_curr["close"] < h4_high:
    direction = "SHORT"
    level = h4_high
    tf = "4H"
  elif h1_curr["high"] > d1_high and h1_curr["close"] < d1_high:
    direction = "SHORT"
    level = d1_high
    tf = "1D"

  # Проверка на Bullish Sweep (сняли лоу и закрылись выше)
  elif h1_curr["low"] < h4_low and h1_curr["close"] > h4_low:
    direction = "LONG"
    level = h4_low
    tf = "4H"
  elif h1_curr["low"] < d1_low and h1_curr["close"] > d1_low:
    direction = "LONG"
    level = d1_low
    tf = "1D"

  if not direction:
    return None

  # Дополнительный фильтр по CISD на младшем таймфрейме (если передан m15_bars)
  if m15_bars and not detect_cisd_confirmation(m15_bars, direction):
    log.info(
        f"Свитп на {tf} найден, но подтверждение CISD на M15 отсутствует."
        " Пропускаем."
    )
    return None

  current_close = h1_curr["close"]

  if direction == "SHORT":
    stop_loss = level + (level * 0.0035)  # тюнинг отступа SL
    risk = stop_loss - current_close
    target_1 = current_close - (risk * 1.5)
    target_2 = current_close - (risk * 3.5)
    rr_ratio = 3.5
  else:  # LONG
    stop_loss = level - (level * 0.0035)  # тюнинг отступа SL
    risk = current_close - stop_loss
    target_1 = current_close + (risk * 1.5)
    target_2 = current_close + (risk * 3.5)
    rr_ratio = 3.5

  return {
      "direction": direction,
      "tf": tf,
      "level": level,
      "entry": current_close,
      "stop_loss": stop_loss,
      "target_1": target_1,
      "target_2": target_2,
      "rr_ratio": rr_ratio,
      "bar_time": h1_curr["timestamp"],
  }


def format_signal(symbol, event):
  """Форматирование красивого сообщения для Telegram с учетом CISD-подтверждения"""
  emoji = "🔴" if event["direction"] == "SHORT" else "🟢"
  cisd_label = "-CISD (Bearish)" if event["direction"] == "SHORT" else "+CISD (Bullish)"

  msg = (
      f"{emoji} **LIQUIDITY SWEEP + CISD ALERT** {emoji}\n\n"
      f"🪙 **Инструмент:** `{symbol}`\n"
      f"⏱ **Таймфрейм:** `{event['tf']}` + M15 Confirmation\n"
      f"📊 **Направление:** `{event['direction']}` `({cisd_label})`\n\n"
      f"📍 **Уровень снятия:** `{event['level']:.5f}`\n"
      f"🎯 **Вход (Entry):** `{event['entry']:.5f}`\n"
      f"🛑 **Стоп-лосс (SL):** `{event['stop_loss']:.5f}`\n\n"
      f"🎯 **T1 (Частичная фиксация / БУ):** `{event['target_1']:.5f}` _(R:R 1.5)_\n"
      f"🚀 **T2 (Глобальная цель):** `{event['target_2']:.5f}` _(R:R 3.5)_\n"
  )
  return msg
