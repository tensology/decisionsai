# FX deterministic rule search

## Deterministic?
Yes — frozen OHLC + frozen rule → same trades.

## Can we force every pair profitable?
We expand the search under fixed gates. v3 densified range-reversion so AUDUSD could clear the same bar.

Latest run: `2026-08-04T13:42:58.582778+00:00` (v3 grid = 39488).

**All pairs have a rule:** YES

## Results

### EURUSD: **RULE_FOUND**
- `range_H4_s1.25_rr1.0_h10_rsi32_68_adx24_all` (range_reversion, H4)
- Train PF=2.005 n=22
- Validate PF=1.182 n=21
- Holdout PF=1.963 n=34
- Survivors: 48 | families: `{'range_reversion:H4': 47, 'donchian_40:H4': 1}`
- Still needs XM demo forward sample before auto-trading.

### GBPUSD: **RULE_FOUND**
- `range_H4_s1.0_rr1.4_h24_rsi32_68_adx22_all` (range_reversion, H4)
- Train PF=2.458 n=20
- Validate PF=2.817 n=14
- Holdout PF=1.944 n=24
- Survivors: 591 | families: `{'range_reversion:H4': 589, 'range_reversion:H1': 2}`
- Still needs XM demo forward sample before auto-trading.

### USDJPY: **RULE_FOUND**
- `london_H4_s1.6_rr2.0_h36_adx14_all_L` (london_breakout, H4)
- Train PF=1.554 n=24
- Validate PF=2.351 n=25
- Holdout PF=1.235 n=36
- Survivors: 16 | families: `{'london_breakout:H4': 16}`
- Still needs XM demo forward sample before auto-trading.

### AUDUSD: **RULE_FOUND**
- `range_H1_s1.5_rr1.6_h10_rsi30_72_adx24_all` (range_reversion, H1)
- Train PF=1.329 n=46
- Validate PF=1.162 n=43
- Holdout PF=1.952 n=66
- Survivors: 398 | families: `{'range_reversion:H1': 397, 'range_reversion_deep:H1': 1}`
- Still needs XM demo forward sample before auto-trading.

## Bottom line
- Found: EURUSD, GBPUSD, USDJPY, AUDUSD
- No edge: none
- Rule pack: `data/training_reports/fx_majors_rule_pack.json`
- JSON: `data/training_reports/fx_deterministic_rule_search_v3.json`
