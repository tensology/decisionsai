# Completion manifest schema

Return the manifest on one line after the exact label `completion_manifest:`.

```json
{"objective":"Original requested outcome","inventory":[{"id":"surface-id","status":"verified","evidence":["fresh command, artifact, or runtime observation"],"reason":""}],"negative_space_checks":[{"check":"search for omitted behavior","passed":true,"evidence":"command or observation"}],"journeys":[{"journey":"complete user flow","verified":true,"evidence":"fresh end-to-end result"}],"exclusions":[{"item":"approved non-goal","approved":true,"reason":"user-approved reason"}],"blockers":[],"independent_audit":{"passed":true,"reviewed_against_original":true,"findings":[]},"verdict":"complete"}
```

Rules:

- `inventory` cannot be empty.
- Every inventory item must use `verified`, `excluded`, or `blocked`.
- `verified` items require non-empty evidence.
- `excluded` items require an approved matching exclusion and a reason.
- `blocked` items force verdict `incomplete`.
- Every negative-space check must pass and include evidence.
- Every required journey must be verified and include evidence.
- Every exclusion requires `approved: true` and a reason.
- Any blocker or independent-audit finding forces verdict `incomplete`.
- `complete` is rejected unless the independent audit reviewed the result against the original request.
