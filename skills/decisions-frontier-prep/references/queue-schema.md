# Frontier Queue Packet Schema

Each packet must be executable without new planning.

```json
{
  "id": "fp-<project>-<n>",
  "title": "imperative, specific title",
  "domain": "security|correctness|performance|architecture|design|copy|marketing|techdebt|feature",
  "working_directory": "/absolute/path/to/repo-or-project",
  "files": ["concrete/path/or/artifact"],
  "operation": "the exact implementation or production action",
  "reason": "why this needs the stronger model or saves rounds",
  "acceptance": "one sentence done state",
  "machine_check": "command or deterministic check that proves done",
  "expect": "expected output or invariant",
  "rounds_saved": 0,
  "ceiling_lift": "low|med|high",
  "value": "low|med|high",
  "risk": "low|med|high",
  "score": 0.0,
  "blocked_by": []
}
```

Required checks before queueing:

- Cited files or artifacts exist now, unless the packet is explicitly for creating a new artifact.
- The operation is narrow enough for one implementation pass.
- The machine check can be run by the target harness or replaced with a Decisions workflow validation step.
- The packet is not duplicate work already present in another wave or active ticket.
