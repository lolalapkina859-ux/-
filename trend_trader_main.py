import json, os, time
from pathlib import Path
from datetime import datetime, timezone
import requests
from multi_asset_data import INSTRUMENTS, get_m5
from trend_trader import get_signal

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN','').strip()
CHAT = os.getenv('TELEGRAM_CHAT_ID','').strip()
INTERVAL = int(os.getenv('SCAN_INTERVAL_SECONDS','60'))
STATE_FILE = Path(os.getenv('SIGNAL_STATE_FILE','/tmp/trend_trader_state.json'))


def load_state():
    try: return json.loads(STATE_FILE.read_text())
    except Exception: return {}

def send(text):
    if not TOKEN or not CHAT: raise RuntimeError('Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Railway Variables')
    r=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage', json={'chat_id':CHAT,'text':text}, timeout=20)
    r.raise_for_status()
    if not r.json().get('ok'): raise RuntimeError(f"Telegram API error: {r.text}")

def fmt(x): return f'{x:.5f}' if abs(x)<100 else f'{x:.2f}'

def run():
    state=load_state()
    print('=== TRADE VISION 24/7 | TREND TRADER M5 ===')
    print('Instruments:', ', '.join(INSTRUMENTS))
    print('Preset: Trend Trader | ATR 10 x 10 | TP 0.5/1.0/1.5% | SL 1.0% | TP4 OFF')
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
                msg=(f"📊 TRADE VISION 24/7 — TREND TRADER M5\n\n"
                     f"Инструмент: {symbol}\nНаправление: {'🟢 BUY' if sig['side']=='BUY' else '🔴 SELL'}\n"
                     f"Entry: {fmt(sig['entry'])}\nSL: {fmt(sig['sl'])}\n"
                     f"TP1 (0.5%): {fmt(sig['tps'][0])}\nTP2 (1.0%): {fmt(sig['tps'][1])}\nTP3 (1.5%): {fmt(sig['tps'][2])}\n"
                     f"Smart Trail: {fmt(sig['trail'])}\nСвеча M5 закрылась: {sig['candle_time']}\n\n"
                     f"⚠️ Сигнал информационный. Источник котировок может отличаться от цены твоего брокера.")
                send(msg); state[symbol]=key; STATE_FILE.write_text(json.dumps(state,ensure_ascii=False,indent=2))
                print(f'{symbol}: {sig["side"]} sent')
            except Exception as e:
                print(f'{symbol}: ERROR: {e}')
            time.sleep(0.25)
        time.sleep(max(5, INTERVAL-(time.time()-started)))

if __name__=='__main__': run()
