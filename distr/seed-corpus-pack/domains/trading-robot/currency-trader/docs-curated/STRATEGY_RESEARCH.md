# Strategy Research And Validation

This file separates strategy ideas from approved trading logic.

## Current Automatic Strategy

None. The maintained gold configuration has no current assignment or promoted symbol.

The registered `ema_trend_v1` baseline is not execution-authorized because its baseline and cost-stress expectancy are negative. A future entry strategy must require:

- EMA fast/slow cross up.
- ADX above the configured minimum.
- +DI greater than -DI.
- RSI inside the configured band.
- ATR inside the configured volatility window.
- Spread below the configured maximum.
- A sealed evidence report with sufficient samples and positive cost-stressed expectancy.
- A passed probability calibration artifact.
- Demo account, Scalping mode when applicable, and position limits passing risk gates.

Safety exits remain deliverable even when no new-entry strategy is authorized. A strategy-specific exit may require:

- EMA fast crossing below EMA slow while a long position is open.

## Default Chart Indicators

The TradingView chart loads these studies by default:

- Simple Moving Average.
- Exponential Moving Average.
- RSI.
- MACD.
- Bollinger Bands.

These are chart context indicators. They do not change the bot's execution until a strategy version explicitly uses them and passes validation.

## Transcript-Derived Ideas

Local transcript checked:

```text
/Users/paul/development/TRADING/artifacts/krypt-trader-video/transcript-corrected.md
```

Useful ideas from that transcript:

- Treat edge and confidence as separate gates.
- Track open positions, won positions, realized P/L, and filled size.
- Do not trust raw P/L unless the accounting path is verified.
- Support time/day trading windows.
- Support alerts and audit logs.
- Allow strategy profiles with different monitored categories and thresholds.

Not directly portable to XAUUSD/forex without testing:

- Kalshi whale/momentum signals.
- 15-minute crypto delta strategy.
- Claimed 91 percent win rate.
- Claimed 20 percent ROI.

## Promotion Rules

A strategy idea cannot become live logic until it has:

- A deterministic rule definition.
- Historical data for the target symbol and timeframe.
- Backtest results with win rate, profit factor, drawdown, expectancy, and trade count.
- Out-of-sample or walk-forward validation.
- A comparison against the current baseline.
- A failure review describing when the strategy loses.

## Next Strategy Candidates

Candidate ideas to research:

- Trend continuation with EMA 12/26 plus ADX/DI confirmation.
- Bollinger Band compression followed by breakout confirmation.
- RSI pullback inside H1/H4 trend.
- ATR volatility filter to avoid dead or chaotic sessions.
- Time-window filter for high-liquidity London/New York sessions.

The bot must remain fail closed for automatic entries until a gold candidate survives this process and XM demo-forward validation.
