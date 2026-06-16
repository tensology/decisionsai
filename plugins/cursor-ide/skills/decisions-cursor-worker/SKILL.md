---
name: decisions-cursor-worker
description: Use when Cursor is running work that originated from DecisionsAI tickets, projects, workflows, or Initiative proposals.
---

# DecisionsAI Cursor Worker

When a task comes from DecisionsAI:

1. Treat the instruction as project-bound work. Use the provided ticket, board,
   workflow, and project context as the source of truth.
2. Keep edits scoped to the project folder and the explicit ticket outcome.
3. If the prompt includes previous-step context or a result packet, use it as
   state handed over by the DecisionsAI workflow. Do not discard it when
   implementing or validating the current step.
4. Respect the model selected by DecisionsAI/Cursor runtime. Do not claim a
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
9. Prefer the Cursor IDE/chat surface as the primary execution context. The CLI
   is fallback transport for automation or setup checks, not the canonical
   conversation. Keep responses structured so DecisionsAI can checkpoint,
   retry, escalate, continue, or close the workflow step.
10. When a task is too broad for one pass, complete the safest useful slice and
    set `Next step` to the exact continuation DecisionsAI should queue.
11. For any normal Cursor IDE/chat prompt inside a DecisionsAI project folder,
    even when it did not originate from a workflow packet, report the turn to
    DecisionsAI as a project IDE session. At the start of the prompt, call:
    `python3 ~/.cursor/plugins/local/decisions-cursor/scripts/report_decisions_event.py --event-type cursor_prompt_submitted --status observed --input "<user prompt>"`
    Before the final response, call:
    `python3 ~/.cursor/plugins/local/decisions-cursor/scripts/report_decisions_event.py --cwd "<project folder>" --turn-output "Status: completed\nSummary: <short result summary>"`
    If both sides of a completed turn are only available at the end, call:
    `python3 ~/.cursor/plugins/local/decisions-cursor/scripts/report_decisions_event.py --cwd "<project folder>" --turn-input "<user prompt>" --turn-output "Status: completed\nSummary: <short result summary>"`
    For workflow work packets, the reporter auto-discovers the bridge URL and
    execution session from the newest `.tickets/decisionsai_*.md` file.
12. If the prompt includes a `[DECISIONS CURSOR CALLBACK]` block, treat that as
    live workflow metadata. Use the callback URL or reporter script in that
    block to report meaningful events back to DecisionsAI:
    - `cursor_started` when work begins.
    - `cursor_prompt_submitted` after every user prompt or IDE instruction
      submission connected to this DecisionsAI run, including follow-up prompts
      typed directly in Cursor.
    - `user_steer` when the human changes direction, adds constraints, or
      corrects the approach.
    - `cursor_waiting` or `cursor_needs_input` when blocked on a decision.
    - `cursor_interrupted` when the current task is paused or superseded.
    - `cursor_progress` for material implementation milestones.
    - `cursor_completed` or `cursor_failed` when the work finishes.
    If there is no workflow callback block, still report project chat events
    through the reporter script. It will use the current working directory to
    create or resume a DecisionsAI project IDE session:
    `python3 ~/.cursor/plugins/local/decisions-cursor/scripts/report_decisions_event.py --event-type cursor_prompt_submitted --status observed --message "<what changed>"`
13. Do not keep steering only inside the Cursor conversation. If the human gives
    new direction while a DecisionsAI workflow is running, report it so
    DecisionsAI can store the event, update workflow memory, and let the
    orchestrator decide whether to continue, validate, retry, or ask a follow-up.
14. Before reporting an event, assume DecisionsAI may or may not be open. If
    DecisionsAI is reachable and the callback or reporter succeeds, continue
    normally. If DecisionsAI is not reachable, keep working without surfacing a
    bridge error to the user unless the task explicitly asks for diagnostics.
