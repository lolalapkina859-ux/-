import asyncio
import logging
from qml_scanner import QMLScanner

logger = logging.getLogger("QMLRunner")

# Укажите нужные монеты и таймфреймы
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XAUUSD"]
TIMEFRAMES = ["5m", "15m", "1h", "4h"]

async def run_qml_scanner_loop(bot_client, data_provider):
    """
    Параллельный фоновый цикл сканирования QML.
    bot_client: экземпляр вашего Telegram-бота
    data_provider: объект для получения исторической свечной информации (например, MultiAssetDataProvider)
    """
    scanner = QMLScanner(zigzag_len=9)
    sent_signals = set()  # Множество для исключения дубликатов сигналов

    logger.info("QML Scanner successfully started in background loop.")

    while True:
        try:
            for symbol in SYMBOLS:
                for tf in TIMEFRAMES:
                    # Загрузка свечей по символу и таймфрейму
                    df = data_provider.get_historical_data(symbol, tf, limit=300)
                    if df is None or df.empty:
                        continue

                    signals = scanner.scan_dataframe(df, tf, symbol)

                    for sig in signals:
                        # Формируем уникальный ключ сигнала, чтобы не слать одно и то же на каждой итерации
                        sig_key = f"{sig['symbol']}_{sig['timeframe']}_{sig['type']}_{sig['level']:.4f}"
                        if sig_key in sent_signals:
                            continue

                        sent_signals.add(sig_key)
                        
                        # Ограничиваем кеш дубликатов, чтобы memory leak не происходил
                        if len(sent_signals) > 1000:
                            sent_signals.clear()

                        emoji = "🟢" if "BULLISH" in sig['type'] else "🔴"
                        status_tag = "FTB Touch" if sig['is_ftb_touch'] else "Structure BOS Break"

                        msg = (
                            f"{emoji * 3} **QML FTB SIGNAL** {emoji * 3}

"
                            f"🔹 **Монета / Инструмент:** `{sig['symbol']}`
"
                            f"⏱ **Таймфрейм:** `{sig['timeframe']}`
"
                            f"📊 **Паттерн:** `{sig['type']}`
"
                            f"📍 **Уровень зоны:** `{sig['level']:.4f}`
"
                            f"💰 **Текущая цена:** `{sig['price']:.4f}`
"
                            f"⚡️ **Статус:** `{status_tag}`
"
                        )

                        # Отправка отдельным сообщением в Telegram
                        if hasattr(bot_client, 'send_alert_message'):
                            await bot_client.send_alert_message(msg)
                        elif hasattr(bot_client, 'send_message'):
                            # Пример отправки через aiogram / telebot
                            # await bot_client.send_message(CHAT_ID, msg, parse_mode="Markdown")
                            pass

            # Интервал между проверками (в секундах)
            await asyncio.sleep(60)

        except Exception as e:
            logger.error(f"Ошибка в цикле QML сканера: {e}")
            await asyncio.sleep(30)
