# DecisionsAI Agentic Developer Workflow Status

Date: 2026-05-12

This status note follows `agentic-developer-workflow-audit-2026-05-11.md`. It captures what has actually been tightened, what is still partial, and what should come next.

For the ongoing section-by-section ecosystem checklist, use `ecosystem-tightening-state-2026-05-12.md` as the living state file. Update it after each implementation pass so Chat, Projects, Tickets, Workflows, Computer Use, Tools, Skills, Subagents, Logs, External Integrations, UI responsiveness, TTS/audio, and Initiative intelligence stay visible.

## Review Scope

Reviewed:

- The current audit document in `audit-docs/`.
- Recent commits on `main`, especially `1673432 tightening the TTS and contexts and cleaning up bugs`.
- Current worktree state.
- Regression coverage around developer context, tickets, workflows, computer-use, chat activity, Telegram, initiative, hotkeys, and TTS.

Current repo hygiene notes:

- `audit-docs/` is still untracked.
- `assets/avatars/oracle/skin.json` has an uncommitted animation change.
- `sidecar/sidecar` is an untracked binary/artifact.

Those should be reviewed before the next commit so audit work does not get mixed with unrelated UI skin or build artifacts.

## What Is Now Banked

### Developer Context Spine

Status: partially implemented and foundational.

Done:

- Added a shared developer context assembler.
- Context includes active project, board, lane counts, current tickets, active workflow runs, runtime state, and recommended skills.
- Context is available to the main orchestrator, Initiative, WorkflowAgent step prompts, and a dedicated `developer_context` tool.
- Added regression tests for context assembly, prompt rendering, fallback behavior, and tool output.
- Added a Project -> Ticket -> Workflow spine regression proving active project, board, ticket, result-packet, and developer context are captured at workflow launch and rendered into the WorkflowAgent step prompt.

Still needed:

- Make terminal, browser, IDE, and screen evidence first-class fields in the developer context contract.
- Add an end-to-end developer loop test: project context -> ticket -> workflow -> terminal/browser evidence -> validation -> report.
- Make the orchestrator explicitly decide whether work belongs in chat, workflow, CLI, IDE, ticket board, or initiative.

### Tickets And Boards

Status: improved, but not yet product-manager quality.

Done:

- Ticket creation now uses developer context more consistently.
- Local ticket workflow dispatch preserves launch-time project/board/ticket context.
- Remote Trello/Jira creation/update/move/comment paths have tests.
- Recommended skills are appended on local and remote ticket creation paths.
- Tests cover ticket intent routing, remote ticket creation, and ticket-to-workflow briefs.
- Added a deterministic ticket draft fallback so weak/meta LLM titles such as `Instruction from user` are replaced with the actual requested work.

Still needed:

- Finish promoting ticket parsing to a first-class structured contract instead of relying on loose tool arguments.
- Add stronger tests for voice-style requests such as "make a ticket for Decisions" and "make a bug for this workflow getting stuck".
- Assert `.tickets/` writes only happen for Decisions local debug mode and remain blocked outside `DEBUG=True`.
- Add UI/browser regression for board selection, lane placement, ticket quality, and refresh consistency.
- Add remote board sync/status parity so Trello/Jira do not feel like second-class boards.

### Workflows And Computer Use

Status: materially tightened, but still not proven end to end.

Done:

- Workflow planning now normalizes vague LLM output such as "Instruction from user" into concrete titles/instructions.
- Planned steps now get validation prompts, retry counts, timeouts, stuck behavior, and type-specific execution config defaults.
- Computer-use step config includes goal, instruction, max iterations, stuck threshold, and screenshot sizing.
- Workflow runs launched from tickets preserve developer context in metadata.
- Workflow step prompts render stored developer context.
- Fixed single-step workflow prompt construction so rich workflow context is preserved whenever it exists; the raw-instruction fast path now only applies when no workflow context is present.
- Terminal workflow completion now canonicalizes the stored run result packet, writes a bounded workflow/evidence note back to the ticket, updates ticket workflow status, and records a ticket audit entry. Added regression coverage for the persisted packet, ticket note, and audit row.
- Workflow step results now classify evidence references into the stored run result packet: screenshots/media, logs/text/json/html/md, diffs/patches, and links. Result-packet context shown to later steps includes recent artifact references, so screen/log evidence is no longer trapped inside free-form step prose.
- Computer-use style step summaries now parse into `execution.action_trace` rows in the stored run result packet. The trace is deduped and included in later step context, so workflows can carry forward recent click/type/key/scroll/escalation actions as structured evidence.
- Tests cover planner normalization, computer-use preservation, stuck contracts, HTTP defaults, and developer-context rendering.

Still needed:

- Broaden structured audit summary coverage beyond terminal happy paths.
- Extend evidence persistence from artifact/action references into validation snapshots and UI-visible failure semantics.
- Add full run tests for create-from-ticket, run, pause, resume, stuck, retry, failed validation, and escalation.
- Make workflow failure statuses user-facing and precise: complete, blocked, needs user, failed validation, escalated.
- Add browser regression around workflow UI state changes and ordering.

### Chat, Tool Activity, TTS, And Hotkeys

Status: banked enough to move forward, but still needs regression pressure.

Done:

- Chat activity blocks were consolidated and made less noisy.
- Blank realtime bubbles were reduced by ignoring empty stream-finished events without an active web stream.
- Markdown/TTS cleanup was tightened for noisy model output.
- Clipboard intent routing now distinguishes consumption from read-aloud: "read/check clipboard and talk about it" loads context for the LLM, while "read from clipboard" remains direct TTS.
- Streaming TTS sentence splitting is shared across providers.
- Coqui and other realtime providers no longer drop similar-but-legitimate sentences through fuzzy duplicate suppression.
- TTS player now opens only after transport-confirmed audible playback, not provider intent.
- PTT and dictation dismiss the player when capture starts.
- Dictation is text-only and does not route through fast actions, chat agent mode, or workflows.
- Dynamic modifier-only shortcut combos are supported and validated across all shortcut rows.

Still needed:

- Browser regression for chat activity order, dedupe, refresh consistency, blank bubble prevention, and correct attachment to the assistant turn.
- Stress tests for TTS across Coqui, Kokoro, OpenAI, ElevenLabs, F5-TTS, VibeVoice, and VoxCPM with streaming and playback comparison.
- Audio hot-swap tests for input/output devices, provider changes, voice changes, speed changes, interruption, and resume.
- Settings live-reload tests proving saved hotkeys/audio settings take effect immediately.

### Skills

Status: partially wired into tickets; not fully trusted as execution aids.

Done:

- Recommended skills are available through developer context.
- Ticket creation paths can append recommended skills.
- Ticket/workflow brief tests confirm recommended skills appear in planning context.

Still needed:

- Validate skill creation before install/use.
- Add tests for skill recommendation by domain: frontend, testing, security, devops, browser, docs.
- Make skill applicability visible in chat, ticket, and workflow context.
- Prove the orchestrator actually chooses relevant skills when executing work, not only when creating tickets.

### Initiative And Telegram

Status: largely still open.

Done:

- Initiative receives the shared developer context.
- Existing tests cover policy, scheduling, planner behavior, and Telegram gates.
- Telegram response-format tests exist.

Still needed:

- Initiative activity timeline in the UI: checked, skipped, proposed, executed, failed, notified.
- Telegram delivery status, retry logging, and "why no message was sent" reporting.
- Tests for Telegram notification delivery failure, retry behavior, and initiative-to-Telegram status messages.
- Event-driven hooks for ticket changes, workflow completion, schedules, project changes, and log/error events.

### Projects And Terminal Services

Status: mostly unaudited in code, recently observed pain is likely project-process related but Decisions needs better diagnostics.

Observed:

- The Merrypak terminal service error showed Celery `ForkPoolWorker` processes exiting with `SIGSEGV`.
- Decisions appears to launch the configured project commands, but the Projects UI surfaces raw process output without classifying the failure.

Still needed:

- Project terminal health diagnostics that classify common failures such as `SIGSEGV`, missing virtualenv, wrong working directory, stale processes, port conflicts, and dependency errors.
- A user-facing project service status model: running, exited, crashed, stale, needs setup, port conflict.
- Regression tests for startup command execution, terminate all, output streaming, stale process cleanup, and classified error display.

## Highest-Value Next Work

1. Expand the Project -> Ticket -> Workflow regression harness beyond context handoff into creation quality, run status, evidence, and writeback.
2. Promote ticket creation to a structured intent/parser contract with tests.
3. Expand workflow run audit/evidence persistence into validation snapshots and UI-visible failure semantics.
4. Add project terminal health diagnostics.
5. Add Initiative/Telegram observability so the user can see checked, skipped, proposed, executed, failed, and notified cycles.
6. Add TTS/audio provider stress tests across all configured providers, including live streaming versus replay comparison and device hot-swap.
7. Add chat/activity/browser regression after the workflow/ticket spine is stable.

The current direction is right. The next phase should avoid broad new feature surface and focus on making the ecosystem behave coherently: understand context, create good work objects, execute with evidence, recover from failure, and report clearly.
