# Jev Task Router

Use `jev_route_task` before committing to an execution path when the task has meaningful ambiguity or risk.

## Procedure

1. Summarize the task in one factual paragraph.
2. Supply only evidence already observed. Do not label assumptions as evidence.
3. List binding constraints, especially irreversibility, security, cost, external effects, and missing verification.
4. Call `jev_route_task`.
5. Follow the returned route unless it conflicts with a higher-priority instruction or safety policy.
6. Treat `guidance` as preset guidance and probabilities as decision evidence, not authorization.

## Route handling

- `proceed_fast`: continue with normal checks.
- `deep_review`: investigate the uncertain or sensitive boundary first.
- `split_task`: separate the work into independently verifiable changes.
- `block`: stop the risky action and resolve the blocker or ask the user.

If `needs_human_review` is high, obtain human review before an irreversible or externally visible action. Never use Jev to widen the user's requested scope.
