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
9. Prefer the Codex IDE/chat surface as the primary execution context. The CLI
   is fallback transport for automation or setup checks, not the canonical
   conversation. Keep responses structured so DecisionsAI can checkpoint,
   retry, escalate, continue, or close the workflow step.
10. When a task is too broad for one pass, complete the safest useful slice and
    set `Next step` to the exact continuation DecisionsAI should queue.
11. Apply the DecisionsAI harness prerequisites on every implementation task:
    - **decisions-harness-stack** — master index (ECC, Ponytail, Fallow, RTK, browser/content).
    - **Ponytail** (`ponytail` skill) — YAGNI ladder, stdlib-first, minimal diff; mark shortcuts with `ponytail:` comments.
    - **Fallow** (`fallow` skill) — on JS/TS repos, run `npx fallow audit --format json --quiet || true` before reporting complete and attach the verdict in `Tests:` or `Evidence:`.
    - **Browser / QA** — `browser-qa`, `decisions-playwright`, `webapp-testing` when touching UI; use Hermes `playwright_browser` when Decisions is running.
    - **Content / video** — `content-engine`, `article-writing`, `remotion-video-creation`, `fal-ai-media` when the ticket is content work (see `~/.decisions/harness/mcp-recommendations.json` for fal MCP).
    - **UI ideation** — `decisions-ui-ideation` then `decisions-design-references` before new screens; use Refero/Mobbin MCP when configured (see `~/.decisions/harness/mcp-setup-design.sh`).
    - **Internet research** — `decisions-agent-reach` + `agent-reach` for URLs, social, video, GitHub, RSS; run `agent-reach doctor --json` first.
    - **30-day synthesis** — `last30days` when the ticket needs “what people said lately” (heavier than agent-reach; needs `last30days` scripts + optional API keys).
    - **Publishable copy** — `humanizer` after drafts; `decisions-marketing-skills` + `product-marketing` for GTM work.
    - **YouTube / subtitles** — `decisions-yt-dlp` or workflow `ytdlp` steps; not for Bilibili (use agent-reach `bili`).
    - **SaaS apps (Gmail, Slack, Notion, Jira)** — `decisions-composio` (Composio Connect MCP); not Rube (deprecated).
12. For any normal Codex IDE/chat prompt inside a DecisionsAI project folder,
    even when it did not originate from a workflow packet, report the turn to
    DecisionsAI as a project IDE session. At the start of the prompt, call:
    `python3 ~/plugins/decisions-codex/scripts/report_decisions_event.py --event-type codex_prompt_submitted --status observed --input "<user prompt>" --thread-id "<codex thread id when known>"`
    Before the final response, call:
    `python3 ~/plugins/decisions-codex/scripts/report_decisions_event.py --event-type codex_completed --status completed --output "<short result summary>" --thread-id "<codex thread id when known>"`
    If both sides of a completed turn are only available at the end, call:
    `python3 ~/plugins/decisions-codex/scripts/report_decisions_event.py --turn-input "<user prompt>" --turn-output "<short result summary>" --thread-id "<codex thread id when known>"`
    The reporter resolves the DecisionsAI project from the current working
    directory and creates or resumes the Codex IDE session. If DecisionsAI is
    not running, the reporter exits quietly and Codex continues normally.
13. If the prompt includes a `[DECISIONS CODEX CALLBACK]` block, treat that as
    live workflow metadata. Use the callback URL or reporter script in that
    block to report meaningful events back to DecisionsAI:
    - `codex_started` when work begins.
    - `codex_prompt_submitted` after every user prompt or Codex instruction
      submission connected to this DecisionsAI run, including follow-up prompts
      typed directly in Codex.
    - `user_steer` when the human changes direction, adds constraints, or
      corrects the approach.
    - `codex_waiting` or `codex_needs_input` when blocked on a decision.
    - `codex_interrupted` when the current task is paused or superseded.
    - `codex_progress` for material implementation milestones.
    - `codex_completed` or `codex_failed` when the work finishes.
    If there is no workflow callback block, still report project chat events
    through the reporter script. It will use the current working directory to
    create or resume a DecisionsAI project IDE session:
    `python3 ~/plugins/decisions-codex/scripts/report_decisions_event.py --event-type codex_prompt_submitted --status observed --message "<what changed>" --thread-id "<codex thread id when known>"`
14. Do not keep steering only inside the Codex conversation. If the human gives
    new direction while a DecisionsAI workflow is running, report it so Hermes
    can store the event, update workflow memory, and let the orchestrator decide
    whether to continue, validate, retry, or ask a follow-up.
15. Before reporting an event, assume DecisionsAI may or may not be open. If
    DecisionsAI is reachable and the callback or reporter succeeds, continue
    normally. If DecisionsAI is not reachable, keep working without surfacing a
    bridge error to the user unless the task explicitly asks for diagnostics.
16. If Codex is working in a fresh chat without a DecisionsAI workflow callback,
    still report material starts, progress, steering, blockers, and completion.
    Inside a project folder, use the IDE session reporting in item 12. For
    activity that is not attached to a known project workflow, use ambient
    harness reporting with the current project folder:
    `python3 ~/plugins/decisions-codex/scripts/report_decisions_event.py --source ambient --harness codex --event-type codex_progress --status observed --message "<what changed>"`
    Ambient reporting is best-effort. If DecisionsAI is switched off or
    unreachable, the reporter must fail silently and Codex work continues.
