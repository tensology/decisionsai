# Verify feedback loop — SYNTHESIZED

```
┌─────────┐   ┌──────────┐   ┌────────────┐   ┌─────────┐
│  FRAC   │→  │  Implement│→  │  Verify    │→  │ Evidence│
│  (03)   │   │  (TDD)    │   │  fresh cmd │   │ + retro │
└─────────┘   └──────────┘   └─────┬──────┘   └─────────┘
                                   │ fail
                                   └──→ rework / reopen AC
```

## Tiers (qa-tester style)
- QUICK: smoke critical path
- STANDARD: AC checklist
- EXHAUSTIVE: edge + regression

## Evidence format
```
Command: <exact>
Exit: 0
Summary: N passed / 0 failed
AC covered: AC1, AC3
```
