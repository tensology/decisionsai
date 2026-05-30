# Hermes Orchestration State

## Product Direction

Hermes is the shared orchestration ledger underneath tickets, workflows, sub-agents, CLI/IDE sessions, validation, and communication channels. It is not the main user experience and it is not a replacement for workflows. Tickets remain the front door. Workflows remain the procedural policy layer. Hermes records what happened, why it happened, what route was chosen, what evidence came back, and what the system should learn from the outcome.

The user should experience this as simple ticket supervision: work comes in, a ticket is shaped, the right route is chosen, execution happens, validation decides whether it is actually complete, and the audit trail fills itself in.

## Architecture Rule

Hermes owns events and learning signals. It does not own WhatsApp, Telegram, Gmail, project credentials, Codex, Cursor, or IDE auth. Those integrations keep their own authenticated sessions. Hermes receives normalized events from them and gives the orchestrator one common history to reason from.

## Current State

- [x] Workflows can run against tickets and boards through `AutoWorkflowRun`.
- [x] A dedicated `WorkflowAgent` is created per workflow run.
- [x] CLI/IDE execution has durable `ProjectExecutionSession` and `ProjectExecutionEvent` rows.
- [x] Workflow UI is simplified to Tickets, Runs, and Rules (agent-managed supervision surface).
- [x] Runs tab includes Active, Executor, Events, and History subtabs.
- [x] Project startup commands already create Decisions-owned PTY terminals in `distr.core.terminal`.
- [x] Hermes has a setup/readiness endpoint and execution setup modal for onboarding, model allocation, executor routing, and ledger status.
- [x] Hermes has a single event ledger joining ticket, workflow, step, executor, validation, and correction evidence.
- [x] Auto-dispatch corrections re-run failed steps when run policy enables it.
- [x] Approval gates emit Hermes events and hold verified steps until continue/approval.
- [x] Channel intake events are emitted for WhatsApp/Telegram/Gmail ticket creation.
- [x] Hermes hybrid orchestrator resolves execution routes with policy baseline, board overrides, harness preferences, optional LLM advisory, and `route_decided` events.
- [x] Workflow step executor dispatches through the unified harness adapter and provisions pre_chain skills before execution.
- [x] Workflow run completion provisions post_chain skills.
- [x] Playwright validation receives project runtime URLs via Hermes runtime sessions (`PLAYWRIGHT_BASE_URL`).
- [x] Workflows UI exposes route cards, run command center, IDE handoff modal, board Hermes policy editor, skill chains, and learned-rule promote hints.
- [x] `/api/ws/workflows` pushes realtime workflow refresh events to the Workflows page.
- [x] Route override approval: when board policy requires approval, Hermes LLM override pauses the run (`waiting_kind: route_approval`), exposes Approve/Reject in the Active Run command center, and redispatches on approval via `POST /workflows/{id}/runs/{run_id}/route-approval`.
- [x] Google Agent Skills (`google/skills`) cloned to `COMPETITION/google-skills`, synced into `DecisionsAI/skills/` (19 Google Cloud skills) with registry entries and `GET /workflows/skills` catalog API.
- [x] Hermes skill transfer catalog (`distr/core/skills/catalog.py`): ticket keyword inference, LLM advisory skill list, validated push via workflow pre_chain + `RouteDecision.skills`.
- [x] Harness steer mid-flight: `POST /workflows/{id}/runs/{run_id}/steer`, Pi RPC delivery when live, queued steers + `harness_steer` Hermes events for Codex/CLI, Run command center UI.
- [x] Hermes validator LLM second pass (`distr/core/hermes_validator.py`): uses `hermes_validator_*` model settings, runs after mechanical verification, emits `validation_second_pass` events.
- [ ] The Events subtab does not yet show a ticket-centric timeline across all workflows/boards.
- [x] Hermes produces learned routing/validation rules from outcomes (board-scoped learned rules + promote-to-policy hints).
- [x] Hermes attaches project runtime context to validation snapshots and workflow active-run payloads.

## Implementation Tasks

- [x] Create a durable Hermes event model.
- [x] Create a small Hermes service for emitting and listing events.
- [x] Wire workflow run start, completion, cancellation, resume, and step result events into Hermes.
- [x] Wire project execution session create/event/complete events into Hermes.
- [x] Add a Workflows API endpoint for Hermes timeline events.
- [x] Add a Workflows Timeline tab that reads Hermes events.
- [x] Add a read-only project runtime snapshot for workflow CLI execution: active terminals, commands, PIDs, cwd, inferred local URLs, and safe restart policy.
- [x] Add durable project runtime sessions for Decisions-owned terminals.
- [x] Move executor visibility into Runs as the Executor trail and remove the standalone workflow CLI tab.
- [x] Remove the hidden workflow schedule UI from the Workflows page so scheduled/channel check-ins are handled by board/channel settings, not a stale workflow tab.
- [x] Add persisted Hermes setup settings: enabled state, orchestrator model, validation model, correction model, and memory export flag.
- [x] Add Hermes setup API with readiness checks for ledger tables, model allocation, executor routing, executor availability, ledger counts, and connected source detection.
- [x] Replace the old Workflow LLM popup with a Hermes setup/onboarding modal in the Workflows area.
- [x] Add saved Decisions Actions as a first-class workflow step type.
- [x] Add a workflow action catalog API for Hermes/workflow step selection.
- [x] Let Hermes run recorded saved Actions through playback and instruction saved Actions through the workflow agent.
- [x] Emit Hermes action lifecycle events when saved Decisions Actions start and complete.
- [x] Add channel intake events for WhatsApp/Telegram/Gmail ticket creation.
- [x] Add validation events with pass/fail evidence and missing-check reasons.
- [x] Add approval events for human gates (approval_requested, approval_granted).
- [ ] Add approval events for outbound replies.
- [x] Add a durable project runtime registry: project, command, terminal id, PID, cwd, owner, port, URL, health, started_at, last_seen_at.
- [x] Attach runtime health checks to workflow validation so Browser Use and Playwright know which URL to inspect.
- [x] Show runtime context in the workflow Runs/Timeline UI for each ticket execution.
- [x] Add Hermes learned rules table: suggested rule, scope, confidence, evidence count, enabled/disabled.
- [x] Add Rules UI for learned/manual Hermes rules.
- [x] Feed learned rules into complexity routing and validation policy.
- [x] Add tests proving orchestrator routing (policy, harness preference, approval, fallback).
- [x] Add tests for route override approval approve/reject and run_data updates (`test_run_route_approval.py`).
- [x] Add route_decided Hermes events for workflow/kanban/agent/initiative dispatch paths.
- [x] Wire auto_dispatch_corrections run policy to re-run failed steps with correction packets.
- [x] Honor hermes_enabled before emitting ledger events.
- [x] Resolve hermes_correction_* models when auto-dispatching corrections.

## UI Shape

The Workflows area is arranged around supervision:

Tickets shows the workflow queue, complexity, project/board context, run preview, and current run state.

Runs shows active and recent workflow runs, including stop/continue controls, executor trail output, event stream, and history.

Rules combines run policy (sequencing, corrections, branches) with manual agent context rules. Learned Hermes rules will become a separate surface later.

Execution setup (gear icon) is the onboarding surface for readiness, model allocation, and complexity routing.

Decisions Action steps are reusable saved Actions from the Actions area. Recorded Actions replay through the desktop playback service; instruction Actions run through the workflow agent. Hermes records both as action lifecycle events and validation should decide whether the resulting state is acceptable.

Agent Context remains editable rules that feed the orchestrator, not vague notes.

Rules will become the Hermes learning surface.

## Learning Model

Hermes learns by observing repeated outcomes, not by silently inventing behavior. It should store failed routes, successful routes, validation misses, repeated corrections, project-specific commands, executor reliability, and complexity-policy outcomes. Learned rules should be visible, editable, and disableable before they become policy.

Examples:

- Frontend tickets for a project require a browser console check and responsive screenshot.
- Auth tickets require login/logout/permission checks.
- High-complexity tickets require a stronger model and audit pass.
- A given executor is unreliable for a category and should escalate sooner.
- A board’s WhatsApp intake often lacks acceptance criteria, so clarification should happen before execution.
- A project usually exposes its React app on a known port after startup, so future validation can reuse that URL instead of guessing.

## Next Build Order

1. ~~Hermes event ledger and Timeline UI.~~
2. ~~Project runtime registry and runtime-aware execution.~~
3. ~~Validation and approval events (step gates + route override approval).~~
4. ~~Channel intake events.~~
5. ~~Learned rules storage and UI.~~
6. ~~Route-policy integration based on ticket complexity and learned rules.~~

### Recommended next (pick one at a time)

1. **Ticket-centric Events timeline** — aggregate Hermes events across boards/workflows for a selected ticket.
2. ~~**Harness steer mid-flight**~~ — done: Pi RPC + queued steer for Codex/CLI.
3. ~~**Hermes validator LLM**~~ — done: second pass after mechanical verification.
4. **Outbound reply approval events** — audit trail when Hermes sends WhatsApp/Telegram/Gmail replies.
5. **Step editor cleanup** — remove dead JS / read-only Steps subtab confusion.
6. **Safe runtime restart actions** — controlled restart from workflow command center.
