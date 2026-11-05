"""Smart Signal Pro v2 Trend Trader preset, evaluated on CLOSED M5 candles."""
import pandas as pd

ATR_LENGTH = 10
SENSITIVITY = 10.0  # Smart Signal Pro v2: Trend Trader preset
TP_PCTS = (0.5, 1.0, 1.5)
SL_PCT = 1.0

def _atr(df: pd.DataFrame) -> pd.Series:
    prev = df.close.shift(1)
    tr = pd.concat([(df.high-df.low), (df.high-prev).abs(), (df.low-prev).abs()], axis=1).max(axis=1)
    return tr.rolling(ATR_LENGTH, min_periods=ATR_LENGTH).mean()

def get_signal(df: pd.DataFrame):
    if df is None or len(df) < 40: return None
    # Ignore the still-forming candle. The last row used below is fully closed.
    d = df.iloc[:-1].copy().reset_index(drop=True)
    atr = _atr(d)
    loss = SENSITIVITY * atr
    trail = [float('nan')] * len(d)
    for i in range(len(d)):
        if pd.isna(loss.iloc[i]): continue
        src = float(d.close.iloc[i]); prev_src = float(d.close.iloc[i-1]) if i else src
        prev_trail = trail[i-1] if i else float('nan')
        if pd.isna(prev_trail): trail[i] = src - loss.iloc[i]
        elif src > prev_trail and prev_src > prev_trail: trail[i] = max(prev_trail, src-loss.iloc[i])
        elif src < prev_trail and prev_src < prev_trail: trail[i] = min(prev_trail, src+loss.iloc[i])
        elif src > prev_trail: trail[i] = src-loss.iloc[i]
        else: trail[i] = src+loss.iloc[i]
    d['trail'] = trail
    if len(d) < 3 or pd.isna(d.trail.iloc[-2]) or pd.isna(d.trail.iloc[-1]): return None
    prev_close, close = float(d.close.iloc[-2]), float(d.close.iloc[-1])
    prev_trail, curr_trail = float(d.trail.iloc[-2]), float(d.trail.iloc[-1])
    side = 'BUY' if prev_close <= prev_trail and close > curr_trail else 'SELL' if prev_close >= prev_trail and close < curr_trail else None
    if not side: return None
    entry = close
    sign = 1 if side == 'BUY' else -1
    return {
        'side': side, 'entry': entry, 'sl': entry * (1 - sign*SL_PCT/100),
        'tps': [entry * (1 + sign*p/100) for p in TP_PCTS],
        'candle_time': d.time.iloc[-1].isoformat(), 'trail': curr_trail,
    }
