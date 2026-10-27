import warnings
warnings.simplefilter('ignore', FutureWarning)
warnings.filterwarnings('ignore', category=UserWarning, module='yfinance')

import os, time, logging, requests
from datetime import datetime, timezone
from dotenv import load_dotenv
import yfinance as yf
from scanner import detect_sweep, format_signal
from tracker import init_db, save_signal, check_open_trades

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('liquidity-hunter')
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '').strip()
SCAN_SECONDS = int(os.getenv('SCAN_INTERVAL_SECONDS', '60'))
MAX_ENTRY_DRIFT_ATR = float(os.getenv('MAX_ENTRY_DRIFT_ATR', '0.75'))  # scanner also applies a guard

SPREAD_BUFFER_DEFAULTS = {
    'BTC_USDT': 10.0, 'ETH_USDT': 1.0, 'SOL_USDT': 0.10,
    'XAU_USD': 0.50, 'XAG_USD': 0.03, 'USOIL_USD': 0.05,
    'US500_USD': 0.50, 'EUR_USD': 0.00015, 'GBP_USD': 0.00015, 'USD_JPY': 0.015,
}
SYMBOLS = {
    'DXY': {'source':'yfinance', 'ticker':'DX-Y.NYB'},
    'BTC / USDT': {'source':'bingx', 'ticker':'BTC-USDT'},
    'ETH / USDT': {'source':'bingx', 'ticker':'ETH-USDT'},
    'SOL / USDT': {'source':'bingx', 'ticker':'SOL-USDT'},
    # Yahoo XAUUSD=X is a spot-gold reference quote, not a guaranteed FxPro quote.
    'XAU / USD': {"source": "yfinance", "ticker": "XAUT-USD"},
    'XAG / USD': {"source": "yfinance", "ticker": "SI=F"},
    'USOIL / USD': {'source':'yfinance', 'ticker':'CL=F'},
    'US500 / USD': {'source':'yfinance', 'ticker':'SPY'},
    'EUR / USD': {'source':'yfinance', 'ticker':'EURUSD=X'},
    'GBP / USD': {'source':'yfinance', 'ticker':'GBPUSD=X'},
    'USD / JPY': {'source':'yfinance', 'ticker':'USDJPY=X'},
}
last_alerts = set()
symbol_cooldown = {}
COOLDOWN_SECONDS = 1800


def spread_buffer(symbol):
    key = symbol.replace(' / ', '_').replace(' ', '_').upper()
    raw = os.getenv(f'SPREAD_BUFFER_{key}')
    value = SPREAD_BUFFER_DEFAULTS.get(key, 0.0) if raw is None else float(raw)
    if value < 0:
        raise ValueError(f'SPREAD_BUFFER_{key} must be non-negative')
    return value


def telegram(message):
    if not TOKEN or not CHAT_ID:
        log.error('Telegram credentials are missing')
        return False
    try:
        r = requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',
                          json={'chat_id':CHAT_ID, 'text':message, 'parse_mode':'Markdown'}, timeout=15)
        r.raise_for_status()
        return True
    except Exception:
        log.exception('Telegram delivery failed')
        return False


def fetch_bingx_bars(symbol, interval):
    url = 'https://open-api.bingx.com/openApi/swap/v2/quote/klines'
    r = requests.get(url, params={'symbol':symbol, 'interval':interval, 'limit':200}, timeout=15)
    r.raise_for_status(); data = r.json()
    if data.get('code') != 0 or not data.get('data'):
        raise RuntimeError(f'BingX API error for {symbol}: {data}')
    bars = [{'timestamp':int(x['time']/1000), 'open':float(x['open']), 'high':float(x['high']),
             'low':float(x['low']), 'close':float(x['close'])} for x in data['data']]
    return sorted(bars, key=lambda x:x['timestamp'])


def fetch_yfinance_bars(ticker, interval):
    # yfinance generally does not support native 4h; request 1h and aggregate by UTC 4h boundaries.
    yf_interval = {'1d':'1d', '4h':'1h', '1h':'1h', '15m':'15m', '5m':'5m'}[interval]
    period = {'1d':'350d', '4h':'60d', '1h':'60d', '15m':'60d', '5m':'7d'}[interval]
    df = yf.download(ticker, period=period, interval=yf_interval, progress=False, auto_adjust=False)
    if df.empty:
        raise RuntimeError(f'Empty data from yfinance for {ticker} ({interval})')
    if hasattr(df.columns, 'levels') and len(df.columns.levels) > 1:
        df.columns = df.columns.get_level_values(0)
    bars=[]
    for idx,row in df.iterrows():
        bars.append({'timestamp':int(idx.timestamp()), 'open':float(row['Open']), 'high':float(row['High']),
                     'low':float(row['Low']), 'close':float(row['Close'])})
    bars.sort(key=lambda b:b['timestamp'])
    if interval != '4h':
        return bars
    # Aggregate only complete UTC 4-hour groups. Never mix partial groups into HTF calculations.
    groups={}
    for b in bars:
        bucket=(b['timestamp']//14400)*14400
        groups.setdefault(bucket, []).append(b)
    out=[]
    for bucket, group in sorted(groups.items()):
        group=sorted(group,key=lambda x:x['timestamp'])
        if len(group) != 4:
            continue
        out.append({'timestamp':bucket, 'open':group[0]['open'], 'high':max(x['high'] for x in group),
                    'low':min(x['low'] for x in group), 'close':group[-1]['close']})
    return out


def fetch_bars(info, interval):
    return fetch_bingx_bars(info['ticker'], interval) if info['source']=='bingx' else fetch_yfinance_bars(info['ticker'], interval)


def get_current_quote(info):
    """Fresh indicative quote. It is not a substitute for FxPro Bid/Ask."""
    if info['source']=='bingx':
        url='https://open-api.bingx.com/openApi/swap/v2/quote/price'
        r=requests.get(url, params={'symbol':info['ticker']}, timeout=10); r.raise_for_status()
        data=r.json()
        if data.get('code') != 0 or not data.get('data'):
            raise RuntimeError(f'BingX quote error: {data}')
        return float(data['data']['price'])
    t=yf.Ticker(info['ticker'])
    price=t.fast_info.get('last_price')
    if price is None:
        hist=t.history(period='1d', interval='1m')
        if hist.empty:
            raise RuntimeError(f'No fresh quote for {info["ticker"]}')
        price=float(hist['Close'].iloc[-1])
    return float(price)


def scan_once():
    now=time.time(); tracked_prices={}
    log.info('--- Scan cycle: closed H1 sweep / fresh M5 CHoCH / quote validation ---')
    for symbol, info in SYMBOLS.items():
        if symbol=='DXY':
            continue
        try:
            if now - symbol_cooldown.get(symbol, 0) < COOLDOWN_SECONDS:
                # Cooldown blocks new alerts, not tracking. Use M5 bars for outcome checks.
                m5_track=fetch_bars(info,'5m')
                now_ts=time.time()
                closed_track=[b for b in m5_track if b['timestamp']+300 <= now_ts]
                if closed_track:
                    b=closed_track[-1]
                    tracked_prices[symbol]={'high':b['high'],'low':b['low'],'close':b['close'],'bar_timestamp':b['timestamp']}
                continue
            d1=fetch_bars(info,'1d'); time.sleep(.1)
            h4=fetch_bars(info,'4h'); time.sleep(.1)
            h1=fetch_bars(info,'1h'); time.sleep(.1)
            m5=fetch_bars(info,'5m')
            quote=get_current_quote(info)
            if h1:
                tracked_prices[symbol]={'high':h1[-1]['high'],'low':h1[-1]['low'],'close':h1[-1]['close'],'bar_timestamp':h1[-1]['timestamp']}
            # For trade tracking, use the latest completed M5 candle, not an H1 candle
            # whose range may include price action from before the signal was sent.
            now_ts=time.time()
            closed_m5=[b for b in m5 if b['timestamp']+300 <= now_ts]
            if closed_m5:
                b=closed_m5[-1]
                tracked_prices[symbol]={'high':b['high'],'low':b['low'],'close':b['close'],'bar_timestamp':b['timestamp']}
            event=detect_sweep(d1,h4,h1,symbol,current_price=quote,m5_bars=m5,spread_buffer=spread_buffer(symbol))
            if not event:
                log.info('%s: no fresh valid setup (quote %.8f)', symbol, quote)
                continue
            key=f"{symbol}:{event['direction']}:{event['sweep_time']}:{event['bar_time']}"
            if key in last_alerts:
                continue
            # Recheck quote against entry/SL/TP immediately before delivery.
            direction=event['direction']; entry=event['entry']; sl=event['stop_loss']; tp=event['target_1']
            if direction=='LONG' and (quote<=entry or quote<=sl or quote>=tp):
                log.warning('%s: rejected at final quote %.8f (LONG entry/SL/TP %.8f/%.8f/%.8f)',symbol,quote,entry,sl,tp); continue
            if direction=='SHORT' and (quote>=entry or quote>=sl or quote<=tp):
                log.warning('%s: rejected at final quote %.8f (SHORT entry/SL/TP %.8f/%.8f/%.8f)',symbol,quote,entry,sl,tp); continue
            msg=format_signal(symbol,event)
            if not telegram(msg):
                continue
            save_signal(symbol,event['tf'],direction,entry,sl,tp,event['rr_ratio'],
                        signal_timestamp=event.get('signal_timestamp'), note='Indicative candle-based signal; not a real execution')
            last_alerts.add(key); symbol_cooldown[symbol]=now
            log.info('%s %s signal sent entry=%.8f SL=%.8f TP=%.8f quote=%.8f RR=%.2f',symbol,direction,entry,sl,tp,quote,event['rr_ratio'])
        except Exception as exc:
            log.exception('%s scan failed: %s',symbol,exc)
        time.sleep(.15)
    if tracked_prices:
        check_open_trades(tracked_prices,telegram)
    log.info('--- Scan cycle finished ---')


def main():
    if not TOKEN or not CHAT_ID:
        raise SystemExit('Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID')
    init_db()
    log.info('Database initialized')
    while True:
        scan_once()
        time.sleep(SCAN_SECONDS)

if __name__=='__main__':
    main()
