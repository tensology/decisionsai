# Positive Edge Research

Date: 2026-08-02

## Decision

One deterministic XAUUSD strategy has supported long-horizon historical edge evidence: `gold_daily_trend_momentum_research`.

It remains research-only and `ALERTS_ONLY`. Strict live promotion fails because four of twelve calendar years lost money and there is no forward demo sample. A profitable backtest is evidence, not a guarantee.

## Data

- 1,556,926 XAUUSD M5 candles from 2005-01-02 through 2026-07-31.
- All 6,756 requested daily cache files are present.
- Zero duplicate timestamps and zero invalid OHLC rows.
- 0.1435% unclassified missing-interval rate.
- 2,525 older rows have nonpositive observed spreads. The validators therefore use explicit execution-cost floors instead of trusting those spread values.

## Supported Edge

The fixed daily rule uses a 50-day/200-day trend filter, 21-day momentum confirmation, ATR-based exits, next-bar execution, and no AI discretion in order generation.

| Metric | Result |
| --- | ---: |
| Trades | 270 |
| Win rate | 43.7% |
| Profit factor | 1.679 |
| Expectancy | $9.977 per trade at 0.01 lot |
| Net P/L | $2,693.70 |
| Maximum drawdown | $689.55 |
| Positive calendar years | 8 of 12 |
| Bootstrap probability of positive P/L | 99.42% |
| Bootstrap 95% P/L interval | $551.92 to $5,215.26 |

Execution-cost stress remained positive:

| Modeled spread | Net P/L | Profit factor | Expectancy |
| --- | ---: | ---: | ---: |
| 130 pips | $2,606.59 | 1.649 | $9.654 |
| 190 pips | $2,459.00 | 1.600 | $9.107 |

These figures describe this historical sample only. They do not establish future profitability.

## Capital Boundary

At XM's minimum 0.01-lot gold position, one price dollar is approximately one account dollar before costs. Historical initial stop risk was:

- Minimum: $14.32
- Median: $34.71
- Maximum: $445.95
- Capital needed for the median trade at 2% risk: $1,735.54
- Capital needed to admit every historical trade at 2% risk: $22,297.60

The reference research balance is therefore $25,000, where maximum historical drawdown was 2.76%. The `$50` and `$500` scenarios could not take the strategy's ordinary trades while respecting a 2% risk cap. Leverage reduces margin used but does not reduce stop-loss cash risk.

## Rejected Variants

- `gold_published_regime_m15_causal_walk_forward`: positive in aggregate but only 5 of 10 years were positive. Strict edge gate failed.
- `gold_forecast_policy_walk_forward`: the fractional diagnostic and XM 0.01-lot mode both failed stability and bootstrap gates.
- `gold_published_daily_trend_stressed`: adding a 200-day moving-average safety layer did not improve calendar-year consistency enough to pass live promotion.

Rejected configurations must not be selected because one period looks attractive.

## Operational Rule

- Do not replace the active strategy automatically.
- Keep execution in `ALERTS_ONLY`.
- Freeze the candidate before forward demo testing.
- Require broker-history parity, a forward-demo sample, explicit review, and explicit approval before any execution-mode change.

## Reproduce

From the project root:

```bash
./scripts/run_positive_edge_research.sh
```

The command profiles the full cache, runs the daily and intraday validators, runs cost stress, executes the unit tests, and builds the dashboard.

## Research Sources

- [Forecast-to-Fill: A Practical Framework for Systematic Trading](https://arxiv.org/abs/2511.08571)
- [The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/Papers.cfm?abstract_id=2326253)
- [The Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
- [XM Risk Disclosure](https://www.xm.com/assets/pdf/new/docs/XM-Risk-Disclosure.pdf)

