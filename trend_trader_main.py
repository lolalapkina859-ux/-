import json, os, time, threading
from pathlib import Path
from datetime import datetime, timezone
import requests
from multi_asset_data import INSTRUMENTS, get_m5
from trend_trader import get_signal
from qml_scanner import QMLScanner

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN','').strip()
CHAT = os.getenv('TELEGRAM_CHAT_ID','').strip()
INTERVAL = int(os.getenv('SCAN_INTERVAL_SECONDS','60'))
STATE_FILE = Path(os.getenv('SIGNAL_STATE_FILE','/tmp/trend_trader_state.json'))
QML_STATE_FILE = Path(os.getenv('QML_STATE_FILE','/tmp/qml_scanner_state.json'))

TIMEFRAMES = ["5m", "15m", "1h", "4h"]


def load_state(filepath):
    try: return json.loads(filepath.read_text())
    except Exception: return {}

def save_state(filepath, data):
    try: filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as e: print(f"State save error: {e}")

def send(text):
    if not TOKEN or not CHAT: raise RuntimeError('Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Railway Variables')
    r=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage', json={'chat_id':CHAT,'text':text}, timeout=20)
    r.raise_for_status()
    if not r.json().get('ok'): raise RuntimeError(f"Telegram API error: {r.text}")

def fmt(x): return f'{x:.5f}' if abs(x)<100 else f'{x:.2f}'


def run_qml_thread():
    """Отдельный поток для QML-сканера (не затрагивает Trend Trader)"""
    print('=== TRADE VISION 24/7 | QML FTB SCANNER STARTED ===')
    qml_state = load_state(QML_STATE_FILE)
    scanner = QMLScanner(zigzag_len=9)
    
    # Пытаемся импортировать функцию загрузки таймфреймов, либо fallback на get_m5
    try:
        from multi_asset_data import get_klines
    except ImportError:
        def get_klines(symbol, tf):
            return get_m5(symbol) if tf in ["5m", "m5"] else None

    while True:
        for symbol in INSTRUMENTS:
            for tf in TIMEFRAMES:
                try:
                    df = get_klines(symbol, tf)
                    if df is None or df.empty:
                        continue
                    
                    signals = scanner.scan_dataframe(df, tf, symbol)
                    for sig in signals:
                        key = f"{symbol}:{tf}:{sig['type']}:{sig['level']:.4f}"
                        if qml_state.get(key):
                            continue
                        
                        emoji = "🟢" if "BULLISH" in sig['type'] else "🔴"
                        status_tag = "FTB Touch" if sig['is_ftb_touch'] else "Structure BOS Break"
                        
                        msg = (
                            f"📊 TRADE VISION 24/7 — QML FTB SIGNAL\n\n"
                            f"Инструмент: {symbol}\n"
                            f"Таймфрейм: {tf}\n"
                            f"Паттерн: {emoji} {sig['type']}\n"
                            f"Зона QML: {fmt(sig['level'])}\n"
                            f"Текущая цена: {fmt(sig['price'])}\n"
                            f"Статус: {status_tag}\n\n"
                            f"⚠️ Сигнал информационный."
                        )
                        
                        send(msg)
                        qml_state[key] = time.time()
                        save_state(QML_STATE_FILE, qml_state)
                        print(f"[QML] {symbol} {tf} {sig['type']}: sent")
                except Exception as e:
                    print(f"[QML] {symbol} {tf} ERROR: {e}")
                time.sleep(0.1)
        time.sleep(INTERVAL)


def run():
    state=load_state(STATE_FILE)
    print('=== TRADE VISION 24/7 | TREND TRADER M5 ===')
    print('Instruments:', ', '.join(INSTRUMENTS))
    print('Preset: Trend Trader | ATR 10 x 10 | TP 0.5/1.0/1.5% | SL 1.0% | TP4 OFF')
    
    # Запуск QML-сканера в параллельном автономном потоке
    qml_thread = threading.Thread(target=run_qml_thread, daemon=True)
    qml_thread.start()

    while True:
        started=time.time()
        for symbol in INSTRUMENTS:
            try:
                df=get_m5(symbol)
                sig=get_signal(df)
                if not sig:
                    print(f'{symbol}: no closed-candle crossover')
                    continue
                key=f"{symbol}:{sig['side']}:{sig['candle_time']}"
                if state.get(symbol)==key:
                    print(f'{symbol}: duplicate skipped')
                    continue
                msg=(
                    f"📊 TRADE VISION 24/7 — TREND TRADER M5\n\n"
                    f"Инструмент: {symbol}\n"
                    f"Направление: {'🟢 BUY' if sig['side']=='BUY' else '🔴 SELL'}\n"
                    f"Entry: {fmt(sig['entry'])}\n"
                    f"SL: {fmt(sig['sl'])}\n"
                    f"TP1 (0.5%): {fmt(sig['tps'][0])}\n"
                    f"TP2 (1.0%): {fmt(sig['tps'][1])}\n"
                    f"TP3 (1.5%): {fmt(sig['tps'][2])}\n"
                    f"Smart Trail: {fmt(sig['trail'])}\n"
                    f"Свеча M5 закрылась: {sig['candle_time']}\n\n"
                    f"⚠️ Сигнал информационный. Источник котировок может отличаться от цены твоего брокера."
                )
                send(msg); state[symbol]=key; save_state(STATE_FILE, state)
                print(f'{symbol}: {sig["side"]} sent')
            except Exception as e:
                print(f'{symbol}: ERROR: {e}')
            time.sleep(0.25)
        time.sleep(max(5, INTERVAL-(time.time()-started)))

if __name__=='__main__': run()
