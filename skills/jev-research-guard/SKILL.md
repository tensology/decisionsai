# Jev Research Guard

Use `jev_check_research` after collecting evidence and before presenting a material claim as established.

## Procedure

1. State exactly one claim to evaluate.
2. Provide compact evidence entries with source, date, and what each source directly supports.
3. Set `source_quality` conservatively. Use `unknown` when provenance is unclear.
4. Set `stakes` by the consequence of being wrong, not by confidence.
5. Call `jev_check_research`.

## Verdict handling

- `accept`: use the claim with appropriate attribution and caveats.
- `verify_more`: collect independent or more direct evidence, then evaluate again.
- `reject`: do not use the claim in its current form.

Do not send secrets, whole documents, or unrelated context. Jev evaluates supplied evidence; it does not browse or verify sources on its own.
