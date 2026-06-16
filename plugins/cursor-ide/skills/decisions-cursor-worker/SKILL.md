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
5. For workflow steps, iterate inside the step before reporting complete:
   **do → test → self-assess (drift, security, UI sanity) → correct → report**.
   Use bundled skills when listed in the work packet. The human steers via the
   orchestrator, not by programming sub-steps in Cursor.

   Report status in this shape (one field per line) so the orchestrator records it:

```text
Status: completed | failed | needs_input
Summary: ...
Tests run: ...
Drift check: ...
Security: ...
UI assessment: ...
Self-corrections: ...
Files changed: ...
Blockers: ...
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
11. Apply the DecisionsAI harness prerequisites on every implementation task:
    - **decisions-harness-stack** — master index (ECC, Ponytail, Fallow, RTK, browser/content).
    - **Ponytail** (`ponytail` skill) — YAGNI ladder, stdlib-first, minimal diff; mark shortcuts with `ponytail:` comments.
    - **Fallow** (`fallow` skill) — on JS/TS repos, run `npx fallow audit --format json --quiet || true` before reporting complete and attach the verdict in `Tests run:` or `Evidence:`.
    - **Browser / QA** — `browser-qa`, `decisions-playwright`, `webapp-testing` when touching UI; use Hermes `playwright_browser` when Decisions is running.
    - **Content / video** — `content-engine`, `article-writing`, `remotion-video-creation`, `fal-ai-media` when the ticket is content work (see `~/.decisions/harness/mcp-recommendations.json` for fal MCP).
    - **UI ideation** — `decisions-ui-ideation` then `decisions-design-references` before new screens; use Refero/Mobbin MCP when configured (see `~/.decisions/harness/mcp-setup-design.sh`).
    - **Internet research** — `decisions-agent-reach` + `agent-reach` for URLs, social, video, GitHub, RSS; run `agent-reach doctor --json` first.
    - **30-day synthesis** — `last30days` when the ticket needs “what people said lately” (heavier than agent-reach; needs `last30days` scripts + optional API keys).
    - **Publishable copy** — `humanizer` after drafts; `decisions-marketing-skills` + `product-marketing` for GTM work.
    - **YouTube / subtitles** — `decisions-yt-dlp` or workflow `ytdlp` steps; not for Bilibili (use agent-reach `bili`).
    - **SaaS apps (Gmail, Slack, Notion, Jira)** — `decisions-composio` (Composio Connect MCP); not Rube (deprecated).
12. For any normal Cursor IDE/chat prompt inside a DecisionsAI project folder,
    even when it did not originate from a workflow packet, report the turn to
    DecisionsAI as a project IDE session. At the start of the prompt, call:
    `python3 ~/.cursor/plugins/local/decisions-cursor/scripts/report_decisions_event.py --event-type cursor_prompt_submitted --status observed --input "<user prompt>" --thread-id "<cursor chat id when known>"`
    Before the final response, call:
    `python3 ~/.cursor/plugins/local/decisions-cursor/scripts/report_decisions_event.py --cwd "<project folder>" --turn-output "Status: completed\nSummary: <short result summary>" --thread-id "<cursor chat id when known>"`
    If both sides of a completed turn are only available at the end, call:
    `python3 ~/.cursor/plugins/local/decisions-cursor/scripts/report_decisions_event.py --cwd "<project folder>" --turn-input "<user prompt>" --turn-output "Status: completed\nSummary: <short result summary>"`
    For workflow work packets, the reporter auto-discovers the bridge URL and
    execution session from the newest `.tickets/ticket_*.md` or `.tickets/decisionsai_*.md` file.
13. If the prompt includes a `[DECISIONS CURSOR CALLBACK]` block, treat that as
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
14. Do not keep steering only inside the Cursor conversation. If the human gives
    new direction while a DecisionsAI workflow is running, report it so
    DecisionsAI can store the event, update workflow memory, and let the
    orchestrator decide whether to continue, validate, retry, or ask a follow-up.
15. Before reporting an event, assume DecisionsAI may or may not be open. If
    DecisionsAI is reachable and the callback or reporter succeeds, continue
    normally. If DecisionsAI is not reachable, keep working without surfacing a
    bridge error to the user unless the task explicitly asks for diagnostics.
