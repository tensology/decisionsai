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

## Global completion gate

For non-trivial work, read the global `completion-protocol` skill. Verification is incomplete until the original outcome has a full affected-surface inventory, every item is accounted for in a coverage ledger, negative-space checks have searched for omissions, required end-to-end journeys pass, and an independent audit has attempted to disprove completion.

Return the machine-checkable `completion_manifest` defined by that skill. Any unapproved exclusion, blocker, missing evidence, failed omission check, unverified journey, or unresolved audit finding requires verdict `incomplete`.
