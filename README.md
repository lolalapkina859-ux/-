# Liquidity Hunter FX — H1 Sweep / M15 CHoCH

Telegram alert bot deployed as a Railway worker. It does not place orders.

## Signal logic
1. Uses a **closed H1 candle** to detect a true liquidity sweep: price trades beyond the prior 24-candle H1 high/low and closes back inside the level.
2. Waits for a **closed M15 candle** to break a confirmed local structure pivot in the reversal direction (CHoCH).
3. Entry is the close of that M15 confirmation candle.
4. SL is beyond the actual H1 sweep wick with a small volatility-based safety buffer.
5. TP is the nearest eligible liquidity level in the trade direction, selected from confirmed H1/H4 swing pivots, previous daily high/low, or the sweep range boundary.
6. HTF filter rejects setups where the selected target offers less than 1R and reports the simplified H4 bias as context; reversal setups are not automatically rejected solely because they counter that bias.

## Setup / Railway
Required variables:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- Optional: `SCAN_INTERVAL_SECONDS` (defaults to 60)

Install requirements and run `python main.py`, or deploy as a Railway worker using the existing `Procfile`.

## Important limitations
- Market data still comes from BingX for crypto and Yahoo Finance for other instruments. These quotes may differ from the broker's CFD prices; especially note that `XAUT-USD` is not the same instrument as a broker's `XAUUSD` CFD.
- Yahoo Finance 4H bars are aggregated from 1H bars and may not align perfectly with broker session boundaries.
- The HTF bias is intentionally a simple directional filter, not a complete discretionary ICT/SMC model.
- TP selection is algorithmic: the nearest qualifying pivot/PDH/PDL can be a weak liquidity level. Validate chart-by-chart before using live.
- Tracker still evaluates outcomes from fetched H1 highs/lows, so if SL and TP are both touched within the same H1 candle, intrabar order cannot be known from these bars. Such outcomes should be reviewed manually.
- The algorithm is not backtested or guaranteed profitable. Start with alerts only and verify signals against the exact broker chart.
- Never commit `.env` or API tokens to GitHub.
