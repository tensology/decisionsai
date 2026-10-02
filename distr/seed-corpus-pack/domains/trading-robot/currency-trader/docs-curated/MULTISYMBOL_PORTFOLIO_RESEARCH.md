# Multi-Symbol Portfolio Research

## Verdict

The current five-symbol **H1** portfolio is rejected for promotion. It remains available for research and alerts only.

**The MT4 bridge does not run this portfolio.** Runtime uses Path B: M1 `ema_trend_v1` with fixed pip stops. See [EMA_TREND_V1_M1_PARITY.md](EMA_TREND_V1_M1_PARITY.md) and `data/training_reports/ema_trend_v1_m1_xm_parity.json`. Do not confuse the automated trading switch with this H1 research verdict.

The tested universe is:

- `XAUUSD`, mapped to XM `GOLD#`
- `EURUSD`
- `GBPUSD`
- `USDJPY`
- `AUDUSD`

The recent rolling evaluation started with `$500` and ended with `$482.05`. It lost `$17.95` across 25 accepted trades, with a profit factor of `0.740`. The adverse-cost run ended at `$471.38` with a profit factor of `0.603`.

The independent historical holdout started with `$500` and ended with `$475.17`. It lost `$24.83` across 22 accepted trades, with a profit factor of `0.609`. Its cost-stress result ended at `$477.26` with a profit factor of `0.625`.

These results do not support automated execution. The configuration therefore contains no promoted symbols, no executable strategy assignments, and keeps the connector in `ALERTS_ONLY`. Research assignments are stored separately so a quarterly research winner cannot be mistaken for a runtime strategy.

## What Deterministic Means

A frozen strategy version produces the same decision from the same market state and account state. It does not alter its rules, thresholds, or coefficients while an evaluation quarter is running.

Learning happens only at a scheduled quarterly boundary:

1. Use only information available before the boundary.
2. Score fixed rule candidates on the preceding 12 months.
3. Train the regularized statistical candidate on older data.
4. Require that learned candidate to survive two sequential three-month calibration folds.
5. Refit through the boundary, freeze the coefficients, and evaluate the next quarter.

An LLM is not in the order path. It may summarize research and logs, but it cannot change a frozen decision or authorize execution.

## Data And Execution Model

The validator uses Dukascopy public bid and ask history. M1 data is aggregated to M5 and then H1. An H1 candle is retained only when all six M5 source bars are present.

The replay uses these conservative rules:

- A signal is calculated from a completed H1 candle.
- Entry occurs at the following H1 open.
- If a stop and target are both touched in one H1 candle, the stop fills first.
- Spread and slippage are deducted from every trade.
- A second adverse-cost scenario is tested.
- Lots are calculated from current equity and actual stop distance.
- Lot size is rounded down to the broker step.
- A trade is rejected when the broker minimum lot exceeds the cash-risk budget.
- Total open risk, same USD-direction risk, margin use, concurrent positions, daily loss, and account drawdown are capped.
- Open stop risk is reserved immediately and included in the drawdown path before a trade closes.
- Every selected candidate is persisted in the report so the dashboard and validator replay the same chronological ledger.

## Why Gold Is Usually Rejected At $500

The historical daily gold rule showed useful long-horizon behavior, but a normal ATR stop at the broker minimum of `0.01` lot commonly risks more than 1% of a `$500` account. The portfolio engine rejects that order instead of pretending it can trade a smaller unavailable lot.

This is a capital and contract-size constraint, not a missing signal.

## Candidate Admission Gate

A quarterly candidate must have all of the following in training or calibration:

- At least 20 closed trades.
- Profit factor of at least 1.25.
- Expectancy of at least 0.12R per trade.
- Positive results in at least 60% of represented months.
- Maximum drawdown no greater than 10R.
- Positive expectancy under adverse costs.
- Stress profit factor of at least 1.05.
- Both sequential calibration folds profitable for the learned candidate.

Passing admission does not imply promotion. The following unseen quarter can still fail, as happened with EURUSD in the recent run.

## Portfolio Promotion Gate

The assembled portfolio must also meet all of these conditions:

- At least 30 accepted trades.
- Profit factor of at least 1.20.
- Positive cash expectancy.
- At least three of four positive evaluation quarters.
- Maximum drawdown no greater than 15%.
- Positive P/L and profit factor of at least 1.05 under adverse costs.
- XM demo history parity and a forward demo sample.

## Reproduce The Reports

Run both frozen evaluations:

```bash
/Users/paul/development/TRADING/currency-trader/scripts/run_multisymbol_portfolio_research.sh
```

The reports are:

- `data/training_reports/multisymbol_portfolio_walk_forward.json`
- `data/training_reports/multisymbol_portfolio_independent_holdout_2023_2024.json`

Download or resume the earlier FX history block:

```bash
PYTHONPATH=/Users/paul/development/TRADING/currency-trader/packages \
/Users/paul/.virtualenvs/trading/bin/python \
/Users/paul/development/TRADING/currency-trader/scripts/download_dukascopy_portfolio.py \
  --symbols EURUSD,GBPUSD,USDJPY,AUDUSD \
  --start-date 2022-08-01 \
  --end-date 2024-07-31 \
  --workers 12
```

## Next Evidence Required

The next useful step is not more threshold searching on these same periods. It is XM demo telemetry with actual fills, spreads, rejected orders, swaps, and closed-trade outcomes. Those records can measure broker parity and supply a genuinely forward sample without contaminating the historical tests. Re-running an identical holdout is idempotent and cannot increase memory confidence.
