# Jev Completion Review

Use `jev_review_completion` immediately before declaring a non-trivial objective complete.

## Procedure

1. Restate the original objective without weakening its requirements.
2. List completed work as observable outcomes.
3. List verification actually run, including failures and limitations.
4. List every known gap. Use an empty list only after checking.
5. Call `jev_review_completion`.

## Verdict handling

- `complete`: report completion and the relevant verification.
- `verify_more`: run the missing checks, then evaluate again.
- `incomplete`: continue the required work or report the concrete blocker.

Never mark work complete merely because time or budget is low. Jev is an advisory decision layer; higher-priority instructions and safety requirements still apply.
