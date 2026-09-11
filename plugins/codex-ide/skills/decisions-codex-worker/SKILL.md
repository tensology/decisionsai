---
name: decisions-codex-worker
description: Use when Codex is running work that originated from DecisionsAI tickets, projects, workflows, or Initiative proposals.
---

# DecisionsAI Codex Worker

Treat the supplied Decisions project, ticket, workflow, callback, and prior-step packet as authoritative. Work in the named project and do not create another ticket, plan, workflow, or branch unless explicitly requested. Prefer the Codex IDE/chat surface as the primary execution context. The CLI is fallback transport for automation or setup checks.

## Lean execution

- Inspect only the files and lines needed for the request. Prefer focused search and bounded reads.
- Use ordinary project tools for routine edits. Read a specialist skill only when selected by the user or materially required, and normally use no more than two.
- Use `decisions-harness-stack` as the capability index. Skills including Impeccable, Playwright, computer-use, Ponytail, Fallow, content, research, and SaaS routing remain available on demand.
- Apply Ponytail's minimal-diff rule to implementation work. Use Impeccable and browser verification for UI work. Use computer-use only when the runtime exposes it.
- Run Fallow only when the workflow selects it or the task is a code-health, cleanup, PR-risk, or audit request. Save verbose JSON to a file and report its compact verdict. Never inject full audit output, long logs, or generated reports into the conversation.
- Run the smallest test that proves the change. Broaden checks only for failure, material risk, or an explicit request.
- Stop when direct evidence proves the requested outcome. Do not add speculative reviews, extra documents, or optional polish.

## Decisions reporting

For any normal Codex IDE/chat prompt inside a DecisionsAI project folder, report the turn through the quiet project IDE session reporter. At the start:

`python3 ~/plugins/decisions-codex/scripts/report_decisions_event.py --event-type codex_prompt_submitted --status observed --input "<user prompt>" --thread-id "<thread id when known>"`

Before the final response:

`python3 ~/plugins/decisions-codex/scripts/report_decisions_event.py --event-type codex_completed --status completed --output "<short result summary>" --thread-id "<thread id when known>"`

If both sides are only available at the end, use `--turn-input` and `--turn-output`. The reporter exits quietly when DecisionsAI is unavailable.

When a `[DECISIONS CODEX CALLBACK]` block is present, report meaningful lifecycle events: `codex_started`, `codex_prompt_submitted`, `user_steer`, `codex_progress`, `codex_waiting`, `codex_needs_input`, `codex_interrupted`, `codex_completed`, and `codex_failed`. Report human steering instead of keeping it only in Codex. Do not emit routine tool chatter as progress.

## Return contract

Return concise, checkpoint-friendly results:

```text
Status: completed | failed | needs_input
Summary: ...
Files changed: ...
Tests: ...
Evidence: ...
Blockers: ...
Next step: ...
```

Include artifact paths needed by the next step. If a real user decision is missing, return `needs_input` with that exact decision. If work is broad, complete the safest useful slice and name the exact continuation. Keep enough evidence for DecisionsAI to checkpoint, retry, escalate, continue, or close the step.
