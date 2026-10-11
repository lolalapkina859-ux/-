# Liquidity Hunter FX — Twelve Data edition

A small Telegram alert bot for BTCUSD, XAUUSD and EURUSD. It uses Twelve Data REST time-series candles and a simple H1 liquidity-sweep + M5 structure heuristic. It never places trades.

## Setup
1. Get an API key at https://twelvedata.com/
2. Create a Telegram bot with @BotFather and get your chat ID.
3. Copy `.env.example` to `.env` and fill the three keys.
4. Install and run:
   ```bash
   pip install -r requirements.txt
   python main.py
   ```
5. Deploy the repository to Railway as a worker. Add the same variables under Railway Variables.

## Important limitations
- Twelve Data candles are not guaranteed to match FxPro CFD candles exactly. Compare the same timestamp and timeframe in FxPro before relying on alerts.
- Check whether your Twelve Data plan supports intraday candles and the requested `BTC/USD`, `XAU/USD`, `EUR/USD` symbols. If a symbol is unavailable, change `TD_SYMBOL_*` to the exact supported symbol shown by the provider.
- The algorithm is an unvalidated heuristic, not a proven strategy. It does not calculate a risk-sized entry, stop loss, news filter or trade execution.
- Start with alerts only and compare signals against FxPro demo/live charts before using them for decisions.
- Never commit `.env` or API tokens to GitHub.
