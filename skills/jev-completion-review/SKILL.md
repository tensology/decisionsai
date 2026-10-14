# Jev Completion Review

Use `jev_review_completion` immediately before declaring a non-trivial objective complete.

Read and apply the global `completion-protocol` skill first. Jev reviews the resulting completion manifest; it does not replace the required inventory, coverage ledger, negative-space audit, end-to-end evidence, or independent audit.

## Procedure

1. Restate the original objective without weakening its requirements.
2. Inspect the completion manifest and confirm every inventory item has verified evidence or an approved exclusion.
3. Confirm negative-space checks and required end-to-end journeys used fresh evidence.
4. List verification actually run, including failures and limitations.
5. List every known gap. Use an empty list only after actively searching for omissions.
6. Call `jev_review_completion`.

## Verdict handling

- `complete`: report completion and the relevant verification.
- `verify_more`: run the missing checks, then evaluate again.
- `incomplete`: continue the required work or report the concrete blocker.

Never mark work complete merely because time or budget is low. Jev is an advisory decision layer; higher-priority instructions and safety requirements still apply.
