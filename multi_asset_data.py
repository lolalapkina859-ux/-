"""M5 market-data adapters: Bybit spot/perpetual crypto and Yahoo Finance CFDs/futures proxies."""
from __future__ import annotations
import time
import requests
import pandas as pd
import yfinance as yf

BYBIT_URL = "https://api.bybit.com/v5/market/kline"

# The Yahoo tickers are market-data proxies, not guaranteed broker CFD symbols.
INSTRUMENTS = {
    "BTCUSDT": ("bybit", "BTCUSDT"),
    "XAUUSD": ("yahoo", "GC=F"),       # COMEX gold futures proxy
}

def _bybit(symbol: str, limit: int = 300) -> pd.DataFrame:
    r = requests.get(BYBIT_URL, params={"category":"linear", "symbol":symbol, "interval":"5", "limit":limit}, timeout=20)
    r.raise_for_status(); payload = r.json()
    if payload.get("retCode") != 0:
        raise RuntimeError(f"Bybit {symbol}: {payload.get('retMsg')}")
    rows = payload.get("result", {}).get("list", [])
    if not rows: raise RuntimeError(f"Bybit returned no candles for {symbol}")
    df = pd.DataFrame(rows, columns=["time","open","high","low","close","volume","turnover"])
    df["time"] = pd.to_datetime(pd.to_numeric(df["time"]), unit="ms", utc=True)
    for c in ("open","high","low","close","volume"): df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna().sort_values("time").drop_duplicates("time").reset_index(drop=True)

def _yahoo(ticker: str) -> pd.DataFrame:
    raw = yf.download(ticker, period="5d", interval="5m", progress=False, auto_adjust=False, threads=False)
    if raw is None or raw.empty: raise RuntimeError(f"Yahoo Finance returned no M5 data for {ticker}")
    if isinstance(raw.columns, pd.MultiIndex): raw.columns = raw.columns.get_level_values(0)
    raw = raw.rename(columns={"Open":"open","High":"high","Low":"low","Close":"close","Volume":"volume"})
    raw = raw.reset_index()
    time_col = "Datetime" if "Datetime" in raw.columns else "Date"
    raw["time"] = pd.to_datetime(raw[time_col], utc=True)
    for c in ("open","high","low","close"):
        raw[c] = pd.to_numeric(raw[c], errors="coerce")
    if "volume" not in raw: raw["volume"] = 0.0
    raw["volume"] = pd.to_numeric(raw["volume"], errors="coerce").fillna(0)
    return raw[["time","open","high","low","close","volume"]].dropna().sort_values("time").drop_duplicates("time").reset_index(drop=True)

def get_m5(symbol: str) -> pd.DataFrame:
    if symbol not in INSTRUMENTS: raise ValueError(f"Unsupported instrument: {symbol}")
    provider, ticker = INSTRUMENTS[symbol]
    return _bybit(ticker) if provider == "bybit" else _yahoo(ticker)
