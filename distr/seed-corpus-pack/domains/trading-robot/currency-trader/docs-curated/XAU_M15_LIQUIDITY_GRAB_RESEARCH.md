# XAU M15 liquidity-grab research (carousel)

Independent replay of the Instagram XAU strategy (30 EMA + LuxAlgo-style S/R pivots L/R=1 + liquidity grab + 1:1). **Not AvEMA.**

## Result: REJECT

| Window | Trades | Win rate | P/L (0.01 lot, $500 start accounting) | PF | Max DD |
|--------|--------|----------|----------------------------------------|----|--------|
| Apr 2025 (claim-like) | 71 | 38% | −$221 | 0.51 | 44% |
| Apr 2026 | 54 | 54% | −$19 | 0.95 | 17% |
| 2025 full | 718 | 41% | −$1300 | 0.61 | >100%* |
| 2024 holdout | 677 | 34% | −$1373 | 0.40 | >100%* |

\*DD >100% means the cumulative independent trade path blew past the $500 notionals used for the curve — strategy loses hard, not “<2% DD.”

Instagram claim (~76% WR, &lt;2% DD, 98 trades) **does not reproduce**.

## Rules encoded

See [`scripts/backtest_xau_m15_liquidity_grab.py`](../../scripts/backtest_xau_m15_liquidity_grab.py) and report [`data/training_reports/xau_m15_liquidity_grab_parity.json`](../../data/training_reports/xau_m15_liquidity_grab_parity.json).

## Action

- Do **not** wire into MT4 bridge / DEMO.
- Do **not** expect this to fix FX pairs.
- Park unless someone re-specs with different pivot/expiry/session filters and re-runs Path B style.
