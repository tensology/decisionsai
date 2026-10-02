# Tom Camp Gold Archive Audit

Date: 2026-08-13  
Status: research only, no strategy promoted

## Outcome

The Tom Camp archive contains a coherent discretionary process, but none of the three deterministic gold translations passed the required chronological and cost-aware gates. The material is useful as a Council evidence checklist and a set of WAIT conditions. It is not evidence for enabling automatic execution.

## Local Source Inventory

The source archive remains on the `EGO DEATH` drive and is not copied into Git or production.

- YouTube: 76 videos and 76 library records under `SCRAPE/TRADING/Youtube/tom-camp---professional-day-trader`.
- Instagram: 1,035 video files, 955 plain-text transcript files, 985 visual-OCR notes, and 986 indexed library records under `SCRAPE/TRADING/Instagram/tomcampcoaching`. The difference between media and library records is retained source material that has not yet been normalized into the human-readable library index.
- The updated local strategy index found 1,281 mentions across the selected Tom archive. Dominant concepts were risk management, fair-value gaps, session timing, order-block retests, market structure, liquidity targets, liquidity sweeps, and premium/discount location.
- No Telegram-named directory or Telegram export file was found anywhere on the mounted drive. Tom-related Telegram material may instead be in the production Telegram database and was not treated as part of this archive audit.

## Extended Local YouTube Pass

Five strategy-heavy YouTube videos that had media but no transcript were transcribed locally with MLX Whisper. This did not use an OpenAI API key, and the generated transcripts remain in the ignored local `data/research_cache/tomcamp` directory.

- Full Breakdown of Gold (2023-01-16)
- Simple Price Action Lecture: 4 Hour Power of Three (2025-05-17)
- Power of Three Masterclass (2026-03-04)
- The Entry Method That Changed Everything (2026-04-08)
- The Easiest Way To Determine Daily Bias (2026-07-10)

The longer lessons confirm that the lower-timeframe entry is not a standalone signal. It follows a higher-timeframe target, a session-aligned sweep or manipulation candle, and confirmation after the sweep. One-minute inverse fair-value gaps are described as optional entry refinement, not as independent edge.

## Repeated Rule Sequence

The most consistently described workflow is:

1. Establish higher-timeframe direction.
2. Name a higher-timeframe liquidity target.
3. Wait for price to reach a higher-timeframe area of interest or valid premium/discount location.
4. Observe a London or New York session liquidity sweep.
5. Require lower-timeframe structure shift and displacement.
6. Enter only on a causal retest.
7. Place invalidation beyond the sweep and target the named liquidity pool.

## Explicit-rule rescan on 2026-08-14

The 955 normalized Instagram transcripts were rescanned directly from the mounted `EGO DEATH` drive. The read-only scan found 298 transcripts mentioning liquidity or sweeps, 140 mentioning a trading session, 69 mentioning candle confirmation, 66 mentioning a stop loss, and 22 distinct transcripts containing entry, stop, and target language together.

The strongest codable families were:

- 10:00 New York H4 sweep and engulf, already covered by the H4 validation below.
- Session liquidity sweep, structure shift, and retest, already covered by the M5 sequence validation below.
- A liquidity sweep followed by a break of structure and a 71 percent OTE retrace, stop beyond the sweep, and target at the opposite range edge.
- A prior-day high or low sweep followed by an H1 reversal toward the opposite prior-day boundary. A frozen deterministic abstraction of this discretionary imbalance-based rule has now been tested separately and rejected before holdout.

Marketing claims and hindsight examples were not treated as evidence. The media and transcripts remain on the external drive and were not copied into Git or production.

This is materially stricter than treating a single wick or liquidity sweep as a trade.

## Gold-Specific Veto

In `Why I don't trade Gold` (2024-08-14), Tom explains that he avoided buying gold while it was at all-time highs and in premium because there was no clean upside liquidity target and therefore no acceptable reward/risk. The durable rule is not "never trade gold." It is:

- WAIT when the directional thesis lacks a clear target.
- WAIT when the proposed entry is poorly located within the relevant range.
- WAIT when the remaining reward does not justify the structural invalidation after costs.

## Deterministic Tests

### M5 Session-Liquidity Sequence

Script: `scripts/backtest_tomcamp_gold_sequence.py`

- Data: Dukascopy XAUUSD M5 bid/ask aggregation.
- Development: 2024 quarterly.
- Validation: 2025 H1.
- Untouched holdout preserved: 2025 H2 onward.
- Search: 960 bounded interpretations across prior-day bias, intraday trend, completed daily/H4 alignment, confirmation, structure lookback, displacement, retest, premium/discount, reward/risk, and maximum hold.
- Result: zero finalists. No candidate passed all development quarters and validation.

The added daily/H4 alignment rule did not recover an edge. Its best variant generated only three development trades, lost $0.78 at 0.01 lot, and generated no 2025 H1 validation trade. Requiring daily, H4, and intraday alignment generated no trades in any tested fold. This supports using timeframe conflict as a Council WAIT veto, but not presenting alignment as positive probability evidence.

### 10:00 New York H4 Manipulation Engulf

Script: `scripts/validate_tomcamp_h4_gold.py`

- Data: 1,556,926 Dukascopy XAUUSD M5 candles resampled into 33,266 New York-aligned H4 candles.
- Period: 2005 through 2026.
- Costs: at least 35 pips spread plus 5 pips slippage, at 0.01 lot.
- Best tested version: 2R target, 12 H4 bars maximum hold, no premium/discount filter, no chase filter.
- 2005-2014: 486 trades, profit factor 0.947, expectancy -$0.262 per trade.
- 2015-2020: 275 trades, profit factor 0.918, expectancy -$0.388 per trade.
- 2021-2026: 288 trades, profit factor 1.039, expectancy +$0.373 per trade.
- Result: failed. Recent performance was too weak and did not survive earlier regimes.

### Daily Continuation Bias

Script: `scripts/validate_tomcamp_daily_bias_gold.py`

- Data: 1,556,926 Dukascopy XAUUSD M5 bid/ask candles from 2005 through July 2026.
- Search: 24 bounded interpretations across UTC or New York 17:00 daily boundaries, three to five same-direction candles, 1.5R or 2R, and two- or five-day maximum holds.
- Development: 2005 through 2014. Validation: 2015 through 2020. The 2021 onward holdout remained unopened because no candidate qualified.
- Best balanced version: New York 17:00 boundary, five-candle streak, 2R, five-day hold.
- Development: 79 trades, profit factor 1.007, expectancy +$0.044 per trade at 0.01 lot, maximum drawdown $155.50.
- Validation: 54 trades, profit factor 1.775, expectancy +$4.012 per trade.
- Result: failed before holdout. The large regime difference and development profit factor near 1.0 do not establish a repeatable edge.

### OTE Sweep And Retest

Script: `scripts/validate_tomcamp_ote_gold.py`

- Data: Dukascopy XAUUSD M5 bid/ask aggregation.
- Development: four independent 2024 quarters.
- Validation: 2025 H1.
- Untouched holdout preserved: 2025 H2 onward.
- Search: 256 bounded interpretations across side, active session, liquidity lookback, structure lookback, shift timing, retest timing, and maximum hold. The transcript-derived OTE level remained fixed at 71 percent.
- Costs: at least 35 pips spread plus 5 pips slippage, at 0.01 lot.
- Result: zero finalists. The strongest short-overlap variant was positive in three 2024 quarters but failed 2024 Q3 with PF 0.587 and failed 2025 H1 with 27 trades, PF 0.558, and expectancy of -$1.22 per trade.

The final holdout was not opened. OTE location can remain a Council context item, but this result does not establish executable gold edge.

### Prior-Day Boundary Sweep And H1 Reversal

Script: `scripts/validate_tomcamp_prior_day_sweep_h1.py`

- Data: 283,651 Dukascopy XAUUSD M5 bid/ask candles aggregated causally into 23,624 complete H1 candles.
- Frozen interpretation: sweep exactly one previous-day boundary during London or New York, close back inside with a directional H1 body, enter at the next H1 open, stop beyond the sweep, and target the opposite prior-day boundary.
- Development: six chronological half-years from 2021 through 2023.
- Validation: both 2024 half-years.
- Untouched holdout preserved: 2025 onward.
- Result: failed. Observed-cost validation PF was 0.638 and 0.701 with negative expectancy in both halves. Stress PF was 0.534 and 0.645, also with negative expectancy.

This is a measured rejection of one pre-specified automatic translation. The pattern may remain descriptive Council context, but it is not positive edge and cannot authorize an order.

## Application

The Council may use the sequence to compress market evidence and reject incomplete proposals. It must not cite the creator or the pattern as probability evidence. Any trade still requires fresh broker telemetry, a deterministic promoted strategy, calibrated probabilities, and account-level execution eligibility.

The source archive also contains marketing language and hindsight examples. Those are deliberately excluded from the evidence pack.
