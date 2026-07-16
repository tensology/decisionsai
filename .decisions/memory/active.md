# Active state

- Production-readiness release candidate is at the commit/push gate.
- Automatic model routing is opt-in and role-aware: Codex plans medium/high-complexity work, local/free Ornith implements bounded work, independent review can use HY3, and Claude is reserved for evidence-backed last-resort escalation.
- Live run #103 proved the existing Runs UI exposes the selected provider/model, rationale, step, elapsed time, capabilities, and heartbeats while Codex planning handed implementation to Ornith 9B.
- Live pinned Ornith 35B run #100 and Ornith 9B run #103 timed out cleanly after five minutes; provider timeouts now become explicit failures and preserve the selected failover route instead of silently repeating forever.
- Live OpenRouter run #101 selected exact `tencent/hy3-preview`; OpenRouter accepted the route but refused inference with HTTP 402 because the account has insufficient credits.
- Telegram workflow messages are human-facing, deduplicated, and support durable text, voice, button, and attachment interaction round trips.
- Exact default suite: 2,916 passed, 27 skipped, 71 deselected, 1 expected failure, 0 unexpected failures. Pizza House Node tests: 11 passed. Human browser acceptance: validated menu rendered and Add to order changed subtotal from R0 to R148.
- DecisionsAI is intentionally stopped for the full-tree commit and will be restarted from the pushed `main` build.

_updated: 2026-07-16T21:35:00Z_
