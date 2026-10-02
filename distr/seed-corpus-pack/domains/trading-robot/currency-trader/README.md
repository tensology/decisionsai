# Currency Trader

Currency Trader is a multi-account, gold-only trading research and controlled-execution system for XM MT4.

The MT4 Expert Advisor is only the broker adapter. The local bot owns strategy, memory, market context, model routing, and audit logs.

## Current Shape

```text
apps/
  dashboard/        React + Tailwind dashboard.
  bot-api/          Flask API for dashboard and bot control.
  mt4-bridge/       Existing XM MT4 EA and Flask signal bridge.
  telegram-listener/ Real-time provider channel listener.
packages/
  currency_trader/  Local bot engine.
config/
  bot.json          Runtime configuration.
docs/
  architecture/     API, control-plane, and frontend architecture.
  audits/           Dated project and reference audits.
  operations/       Installation and production runbooks.
  planning/         Build and remediation plans.
  research/         Backtests, studies, and applied research.
  strategies/       Strategy registry and provider policy.
  ui/               Interface standards.
references/
  repositories/     External source repositories used for comparison.
  media/youtube/    Source videos, transcripts, screenshots, and OCR.
artifacts/
  qa/               Visual QA evidence and raw captures.
  releases/         Generated deployment bundles.
  backups/          Local copies of production backups.
  config-snapshots/ Point-in-time deployment configuration snapshots.
tests/
  test_engine.py    Engine smoke tests.
```

## Safety Model

- The authoritative runtime policy is `config/runtime_policy.json`.
- Live accounts accept only an operator-submitted manual ticket that confirms the exact XM account number. Autonomous execution remains demo-only until a live stage is explicitly authorized.
- The Council can authorize a demo order only after market evidence, a registered strategy, cost-stressed strategy evidence, sealed probability calibration, provider, account, bridge, telemetry, freshness, geometry, and risk gates pass.
- Telegram channels collect evidence but cannot directly authorize execution.
- The EA still enforces account identity, server, terminal permission, symbol, lot, and stop checks. Manual tickets bypass Council approval, but cannot bypass XM or MT4 execution requirements.
- Model provider keys are encrypted before SQLite storage and only shown redacted.
- OpenAI and OpenRouter may summarize context, but their output is never an order signal.
- Transcript and OCR concepts are research metadata. Mention counts do not alter candidate ranking.
- XAUUSD is the only automatic-execution symbol. Scalping additionally requires its separate per-account switch.
- No strategy is currently assigned or promoted. `ema_trend_v1` remains registered at `demo_forward`, but its evidence report is negative and the evidence gate rejects it.

## Gold Research

The active research universe is `GOLD#` only. Validators use bid/ask history, stop-distance-based sizing, adverse spread, commission, slippage and latency assumptions, chronological walk-forward periods, and an untouched holdout. The older multi-symbol reports remain historical evidence only.

Current evidence from `$500`:

- Recent walk-forward: `$482.05`, 25 trades, profit factor `0.740`.
- Recent cost stress: `$471.38`, profit factor `0.603`.
- Independent holdout: `$475.17`, 22 trades, profit factor `0.609`.
- Independent cost stress: `$477.26`, profit factor `0.625`.

These are failed research results, not forecasts.

Run both portfolio evaluations:

```bash
/Users/paul/development/TRADING/currency-trader/scripts/run_multisymbol_portfolio_research.sh
```

## Run Tests

```bash
PYTHONPATH=/Users/paul/development/TRADING/currency-trader/packages \
/Users/paul/.virtualenvs/trading/bin/python -m unittest discover -s /Users/paul/development/TRADING/currency-trader/tests
```

## Install

```bash
/Users/paul/development/TRADING/currency-trader/scripts/install.sh
```

Then verify:

```bash
/Users/paul/development/TRADING/currency-trader/scripts/check_env.sh
```

## Run The API

```bash
/Users/paul/development/TRADING/currency-trader/scripts/run_api.sh
```

## Run 15 Day Profitability Goal Search

Use this command to run the deterministic 15-day goal search on GOLD:

```bash
cd /Users/paul/development/TRADING/currency-trader
./scripts/run_15d_goal.sh
```

Optional custom parameters:

```bash
./scripts/run_15d_goal.sh [END_DATE] [STARTING_BALANCE] [TARGET_BALANCE] [TRADING_DAYS] [TIMEFRAMES]
```

Example:

```bash
./scripts/run_15d_goal.sh 2026-08-03 500 4000 15 1h,5m
```

Results are written to:

`data/training_reports/goal_15d_significant_profit.json`


Or start everything from the project root:

```bash
cd /Users/paul/development/TRADING/currency-trader
./start.sh
```

`./start.sh` supervises all services by default and restarts anything that crashes
(including the MT4 bridge). Keep that terminal open, or use `./start.sh --daemon`
(alias: `--forever`) for the same loop in tmux. Press `Ctrl-C` or run `./stop.sh` to stop.

Dashboard:

```text
http://127.0.0.1:43917
```

Backend API:

```text
http://127.0.0.1:43918
```

MT4 bridge:

```text
http://127.0.0.1:43919
```

## Docs

- [Install Guide](docs/operations/INSTALL.md)
- [Runbook](docs/operations/RUNBOOK.md)
- [Build Plan](docs/planning/PLAN.md)
- [API Example](docs/architecture/API_EXAMPLE.md)
- [Signal Providers](docs/strategies/SIGNAL_PROVIDERS.md)
- [Positive Edge Research](docs/research/POSITIVE_EDGE_RESEARCH.md)
- [Multi-Symbol Portfolio Research](docs/research/MULTISYMBOL_PORTFOLIO_RESEARCH.md)
- [Full Project Audit](docs/audits/PROJECT_AUDIT_2026-08-12.md)

## First Bot Principle

One active strategy. One version. Backtest first. Demo second. Live later, if ever.
