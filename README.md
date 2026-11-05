# Trade Vision 24/7 — Liquidity Signal Bot

Railway-ready Python scanner for Bybit perpetual markets.

## Logic v1

The bot uses:

- 4H context
- 15M confirmation
- PDH / PDL
- PWH / PWL
- PMH / PML
- previous swing high / low
- Asia / London / New York ranges
- liquidity sweep detection
- WaveTrend 9 / 12 / 3
- extreme level ±60
- Money Flow 21 / 9
- 15M structure shift
- dynamic Entry / SL / TP levels
- Telegram signal score

The WaveTrend and Money Flow formulas are ported from the supplied Liquidity Tracker Pine logic.

## Railway

1. Push this folder to GitHub.
2. Create a new Railway project from the GitHub repo.
3. Add Variables:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
WATCHED_SYMBOLS=BTCUSDT,ETHUSDT,HYPEUSDT,FARTCOINUSDT,ZAMAUSDT
MIN_SIGNAL_SCORE=70
SCAN_INTERVAL_SECONDS=60
```

4. Railway should detect the Procfile and run:

```bash
python main.py
```

## Important

Version 1 is **signal-only**. It does not place trades.

That is intentional: first compare Telegram calls against manual chart analysis and tune the score/entry logic before enabling execution.

## Recommended next upgrades

- exact equal-high / equal-low engine from the Pine script
- explicit sweep vs liquidity-run state machine
- bullish/bearish divergence engine
- half-timeframe Money Flow
- FVG detection and 50% FVG entries
- persistent database instead of JSON state
- chart snapshot attached to Telegram
- optional Bybit execution module after validation

## Trend Trader M5 (multi-asset signal-only mode)

The Railway `Procfile` runs `trend_trader_main.py`. This mode scans a fixed 10-symbol universe:

- BTCUSDT, ETHUSDT, SOLUSDT — Bybit linear perpetual market candles
- XAUUSD — Yahoo Finance `GC=F` (COMEX gold futures proxy)
- XAGUSD — Yahoo Finance `SI=F` (COMEX silver futures proxy)
- USOIL — Yahoo Finance `CL=F` (WTI crude futures proxy)
- US500 — Yahoo Finance `ES=F` (E-mini S&P 500 futures proxy)
- EURUSD, GBPUSD, USDJPY — Yahoo Finance FX reference candles

Signal logic follows the supplied Smart Signal Pro v2 Trend Trader preset: ATR period 10, sensitivity 10, closed M5 candle crossover. Levels: TP1 0.5%, TP2 1.0%, TP3 1.5%, SL 1.0%; TP4 is removed. This mode only sends Telegram alerts and does not place orders.

Railway Variables required: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Optional: `SCAN_INTERVAL_SECONDS` (default 60).

**Data-source limitation:** Yahoo Finance futures/FX symbols are reference-data proxies, not guaranteed to match a broker's XAUUSD/XAGUSD/USOIL/US500/FX CFD feed. Validate prices and symbol sessions against the broker before using alerts for live decisions. Crypto is sourced from Bybit, not BingX. The previous liquidity-engine modules remain in the repository but are not used by this Trend Trader M5 entry point.
