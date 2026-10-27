# Liquidity Hunter FX — H4/H1 Sweep / M5 CHoCH / HTF SL-TP

Telegram alert bot deployed as a Railway worker. It does not place orders.

## Signal logic
1. Detects confirmed sweeps on **both H4 and H1**. H1 checks the preceding 24 closed H1 candles; H4 checks the preceding 20 closed H4 candles.
2. A sweep is confirmed only when the candle trades beyond the prior range and closes back inside. The H4 detector currently checks the latest fully closed H4 candle because M5 history is limited by the current data feed.
3. Waits for a **closed M5 candle** to break a confirmed local structure pivot in the sweep direction (CHoCH). M5 is the entry-confirmation timeframe for both H1 and H4 sweeps.
4. If an H1 sweep occurs inside the same H4 sweep candle in the same direction, the alert is labeled **H4 + H1** and requires M5 confirmation after H4 closes.
5. Entry is the close of the M5 confirmation candle.
6. SL is calculated from the selected sweep wick extreme plus a small volatility-based buffer; M5 swings never define the stop.
7. TP is selected from confirmed H1/H4 liquidity (with daily levels as context); M5 swings never define the target.
8. SL buffer = volatility buffer + configurable per-symbol spread reserve. Spread reserve is added beyond the selected H1 or H4 sweep wick, and RR is recalculated after the adjustment.
9. The minimum 1R filter remains. HTF bias is contextual: countertrend signals are allowed only after the selected H1/H4 Sweep and M5 CHoCH confirm the chosen DOL direction.

## Setup / Railway
Required variables:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- Optional: `SCAN_INTERVAL_SECONDS` (defaults to 60)

Install requirements and run `python main.py`, or deploy as a Railway worker using the existing `Procfile`.

## Important limitations
- Market data still comes from BingX for crypto and Yahoo Finance for other instruments. These feeds do not provide the live FxPro bid/ask spread used by this bot; spread reserves are configurable estimates, not live broker spread measurements. Tune them to your FxPro account and symbol specifications. These quotes may differ from the broker's CFD prices; especially note that `XAUT-USD` is not the same instrument as a broker's `XAUUSD` CFD.
- Yahoo Finance 4H bars are aggregated from 1H bars and may not align perfectly with broker session boundaries.
- The HTF bias is intentionally a simple directional filter, not a complete discretionary ICT/SMC model. H4 and H1 signals are not proof of profitability; compare their outcomes separately in alert logs.
- TP selection is algorithmic: the nearest qualifying pivot/PDH/PDL can be a weak liquidity level. Validate chart-by-chart before using live.
- Tracker still evaluates outcomes from fetched H1 highs/lows, so if SL and TP are both touched within the same H1 candle, intrabar order cannot be known from these bars. Such outcomes should be reviewed manually.
- The algorithm is not backtested or guaranteed profitable. Start with alerts only and verify signals against the exact broker chart.
- Never commit `.env` or API tokens to GitHub.
