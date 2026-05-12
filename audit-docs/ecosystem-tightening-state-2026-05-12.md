# DecisionsAI Ecosystem Tightening State

Date: 2026-05-12

Purpose: this is the living state file for the DecisionsAI tightening pass. Keep it updated as work lands so the audit does not drift back into memory, chat fragments, or half-remembered implementation notes.

The current product direction is not broad feature expansion. The focus is tightening the whole ecosystem until the orchestrator can understand the user, the active project, the ticket board, workflows, tools, skills, initiative, audio, and external communication as one coherent operating system.

## Status Key

- `Banked`: good enough to preserve and build on.
- `Partial`: meaningful work landed, but the experience is not yet reliable enough.
- `Open`: still needs a first serious implementation pass.
- `Regression Needed`: behavior may be improved, but must be proven by tests or browser/manual harness.

## Current Priority Order

1. Project -> Ticket -> Workflow regression spine. First guardrail landed; continue expanding.
2. Ticket creation intelligence and board targeting.
3. Workflow audit/evidence model. Terminal packet/writeback, evidence artifacts, and first action-trace guardrails landed; validation snapshots still open.
4. Initiative/Telegram observability.
5. TTS/audio provider stress and device switching.
6. Chat/tool activity browser regression.
7. Skills/subagent/tool contract tightening.

This order can change when a bug blocks real use, but the state file should be updated whenever that happens.

## 1. Chat / Main Orchestrator

Status: Partial, Regression Needed

What it covers:

- Intent interpretation.
- Tool selection.
- Memory/context use.
- Conversational response quality.
- Markdown/noise cleanup.
- Tool activity shown in chat.
- TTS-facing response formatting.

What is banked:

- Developer context is injected into the main orchestrator prompt.
- Single-step workflows now keep rich workflow/ticket/developer context when context exists, instead of falling back to raw instruction-only prompts.
- Clipboard commands now distinguish context consumption from direct TTS readout: "read/check clipboard and talk about it" loads clipboard content into the conversation, while "read from clipboard" or "read it out loud" remains a direct TTS path.
- Markdown cleanup was tightened for noisy/free model output.
- Empty stream-finished events without active streams are ignored to reduce blank realtime bubbles.
- Chat activity blocks were consolidated and made less noisy.
- Dictation no longer routes through chat/agent mode.

Known gaps:

- Tool activity still needs browser-level ordering and dedupe regression.
- The orchestrator still needs a clearer routing decision: chat vs dictation vs ticket vs workflow vs action vs initiative.
- Tool result summaries need consistent user-facing wording instead of raw method/function detail.
- TTS responses and text responses need the same cleaned content contract.

Next actions:

- Add browser regression for no blank assistant bubble, correct activity ordering, tool dedupe, refresh consistency, and assistant-turn attachment.
- Add an orchestrator routing contract and tests for common voice phrases.
- Normalize tool result packets into user summary, evidence, debug details, and next step.

## 2. Projects

Status: Partial

What it covers:

- Active project loading.
- Project folders.
- Logs.
- `.tickets`.
- RAG/context.
- Startup commands.
- Terminal service state.
- Exposure of project context to the agent.

What is banked:

- Developer context includes active project data.
- Workflow runs launched from tickets preserve launch-time project context.
- Regression now proves active project context reaches a ticket-launched workflow prompt.
- Project terminal output can be viewed in the UI.

Known gaps:

- Terminal, browser, IDE, and log evidence are not yet first-class developer context fields.
- Project terminal failures are surfaced as raw process output.
- No classified project service states yet: running, crashed, stale, missing venv, port conflict, wrong working directory, setup needed.
- `.tickets/` behavior still needs hard regression around `DEBUG=True`.

Next actions:

- Add project service diagnostics for common terminal/startup failures.
- Add active project context tests that include terminal/log/browser/IDE evidence placeholders.
- Add `.tickets/` debug-mode regression.

## 3. Ticket Boards

Status: Partial

What it covers:

- Ticket creation.
- Board/lane selection.
- Local and remote boards.
- Project/workflow linking.
- Ticket quality.
- UI display.
- Ticket lifecycle.

What is banked:

- Local and remote ticket creation paths can include recommended skills.
- Trello/Jira create/update/move/comment paths have coverage.
- Ticket-created workflows preserve developer context.
- Ticket/workflow brief tests confirm recommended skills reach planning context.
- Regression now proves ticket title, description, checklist, project link, and run packet context are rendered into the workflow step prompt.
- Ticket summarisation now has a deterministic draft fallback that replaces weak/meta LLM titles such as `Instruction from user`, `Task`, or `Create a ticket` with the actual work requested.

Known gaps:

- Ticket creation is not yet a full first-class structured intent pipeline, but the deterministic draft object has started.
- Voice-style requests can still produce weak tickets unless the LLM behaves.
- Remote board parity is not complete at the UI/state/lifecycle level.
- Board/lane targeting needs stronger regression.
- Ticket quality needs acceptance criteria/evidence/project/workflow checks.

Next actions:

- Expand the ticket draft/parser contract to include project, board, lane, title, problem, acceptance criteria, evidence, skills, workflows, and local/remote target.
- Add voice-style ticket tests.
- Add UI/browser regression for create, refresh, lane placement, and remote board status.

## 4. Workflow Engine

Status: Partial

What it covers:

- Step parsing.
- Validation.
- Execution.
- Stuck detection.
- Routing.
- Verification.
- Retry/resume.
- Audit trails.

What is banked:

- Workflow planner normalizes vague step titles/instructions.
- Planned steps now get validation prompt/type, retry count, timeout, stuck behavior, and type-specific config defaults.
- Ticket-launched runs preserve developer context in metadata.
- Step prompts render stored developer context.
- Added a Project -> Ticket -> Workflow spine regression that starts a workflow run, verifies developer context metadata, and confirms ticket/developer/result-packet context appears in the WorkflowAgent prompt.
- Fixed the single-step prompt fast path so it only returns a raw instruction when there is no workflow context, prior result context, rules, or continuation input.
- Terminal workflow completion now canonicalizes the stored run `result_packet`, appends workflow evidence to the ticket, updates ticket workflow status, and writes a ticket audit entry. Regression coverage proves the persisted packet, ticket note, and audit row.
- Workflow step results now extract evidence references into the canonical run `result_packet`: screenshots/media, logs/text/json/html/md, diffs/patches, and links. Later workflow steps see recent artifact references in result-packet context instead of losing screen/log evidence in prose-only summaries.
- Computer-use style step summaries now parse into `execution.action_trace` rows in the canonical run `result_packet`, with dedupe and context propagation. Later steps can see recent actions such as click/type/key/scroll/escalation instead of relying only on a prose transcript.
- Tests cover planner normalization and computer-use config.

Known gaps:

- Run audit summaries now have terminal persistence coverage, but need broader coverage for non-happy paths.
- Screenshot/log/link/patch evidence references and first action traces from step output are now attached to run state, but validation snapshots and UI rendering still need a structured contract.
- Pause/resume/stuck/retry/failure paths need end-to-end tests.
- Failure statuses need clearer user-facing semantics.

Next actions:

- Expand workflow run audit/evidence persistence from artifact/action references into validation snapshots and user-facing failure semantics.
- Add create-from-ticket, run, pause, resume, stuck, retry, validation-fail, and escalate tests.
- Make workflow UI state changes browser-tested.

## 5. Computer Use

Status: Partial

What it covers:

- Screenshot/action loops.
- Sidecar reliability.
- Vision model behavior.
- Validation after action.
- Failure semantics.

What is banked:

- Agent-instruction steps can switch into computer-use mode.
- First-class `computer_use` step planning is preserved.
- Computer-use config gets goal, instruction, max iterations, stuck threshold, and screenshot sizing.
- WorkflowAgent has observe-act-screenshot behavior for physical actions.

Known gaps:

- Evidence attachment is not yet complete.
- Vision-action loop needs more real E2E tests.
- Failure states need to be persisted and shown clearly.
- Subagents/workflow agents need reliable access to current screen evidence.

Next actions:

- Add action/screenshot evidence packets to workflow run state.
- Add failure semantic tests: complete, blocked, needs user, failed validation, escalated.
- Add real screen loop regression using controlled UI fixtures.

## 6. Tools

Status: Partial

What it covers:

- Tool discoverability.
- Health.
- Wiring.
- Structured success/failure.
- User-facing summaries.
- Debug detail separation.

What is banked:

- Tool activity events are shown in chat more cleanly than before.
- Developer context tool exists.
- Computer-use/tool audit tests exist in pockets.
- Clipboard action routing now has regression coverage for consume-vs-speak intent, avoiding accidental full clipboard readouts when the user wants to discuss clipboard contents.

Known gaps:

- Tools do not all return a consistent result contract.
- Tool results can still be too vague or too noisy.
- The agent may not know when a tool failed versus when it partially succeeded.
- Clipboard/write-style tool coverage has started, but still needs UI/chat rendering checks for how clipboard ingestion appears in the web chat.

Next actions:

- Define a shared tool result envelope: success, summary, evidence, debug details, next step.
- Add tests for common tool failures and partial successes.
- Make chat render the summary/evidence by default, with debug details hidden or collapsed.

## 7. Skills

Status: Partial

What it covers:

- Skill creation.
- Validation.
- Install/push behavior.
- Project targeting.
- Registry/indexing.
- Whether the orchestrator knows when to use skills.

What is banked:

- Recommended skills are available through developer context.
- Ticket creation paths can append recommended skills.
- Ticket/workflow brief tests confirm skills reach workflow planning context.

Known gaps:

- Created skills need validation before install/use.
- Skill recommendation needs stronger domain tests.
- The orchestrator must prove it actually uses skills during execution, not only lists them.
- Skill registry/indexing and project targeting need regression.

Next actions:

- Add skill creation validation tests.
- Add skill recommendation tests for frontend, testing, security, devops, browser, docs, and workflow validation.
- Add execution tests where a relevant skill is selected and included in the workflow/subagent prompt.

## 8. Subagents / WorkflowAgent

Status: Partial

What it covers:

- Context inheritance.
- Tool availability.
- Independent operation.
- Failure reporting.
- Delegation.

What is banked:

- WorkflowAgent can receive stored developer context through workflow step prompts.
- Computer-use mode has tighter system instructions and iteration caps.
- WorkflowAgent has screenshot-after-action behavior in computer-use mode.

Known gaps:

- No true concurrent worker pool or supervisor yet.
- Subagents do not have a full shared work queue.
- Failure reporting needs to flow back into tickets/workflows/chat consistently.
- Tool access and screen context inheritance need explicit tests.

Next actions:

- Define subagent context packet.
- Add tests proving WorkflowAgent receives active project, ticket, workflow, skills, and evidence context.
- Add failure writeback from WorkflowAgent to workflow run and ticket.

## 9. Logs / Debugging

Status: Open

What it covers:

- Agent log inspection.
- Failure summarization.
- Evidence-based follow-up.
- User-facing diagnostics.

What is banked:

- Logs exist and have been manually useful during recent debugging.
- Project terminal output appears in the Projects UI.

Known gaps:

- The agent cannot reliably inspect its own recent logs as part of a structured debugging loop.
- Log summaries are not turned into ticket/workflow evidence automatically.
- Project service crashes are not classified.
- User-facing error messages often expose raw logs instead of diagnosis.

Next actions:

- Add log inspection tool/context for recent app, agent, workflow, TTS, Telegram, and project logs.
- Add structured log summary output: likely cause, evidence, affected subsystem, suggested next action.
- Add project terminal crash classification.

## 10. Calendar / External Integrations

Status: Open

What it covers:

- Timezones.
- Recurrence.
- Attendees.
- Availability.
- Create/update/delete.
- Auth state.
- Real-world scheduling correctness.
- External board/service parity.

What is banked:

- Telegram response-format and token/link tests exist.
- Remote ticket board integration has Trello/Jira coverage in the ticket layer.

Known gaps:

- Calendar correctness has not had a serious audit in this pass.
- Auth state and failure recovery need clearer UI and agent context.
- External integrations need delivery/status reporting.
- Remote boards need lifecycle parity with local boards.

Next actions:

- Audit calendar actions separately.
- Add integration health state to developer/context or settings context.
- Add tests for timezone, recurrence, attendee, update/delete, and auth failure behavior.

## 11. UI Responsiveness

Status: Partial, Regression Needed

What it covers:

- Event streams.
- Stale board/workflow state.
- WebSocket/SSE fragmentation.
- Loading states.
- Hard UI errors.
- Refresh consistency.

What is banked:

- Some chat WebSocket/blank-stream fixes landed.
- Chat activity merge/order tests exist.
- Existing UI smoke tests exist in pockets.

Known gaps:

- Need browser-level regression across Chat, Projects, Ticket Boards, Workflows, Skills, Actions, Initiative, Settings, and Audio.
- Stale state and refresh consistency need explicit checks.
- Loading/error states are not consistently tested.
- Tool/workflow activity can still feel detached from the assistant turn.

Next actions:

- Create a UI smoke matrix covering the core tabs.
- Add chat activity browser regression.
- Add board/workflow refresh tests.
- Add settings save/live-reload tests.

## 12. TTS / Audio Devices

Status: Partial, Regression Needed

What it covers:

- TTS providers.
- Playback lifecycle.
- Device hot-swap.
- Interruption handling.
- Output switching.
- Streaming gaps/cut-outs.
- PTT/dictation interaction.

What is banked:

- Shared streaming sentence splitter now covers multiple providers.
- Fuzzy duplicate suppression was removed from major realtime providers so similar legitimate sentences are not dropped.
- Coqui buffers reset per response and forwards lifecycle frames.
- TTS player opens only after transport-confirmed audible playback.
- PTT/dictation dismiss the player when capture starts.
- Zero-duration and synthetic stop handling were tightened.
- Dictation is text-only and does not trigger agent actions.
- Clipboard TTS is now reserved for explicit read-aloud commands, reducing accidental long clipboard playback.

Known gaps:

- Streaming TTS still needs stress testing across all providers, not only Coqui/Kokoro.
- Playback from saved web UI audio is more reliable than live streaming, so streaming and replay must be compared.
- Device hot-swap behavior needs regression.
- Provider/voice/speed changes need live reload checks.
- TTS interruption by PTT/dictation needs long-run testing.
- Oracle/player glow/state can still get stuck if event ordering regresses.

Next actions:

- Build provider stress tests for Coqui, Kokoro, OpenAI, ElevenLabs, F5-TTS, VibeVoice, and VoxCPM.
- Compare live-stream transcript chunks against final replay text/audio events.
- Add output-device switching tests during playback and between turns.
- Add player/oracle lifecycle tests for start, confirmed audio, interrupt, stop, silent provider start, and provider failure.

## 13. Initiative Intelligence

Status: Open to Partial

What it covers:

- When initiative checks.
- What it notices.
- Why it acts or does not act.
- How it communicates.
- Telegram delivery.
- Event-driven triggers.
- User control/noise boundaries.

What is banked:

- Initiative receives shared developer context.
- Initiative policy/planner/scheduler tests exist.
- Telegram gating tests exist.

Known gaps:

- Initiative still feels silent because the user cannot see each cycle's reasoning/status.
- Telegram delivery is not observable enough.
- Initiative is mostly timer/poll based rather than event driven.
- There is no strong UI timeline for checked, skipped, proposed, executed, failed, notified.
- "Why no action was taken" is not exposed.

Next actions:

- Add Initiative activity timeline and persisted cycle records.
- Add Telegram delivery status and retry records.
- Add "why skipped" output for each cycle.
- Add event hooks for tickets, workflow completion/failure, project terminal errors, calendar events, and log events.
- Add user-facing controls for initiative scope and communication channel.

## Immediate Next Implementation Slice

The next slice should not ignore TTS/audio or Initiative. It should split into two parallel tracks:

1. Developer execution spine:
   - Expand the Project -> Ticket -> Workflow regression harness beyond context handoff into creation quality, run status, evidence, and writeback.
   - Ticket intent parser.
   - Workflow audit/evidence output.

2. Reliability shell:
   - TTS/audio provider stress plan and first tests.
   - Initiative/Telegram activity timeline design.
   - Chat/tool activity browser regression.

The developer spine makes the agent useful for real work. The reliability shell keeps the experience trustworthy while that spine gets stronger.
