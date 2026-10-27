# Liquidity Hunter FX — H1 Sweep / M5 CHoCH / HTF SL-TP

Telegram alert bot deployed as a Railway worker. It does not place orders.

## Signal logic
1. Uses a **closed H1 candle** to detect a true liquidity sweep: price trades beyond the prior 24-candle H1 high/low and closes back inside the level.
2. Waits for a **closed M5 candle** to break a confirmed local structure pivot in the DOL direction (CHoCH). M5 is only an early-entry confirmation timeframe.
3. Entry is the close of that M5 confirmation candle.
4. SL is calculated only from the actual H1 sweep wick extreme plus a small volatility-based buffer; M5 swings never define the stop.
5. TP is selected from confirmed H1/H4 liquidity (with daily levels as context); M5 swings never define the target.
6. SL buffer = volatility buffer + configurable per-symbol spread reserve. Spread reserve is added beyond the H1 sweep wick, and RR is recalculated after the adjustment.
6. The minimum 1R filter remains. HTF bias is contextual: countertrend signals are allowed only after the H1 Sweep and M5 CHoCH confirm the chosen DOL direction.

## Setup / Railway
Required variables:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- Optional: `SCAN_INTERVAL_SECONDS` (defaults to 60)

Install requirements and run `python main.py`, or deploy as a Railway worker using the existing `Procfile`.

## Important limitations
- Market data still comes from BingX for crypto and Yahoo Finance for other instruments. These feeds do not provide the live FxPro bid/ask spread used by this bot; spread reserves are configurable estimates, not live broker spread measurements. Tune them to your FxPro account and symbol specifications. These quotes may differ from the broker's CFD prices; especially note that `XAUT-USD` is not the same instrument as a broker's `XAUUSD` CFD.
- Yahoo Finance 4H bars are aggregated from 1H bars and may not align perfectly with broker session boundaries.
- The HTF bias is intentionally a simple directional filter, not a complete discretionary ICT/SMC model.
- TP selection is algorithmic: the nearest qualifying pivot/PDH/PDL can be a weak liquidity level. Validate chart-by-chart before using live.
- Tracker still evaluates outcomes from fetched H1 highs/lows, so if SL and TP are both touched within the same H1 candle, intrabar order cannot be known from these bars. Such outcomes should be reviewed manually.
- The algorithm is not backtested or guaranteed profitable. Start with alerts only and verify signals against the exact broker chart.
- Never commit `.env` or API tokens to GitHub.
