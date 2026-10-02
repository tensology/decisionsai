# Jev Tool Router

Use Jev at a real decision boundary where explicit options and calibrated probabilities are more useful than generated prose. Call only the tool that matches the current decision.

## Ensure Jev MCP is connected

The Skill expects a Jev MCP server:

```text
https://www.jevai.org/api/mcp
```

Use that HTTPS www URL. Do not use `http://` or `https://jevai.org/api/mcp`. Those hosts redirect, and many MCP clients turn the POST into GET.

Authentication uses a personal key from `/agent/keys` after sign-in. Put it in a local credential store or `JEV_API_KEY`. Never put the key in a Skill file, project file, log, or tool output. If the six `jev_*` tools are unavailable, determine whether the MCP connection or its credential is missing. Do not invent successful tool calls.

## Choose one tool

- `jev_route_model`: choose among models the current environment can actually invoke when quality, cost, latency, context, or tool support affects the choice.
- `jev_route_task`: choose `proceed_fast`, `deep_review`, `split_task`, or `block` when an execution path is ambiguous or risky.
- `jev_guard_tool_call`: evaluate a consequential tool call immediately before execution when it can mutate external state, spend money, expose data, publish, deploy, change permissions, or be difficult to reverse.
- `jev_check_research`: evaluate whether supplied evidence supports one precise claim before presenting that claim as established.
- `jev_review_completion`: compare the objective, completed work, verification, and known gaps before reporting a non-trivial task as complete.
- `jev_decide`: define custom `choice`, `noul`, or `score` questions only when none of the preset tools describes the decision.

## Prepare and apply the decision

Send compact task state and only evidence relevant to the decision. Never include passwords, provider keys, access tokens, or unrelated private data.

Treat the returned choice, probability, or score as decision support. It does not grant new authority, replace deterministic validation, or bypass permissions and confirmations required by the user, platform, or policy. If material facts, arguments, candidates, or scope change, evaluate again.
