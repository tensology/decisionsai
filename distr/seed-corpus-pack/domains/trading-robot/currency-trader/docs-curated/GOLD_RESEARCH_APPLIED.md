# GOLD Research Applied

Date: 2026-08-02

This note records what was added after reviewing external gold research, the local `Sanos Setup.pdf`, and the current Socrates transcript-derived strategy memory.

## Evidence Converted Into Rules

External research and market references point to four practical constraints:

- Gold has meaningful intraday seasonality. London, New York, and their overlap should matter more than random off-session candles.
- Gold is highly macro-sensitive. CPI, NFP, FOMC, DXY, and rates can change the regime, so the bot should treat USD/news context as a filter before promotion to live execution.
- Liquidity and volume matter. CME emphasizes gold futures liquidity, open interest, volatility tools, and macro event sensitivity. The strategy should not rely only on EMA trend.
- Walk-forward testing matters. Strategy candidates should be selected from past windows and tested on unseen windows, not promoted because one backtest looked good.

The local `Sanos Setup.pdf` added a sharper discretionary setup:

- Wait for a 5-minute liquidity sweep.
- Require delivery from an inverse/FVG area.
- Require a meaningful FVG size, with the PDF using a 9 point minimum.
- Require obvious targets such as equal highs/lows, trendline liquidity, or intermediate-term highs/lows.
- Treat premium/discount and SMT as confidence boosters.

## Implemented Changes

The backtest engine now has two new deterministic strategy families:

- `socrates_volume_profile_pivot_long`
- `socrates_volume_profile_pivot_short`
- `sanos_ifvg_liquidity_sweep_long`
- `sanos_ifvg_liquidity_sweep_short`

The new helpers are:

- `rolling_volume_profile_levels`: builds a rolling point of control, value-area high, and value-area low from candle volume.
- `fair_value_gap`: detects a simplified three-candle fair value gap so iFVG-style setups can be tested.

Memory routing now maps `volume_profile`, `ifvg`, `fair_value_gap`, `liquidity_sweep`, and `m1_m5_scalping` concepts into these new strategies.

## Current Test Results

Backend tests pass:

```text
27 tests passing
```

Single-window H1 backtest, ending 2026-08-01, starting balance `$50`, fixed 0.01 lots:

| Capacity | Best Strategy | Ending Balance | P/L | Trades | Win Rate | Max Drawdown | Profit Factor | Daily Average |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | `socrates_key_level_volume_session_short` | `$291.92` | `$241.92` | 28 | 75.0% | `$68.28` | 2.27 | `$3.67` |
| 3 | `socrates_key_level_volume_session_short` | `$468.42` | `$418.42` | 39 | 79.5% | `$86.32` | 2.80 | `$6.34` |
| 5 | `socrates_key_level_volume_session_short` | `$492.75` | `$442.75` | 40 | 80.0% | `$86.32` | 2.91 | `$6.71` |

The volume-profile short found one very selective winning trade in this H1 window. The Sanos iFVG setup found no H1 trades, which matches the PDF warning that the setup belongs on 5-minute execution, not hourly candles.

## Interpretation

The applied research did not magically prove a `$300/day` path from `$50`. It made the system more honest:

- H1 Socrates short still wins this three-month slice.
- The added iFVG rules are probably correct but need M5/M1 broker-history ingestion before they can fire.
- Capacity improves this slice, but even capacity 5 is still around `$6.71/day` in the current model.
- The next material improvement is not another UI metric. It is real M5/M1 XAUUSD ingestion, DXY/calendar context, and a faster walk-forward runner.

## Next Implementation Work

1. Import real XAUUSD M5/M1 bars into the simulator.
2. Run Sanos iFVG only on M5/M1, not H1.
3. Add DXY confirmation as a deterministic filter.
4. Add a macro blackout or high-volatility mode for CPI, NFP, and FOMC windows.
5. Optimize the walk-forward runner so full capacity sweeps finish interactively.

## Sources

- Local: `/Users/paul/development/TRADING/documents and books/Sanos Setup.pdf`
- ResearchGate summary of Iwatsubo, Watkins, and Xu, `Intraday Seasonality in Efficiency, Liquidity, Volatility and Volume: Platinum and Gold Futures in Tokyo and New York`: https://www.researchgate.net/publication/320937125_Intraday_Seasonality_in_Efficiency_Liquidity_Volatility_and_Volume_Platinum_and_Gold_Futures_in_Tokyo_and_New_York
- CME Gold Futures Volume and Open Interest: https://www.cmegroup.com/markets/metals/precious/gold.volume.html
- CME Gold Futures product page: https://www.cmegroup.com/markets/metals/precious/gold-futures.html
- CME Gold Futures quotes and CVOL reference: https://www.cmegroup.com/markets/metals/precious/gold.quotes.html
