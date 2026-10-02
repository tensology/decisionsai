# Socrates GOLD Scalping Research

Generated from additional Socrates Investments transcripts pulled on 2026-08-02.

## Why The Current Return Is Weak

The current Socrates backtest is profitable on GOLD#, but it is not actually scalping in the way the videos describe. It mostly tests H1 candles. The Socrates method is lower-timeframe execution around higher-timeframe key levels:

- Mark major pivots on H4 and higher.
- Treat daily high, daily low, daily open, prior week levels, prior month levels, London open/close, New York open/close, Asian high/low, and prior pivot zones as entry/exit candidates.
- Use M1 or M5 to take entries and exits.
- Require volume or buying/selling strength to confirm the move at the level.
- Buy low at demand/pivot support and sell high at supply/pivot resistance.
- Target the next pivot or key level rather than a fixed arbitrary take-profit.
- Use gold-specific context, especially USD strength/weakness and major news sentiment.

## New Transcript Evidence

### Strategy Breakdown

Video: `K3Wh8K1mRY8` - What is My Trading Strategy

- Higher timeframes identify pivots and breaks.
- Daily high, daily low, London close, and New York close are treated as key levels.
- Entries and exits happen on any timeframe, specifically mentioning weekly, daily, H4, H1, and minute charts.
- The method is "key levels and volume".
- For gold, dollar strength is part of directional context.

### Four-Hour Pivot Mapping

Video: `NhKmof9njW4` - Charting pivots on a 4 hour timeframe

- H4 is used to mark major pivots.
- Major pivots are treated as key levels.
- The lower-timeframe entry is taken after price reaches a marked level.
- Volume confirmation is used at the pivot.

### Entry And Exit Pivots

Video: `dJ-yCL41P3U` - How I See Pivots For Entry's & Exits

- Pivots drive both entries and exits.
- Prior week lows and prior pivot areas are used for entry.
- Exits are based on the next pivot/key level.
- The method emphasizes key levels, supply and demand, and volume.

### Live Trading Recap

Video: `kUHNBJOJZvc`

- Wait for a break of a key level.
- Daily highs/lows, previous levels, monthly lows, Asian high, and London low are all possible entry or exit areas.
- The core is "chasing pivots" only after price hits a key level with volume.

### Gold Short Explanation

Video: `upTY-yul0pI`

- Gold shorts were supported by macro/news bias and USD/economy strength.
- Prior month levels and previous pivots were used for targets.
- The trade was not just a chart pattern, but chart level plus macro bias.

### Methodology Entry And Exit

Video: `wMEH2Q37j-4`

- Indicators mentioned: key levels, ICT kill zones, supply/demand, buying/selling strength.
- Gold is the main instrument.
- Price around liquidity/key levels is the decision area.
- The method waits for pivots, daily high retests, breaks, and buying/selling strength.

## Deterministic Scalping Model

The next strategy should be implemented as `socrates_pivot_scalp_v2`.

### Setup Context

- Build H4 pivot map from swing highs/lows.
- Add daily open, daily high, daily low, weekly high/low, monthly high/low.
- Add Asian high/low, London open/close, New York open/close.
- Add previous day high/low and previous week high/low.
- Add DXY or USD proxy bias for GOLD#.

### Entry Timeframe

- M5 first.
- M1 later, only after M5 is stable and broker data is available.

### Long Entry

- Price reaches a demand/pivot/key level.
- Price sweeps below or taps the level and closes back above it on M5.
- Lower wick or displacement candle confirms rejection.
- Buying volume/strength exceeds recent baseline.
- H4/D1 bias is not strongly bearish.
- Entry only during London, New York, or London/New York overlap.

### Short Entry

- Price reaches a supply/pivot/key level.
- Price sweeps above or taps the level and closes back below it on M5.
- Upper wick or displacement candle confirms rejection.
- Selling volume/strength exceeds recent baseline.
- H4/D1 bias is not strongly bullish.
- Entry only during London, New York, or London/New York overlap.

### Exit

- First target at nearest opposite pivot/key level.
- Optional runner only after partial profit and break-even stop.
- Stop beyond the sweep wick or zone boundary.
- Do not use a fixed target if the next pivot is closer.

### Promotion Gate

- Backtest with broker M5 data, not Yahoo H1 data.
- Require at least 100 trades across rolling windows.
- Require profit factor >= 1.5.
- Require max drawdown <= 25%.
- Require out-of-sample profitability.
- Require spread and slippage modeling.
- Keep alerts-only until broker forward testing confirms the backtest.

## Immediate Engineering Implication

To make this scalp, the project needs M5 broker-history ingestion from MT4/XM or another reliable provider. Without that, the simulator is pretending to scalp using H1 candles, which explains why the result feels dismal and structurally wrong.
