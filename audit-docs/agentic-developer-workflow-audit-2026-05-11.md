# DecisionsAI Agentic Developer Workflow Audit

Date: 2026-05-11

## North Star

DecisionsAI is moving from a voice-first desktop assistant into an agentic developer workflow engine: an orchestrator that understands the operating system, the active project, the ticket board, the IDE, the browser, the terminal, the user's intent, and the state of work over time.

The target experience is not just "the agent can run tools." The target is:

- The user says what they want in natural language.
- DecisionsAI understands whether this is chat, dictation, a ticket, a workflow, a project action, or an initiative task.
- It creates or updates the right work object.
- It can see the screen, IDE, terminal, browser, and relevant project context.
- It can delegate to a CLI, IDE, workflow agent, or sub-agent.
- It can validate progress, recover from stuck states, and report clearly.
- It can take initiative when configured, without becoming noisy or surprising.

This document banks the current state so we stop losing context between tightening passes.

## Banked In This Pass

The chat/audio/hotkey work should be treated as improved but not finished. It is good enough to bank and move the main effort toward workflows, tickets, actions, projects, skills, initiative, and developer execution.

Recently tightened:

- Hold-to-dictate defaults to enabled and uses dynamic shortcut settings.
- Modifier-only hotkey combos are supported.
- Shortcut validation is shared across all shortcut rows, not hardcoded to dictation.
- Dictation no longer routes phrases through fast actions, command handling, workflows, or chat agent mode.
- Dictation transcripts no longer show in the chat live transcription preview.
- PTT and dictation now dismiss the TTS player immediately when capture starts.
- Zero-duration non-interrupt TTS stop events no longer prematurely kill active playback.
- Kilo/free-model markdown noise is cleaned before chat/TTS output.
- Empty stream-finished events with no active web stream are ignored to reduce blank realtime bubbles.
- Chat activity blocks were improved, but still need final UX tightening and regression coverage.

Known dirty/uncommitted context at the time of this audit:

- Chat/audio/dictation stream fixes are in progress.
- `distr/core/audio/tts_handler.py` contains a separate TTS-cleaning change.
- `distr/core/data/model_recommendations.json` has large generated changes.
- `sidecar/sidecar` is untracked.

Do not treat this document as saying those files are complete or ready to ship without a final git review.

## Product Reality Check

DecisionsAI already has strong execution primitives:

- Sidecar desktop control.
- Screenshot and screen analysis tools.
- Workflow engine and WorkflowAgent.
- Actions/recording/playback.
- Local ticket boards and remote board groundwork.
- Skills registry.
- Initiative service.
- Telegram/remote control.
- Chat, TTS, STT, hotkeys, and web UI.

The gap is cohesion. The system still feels like strong parts wired together inconsistently. The next phase should focus on regression, context contracts, routing, and developer workflow correctness rather than broad new feature surface.

## Priority Roadmap

### Phase 1 - Developer Workflow Spine

Goal: make DecisionsAI understand and execute developer work as a coherent loop.

Core questions:

- What project is active?
- What ticket or objective is active?
- What files, terminal output, browser tab, IDE state, and logs matter?
- Should the work go to the main orchestrator, WorkflowAgent, CLI, IDE, or a sub-agent?
- How is progress validated?
- Where does the outcome get reported?

Work items:

- Define a single "developer work context" object used by Chat, Projects, Tickets, Workflows, Skills, and Initiative.
- Add a developer workflow regression path: create ticket, inspect project, view screen/IDE/browser, run workflow, validate, report.
- Make project context visible to the orchestrator without relying on loose prompt text.
- Make active ticket and active workflow state explicit in chat context.
- Add terminal/browser/IDE screen evidence as first-class context when requested.

### Phase 2 - Tickets And Boards

Goal: ticket creation should feel like a capable product manager/developer assistant, not raw transcription.

Current pain:

- Ticket titles and bodies can be janky.
- The agent sometimes writes "instruction from user" style content.
- Local `.tickets/` behavior is only for `DEBUG=True` and must stay that way.
- Remote boards need parity with local board intelligence.
- Tickets need recommended skills/workflows/project linkage.

Work items:

- Build a ticket intent parser with structured output:
  - project
  - board
  - lane
  - title
  - problem
  - acceptance criteria
  - evidence/context
  - recommended skills
  - recommended workflows
  - remote/local target
- Add regression tests for common voice requests:
  - "make a ticket for Decisions"
  - "create a bug for the workflow getting stuck"
  - "turn this WhatsApp thread into a ticket"
  - "make a Jira task for the current project"
- Ensure remote boards (Trello/Jira) get the same skill recommendation and project linkage as local boards.
- Keep local `.tickets/` writes behind `DEBUG=True`.

### Phase 3 - Workflows And Computer Use

Goal: workflows should be executable developer processes, not brittle instruction lists.

Current pain:

- Workflows can get stuck.
- Validation is inconsistent.
- Step parsing from tickets is not reliable enough.
- Computer-use loops need stronger observe-act-validate semantics.
- Chat reporting of workflow state is still not polished enough.

Work items:

- Add workflow regression fixtures for:
  - create from ticket
  - run from ticket
  - pause/resume
  - stuck detection
  - validation failure
  - screenshot validation
  - retry/escalate
- Add a workflow-to-ticket mapping contract.
- Require every workflow run to produce a structured audit summary.
- Make screenshot/action loops explicitly attach evidence to run state.
- Make computer-use failure semantics clear: complete, blocked, needs user, failed validation, escalated.

### Phase 4 - Actions And Screen-Aware Operating System Control

Goal: actions should behave like reliable OS automation primitives.

Current pain:

- Action playback, recording, and hotkeys still need regression coverage.
- Tool results can be too noisy or too vague.
- The agent can control the OS, but it does not always understand what it just did.

Work items:

- Build an action regression matrix:
  - record action
  - play action
  - stop action
  - bind action to workflow
  - action failure
  - action playback UI state
- Normalize action/tool result contracts:
  - success boolean
  - user-facing summary
  - evidence
  - debug details
  - next suggested step
- Ensure screen analysis, accessibility tree, and screenshots can be consumed by WorkflowAgent and sub-agents.

### Phase 5 - Skills

Goal: skills should be discoverable execution aids, not a separate shelf the orchestrator forgets exists.

Current pain:

- Skill creation needs better validation and targeting.
- Tickets should recommend relevant skills.
- The agent needs to know when to use a skill.
- Skill registry/indexing needs stronger tests.

Work items:

- Add skill recommendation to all ticket creation paths.
- Add tests that a ticket about security/test/frontend/devops recommends the right skill family.
- Validate created skills before installation/use.
- Make skill applicability visible in chat/ticket/workflow context.

### Phase 6 - Initiative And External Communication

Goal: initiative should be dependable and observable, especially with Telegram.

Current pain:

- Initiative can feel silent.
- Telegram communication is flaky.
- The user cannot always tell what initiative checked, skipped, or failed.
- Polling is not enough for an agentic OS.

Work items:

- Add Initiative activity timeline in the UI:
  - checked
  - skipped
  - proposed
  - executed
  - failed
  - notified
- Add Telegram delivery status and retry logging.
- Add tests for Telegram notification formatting and failure recovery.
- Add event-driven hooks where possible: tickets, workflow completion, schedules, project changes, logs.
- Add "why no action was taken" reporting for initiative cycles.

### Phase 7 - Chat, Audio, Settings

Goal: keep tightening the conversational shell without letting it consume the whole roadmap.

Current pain:

- Chat activity blocks are improved but need UX regression.
- Blank realtime bubbles have been reduced but need browser-level validation.
- TTS/audio device hot-swap still needs systematic testing.
- Settings changes should always take effect immediately.

Work items:

- Add Playwright/browser regression for chat streaming:
  - no blank assistant bubble
  - activity block order
  - tool dedupe
  - stream finish replacement
  - refresh consistency
- Add audio settings regression:
  - device change
  - TTS provider change
  - voice change
  - playback speed
  - hotkey settings save/reload/live refresh
- Keep chat activity concise and attached to the correct assistant turn.

## Regression Test Matrix

The next serious step is a regression harness that proves the ecosystem works end to end.

Minimum smoke flows:

1. Chat: ask a simple question, stream response, TTS on/off, refresh page.
2. Dictation: hold hotkey, speak, release, text appears in focused app only.
3. PTT: interrupt TTS, speak to agent, response appears and speaks.
4. Ticket: create local Decisions ticket in `DEBUG=True`, verify `.tickets/` content.
5. Ticket: create remote-style ticket payload with skills/workflow recommendations.
6. Project: load active project, verify project context reaches agent.
7. Workflow: create/run/pause/resume/validate/fail a workflow.
8. Computer use: observe screen, act once, observe again, validate.
9. Action: record/play/stop action and bind it to a workflow step.
10. Skill: create/validate/index/recommend skill.
11. Initiative: run a cycle, record why it did or did not act, notify Telegram if configured.
12. Telegram: send message, receive response, receive tool/workflow status, handle failure.

## Immediate Next Implementation Recommendation

Move next into Phase 1 and Phase 2 together:

1. Create a structured developer context object.
2. Add regression tests around project + ticket + workflow context assembly.
3. Fix ticket creation quality using that context.
4. Then wire workflow creation/execution to tickets using the same contract.

This is more foundational than more chat polish because it gives the orchestrator a stable understanding of developer work. Chat can keep getting polished, but the developer engine needs a backbone now.

## Implementation Log

### 2026-05-11 - Developer Context Spine Started

Implemented the first foundation slice from Phase 1:

- Added a shared read-only developer workflow context assembler.
- Context now includes current runtime, active project, active board, active lane counts, active/current tickets, active workflow runs, and recommended skills for a user request.
- Wired the context into the Initiative context bundle so initiative decisions can inspect the same project/board/workflow state as the chat orchestrator.
- Injected a compact developer workflow context into the main orchestrator system prompt so ticket, workflow, and delegation decisions start from the active project/board state.
- Added a `developer_context` tool so the agent can explicitly inspect the context it is using before creating tickets, running workflows, or delegating work.
- Added regression tests for prompt rendering, defensive fallback behavior, skill recommendations, and the new tool.

Follow-up implementation should use this shared context inside ticket creation and workflow generation so tickets stop being vague, board/project selection becomes deterministic, and workflow execution receives the same contract.

### 2026-05-11 - Ticket-to-Workflow Context Handoff

Implemented the next Phase 1/2 slice:

- Board/ticket-scoped workflow runs now capture the shared developer workflow context in run metadata when `start_workflow_run` is called.
- Workflow step prompts now render that stored context alongside ticket context and result packet context.
- Ticket-created workflow runs now preserve the launch-time view of active project, active board, active tickets, active workflow runs, and recommended skills, instead of only receiving a free-form ticket string.
- Local ticket creation now respects board execution defaults more consistently:
  - source lane for new work
  - default project
  - default workflow
  - default action
  - CLI mode as mutually exclusive with workflow/action execution
- Added regression coverage for rendering stored developer context and validated the ticket/workflow dispatch path.

Next implementation should tighten workflow generation and validation: generated workflows should include explicit validation requirements, stuck semantics, and project-aware execution defaults before the first run starts.

### 2026-05-11 - Streaming TTS Sentence Integrity

Implemented a regression pass for live TTS gaps:

- Added a shared streaming sentence splitter for TTS providers.
- Replaced provider-specific sentence extraction in Kokoro, OpenAI, ElevenLabs, Coqui, VibeVoice Realtime, F5-TTS, and VoxCPM with the shared splitter.
- Fixed a live-streaming cleaner bug where chunks starting with decimals or versions such as `2.3` were treated as markdown numbered-list markers and partially stripped.
- Added stress tests for awkward chunk boundaries, abbreviations, version numbers, decimals, punctuation without spaces, and final-buffer flush behavior.

This targets the reported gap where generated/web-playback TTS is solid, but realtime streaming can skip words or whole sentence fragments.

### 2026-05-11 - Coqui Live TTS Tightening

Followed up on the Coqui-specific live playback path:

- Removed fuzzy word-overlap duplicate suppression from Coqui, Kokoro, OpenAI, ElevenLabs, and VibeVoice Realtime. Exact duplicates and strict redundant subsets are still filtered, but similar legitimate sentences are no longer silently dropped.
- Reset Coqui's text buffer at the start of every new LLM response so stale fragments cannot leak into the next spoken response.
- Forwarded Coqui `TTSStartedFrame` and `TTSStoppedFrame` lifecycle frames to the audio transport, matching the state contract used by the streaming audio pipeline.
- Added regression coverage proving similar sentences survive duplicate filtering and Coqui forwards lifecycle frames around live audio.

This specifically addresses the Coqui report where web replay was complete but live streaming could leave out full sentences.

### 2026-05-12 - Confirmed-Audio Player Lifecycle

Investigated a report where the TTS player opened while output was silent.

- Logs showed provider-level `tts_started` events opening the player before there was transport-level evidence that audio reached the output path.
- Changed the app event handler so provider `tts_started` is treated as synthesis-pending and does not open the player.
- Added a transport-confirmed `tts_started` event emitted only after the output transport receives non-silent processed audio samples.
- Guarded non-interrupt `tts_stopped` events so a positive synthetic duration does not create/close a player session when no transport playback was confirmed.

This makes the player represent actual audible playback instead of provider intent.

### 2026-05-12 - Workflow Planning Contract Tightening

Implemented the next workflow reliability slice:

- Updated the workflow planner prompt so generated steps must include observable verification/success criteria and stuck behavior.
- Normalized vague planner output such as `Instruction from user`, `Task`, or `Step 1` into concrete titles/instructions derived from the actual user request.
- Added deterministic validation prompts, validation type, retry counts, timeouts, and stuck behavior for every planned step.
- Added type-specific execution config defaults:
  - `computer_use` receives `goal`, `instruction`, `max_iterations`, `stuck_threshold`, and screenshot sizing.
  - `run_command` receives a command and timeout.
  - `http_request` extracts URL/method where possible.
  - code/browser/CLI steps receive their instruction in config.
- Persisted validation and execution-control metadata on created workflow steps so the runner receives a stronger contract.
- Added regression tests for vague ticket/workflow phrasing, computer-use stuck contracts, HTTP config defaults, and planner preservation of `computer_use`.

This targets the janky ticket-to-workflow behavior where the system accepted weak LLM plans and then let workflows get stuck without enough validation context.
