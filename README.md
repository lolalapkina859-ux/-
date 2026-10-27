# Liquidity Hunter FX — corrected starter package

## Files
- `main.py`: scan loop, market data adapters, fresh-quote gate, Telegram alerts.
- `scanner.py`: closed-candle H1 sweep + recent M5 CHoCH, SL buffer, nearest HTF liquidity target and RR.
- `tracker.py`: SQLite tracking with expired and ambiguous outcomes separated from wins/losses.

## Required Railway variables
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- Optional `SCAN_INTERVAL_SECONDS` (default 60)
- Optional `SPREAD_BUFFER_XAU_USD`, `SPREAD_BUFFER_EUR_USD`, etc. Values are raw price units.

## Important limitations
1. This package does **not** connect to FxPro cTrader. Yahoo/BingX prices are indicative and may differ from the broker's Bid/Ask. For live trading decisions, add cTrader Open API and use its exact symbol candles and Bid/Ask quote.
2. Yahoo `XAUUSD=X`/`XAGUSD=X` availability depends on Yahoo's feed. `US500 / USD` remains a SPY proxy and `USOIL / USD` remains a futures proxy; do not treat these as exact broker symbols.
3. The tracker checks candle High/Low touches, not actual fills. If SL and TP are both within one candle, it marks the result `AMBIGUOUS` rather than guessing which came first.
4. Signals are alerts only. The bot does not execute trades.
5. Before deploying, test in a separate Railway service or branch and inspect logs. Keep your current deployment as a rollback copy.

## Python dependencies
`requests`, `python-dotenv`, `yfinance`, `pandas`

Run: `python main.py`
