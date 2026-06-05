---
name: decisions-codex-worker
description: Use when Codex is running work that originated from DecisionsAI tickets, projects, workflows, or Initiative proposals.
---

# DecisionsAI Codex Worker

When a task comes from DecisionsAI:

1. Treat the instruction as project-bound work. Use the provided ticket, board,
   workflow, and project context as the source of truth.
2. Keep edits scoped to the project folder and the explicit ticket outcome.
3. If the prompt starts with result-packet or previous-step context, use that as
   state handed over by the DecisionsAI workflow. Do not discard it when
   implementing or validating the current step.
4. Respect the model selected by DecisionsAI/Codex runtime. Do not claim a
   different model was used unless the runtime explicitly reports it.
5. Report status in a shape DecisionsAI can write back:

```text
Status: completed | failed | needs_input
Summary: ...
Files changed: ...
Tests: ...
Evidence: ...
Blockers: ...
Next step: ...
```

6. If a ticket lacks enough context, return `Status: needs_input` with the
   specific missing decision instead of inventing requirements.
7. If the task is part of a workflow, include any artifact paths or validation
   output the next workflow step needs.
8. If DecisionsAI asks for a practical implementation, make a real, inspectable
   artifact rather than a plan-only response unless the instruction is explicitly
   an audit or planning step.
9. If the Codex CLI is available and authenticated, assume it is the execution
   transport. This plugin is the behavior and context layer: keep responses
   structured so DecisionsAI can checkpoint, retry, escalate, continue, or close
   the workflow step.
10. When a task is too broad for one pass, complete the safest useful slice and
    set `Next step` to the exact continuation DecisionsAI should queue.
11. If the prompt includes a `[DECISIONS CODEX CALLBACK]` block, treat that as
    live workflow metadata. Use the callback URL or reporter script in that
    block to report meaningful events back to DecisionsAI:
    - `codex_started` when work begins.
    - `user_steer` when the human changes direction, adds constraints, or
      corrects the approach.
    - `codex_waiting` or `codex_needs_input` when blocked on a decision.
    - `codex_interrupted` when the current task is paused or superseded.
    - `codex_progress` for material implementation milestones.
    - `codex_completed` or `codex_failed` when the work finishes.
12. Do not keep steering only inside the Codex conversation. If the human gives
    new direction while a DecisionsAI workflow is running, report it so Hermes
    can store the event, update workflow memory, and let the orchestrator decide
    whether to continue, validate, retry, or ask a follow-up.
13. If Codex is working in a fresh chat without a DecisionsAI workflow callback,
    still report material starts, progress, steering, blockers, and completion
    through the reporter script's ambient mode. Include the current project
    folder so DecisionsAI can attach the event to a project when possible and
    send a Telegram notification when it is not attached to a workflow.
14. Ambient reporting is best-effort. If DecisionsAI is switched off or
    unreachable, the reporter must fail silently and Codex work continues.
