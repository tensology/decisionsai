# Workflow Control Room Implementation State

Date: 2026-05-18
Status: In progress

## Product Intent

Tickets and boards are the front door. Workflows are backstage infrastructure managed by the agent. The workflows web UI should become a control room for inspecting runs, tuning rules, debugging failures, and understanding agent decisions. It should not push normal users into building process diagrams as the main experience.

The orchestrator should manage work through this loop:

Intake -> ticket shaping -> process/workflow selection -> project execution session -> observation -> validation -> retry/audit/escalation/complete -> ticket update.

## Execution Promise

This file tracks every concrete build step promised in the PRD direction. Each implementation pass must update this file with what changed, what was verified, and what remains.

## Current Slice

- [x] PRD created and refined around ticket-first orchestration.
- [x] Workflow name editing added to the workflow detail header.
- [x] Redundant workflow context-menu `Open` action removed.
- [x] Example oversized WhatsApp workflow reworked into an internal intake template in local settings DB.
- [x] Workflows web UI reframed as backstage control room.
- [x] Execution session data model.
- [ ] Execution session web UI.
- [x] Project-side ticket/result packet contract, first pass.
- [x] Local DecisionsAI Codex plugin installed and backend availability checked.
- [ ] Development execution driving stack implementation.
- [ ] Retry/audit orchestration loop.

## First UI Slice Plan

The first implementation slice will keep the existing workflow editor intact, but change the user-facing posture:

- [x] Add a control-room overview area above workflow details.
- [x] Rename builder-first copy so workflows are described as agent-managed backstage processes.
- [x] Make `Runs` the first visible tab, while keeping `Steps` plainly accessible.
- [x] Add explicit control-room summary for run status, validation steps, gates, and next action.

## Verification Log

- `node --check distr/gui/web/static/workflows/js/workflows.js` passed.
- `curl http://127.0.0.1:8765/workflows/` confirms rendered server HTML includes `Workflow Control Room`, `This is the backstage control room`, `Steps`, and `wf-control-overview`.
- `curl http://127.0.0.1:8765/workflows/static/js/workflows.js` confirms the served JS includes `renderControlOverview`.
- `python3 -m py_compile distr/core/db/kanban.py distr/core/kanban/project_execution.py distr/core/project_cli_backends/registry.py distr/gui/web/routes/kanban.py` passed.
- `node --check distr/gui/web/static/kanban/js/kanban.js` passed.
- `node --check distr/gui/web/static/kanban/js/kanban_ticket.js` passed.
- `PYTHONPATH=DecisionsAI python3 - <<'PY' ... ensure_project_execution_tables()` created/verified the execution session tables.
- Removed the visible ticket Report tab `Project execution sessions` block because it duplicated the audit trail and made the UI heavier instead of simpler.
- `curl http://127.0.0.1:8765/kanban/static/js/kanban.js` confirms the served JS no longer includes the extra execution-session renderer.
- Restarted the local DecisionsAI web process through `bin/decisions.sh`; `curl http://127.0.0.1:8765/api/kanban/tickets/0/execution-sessions` now returns route-level `Ticket not found`, confirming the new API route is loaded.
- `node --check distr/gui/web/static/workflows/js/workflows.js` passed after adding the workflow board-ticket side panel.
- `curl http://127.0.0.1:8765/workflows/` confirms the Workflows page serves `wf-board-select`, `wf-board-ticket-list`, and `Board Tickets`.
- `curl http://127.0.0.1:8765/api/kanban/boards` and `/api/kanban/external-boards` confirm the panel has local, Trello, and Jira board sources available.

## Completed In This Pass

- Added `wf-control-overview` to the workflow detail layout.
- Added `renderControlOverview(data)` to summarize backstage process posture, validation step count, approval gates, latest run state, and recommended next action.
- Changed the default visible tab from step editing to `Runs`.
- Restored the workflow tab label to `Steps`; `Advanced Editor` was too confusing and hid the edit path.
- Rewrote empty state and create/plan copy so the page describes agent-managed processes rather than manual workflow construction.
- Installed the local DecisionsAI Codex plugin to `~/plugins/decisions-codex` and registered it in `~/.agents/plugins/marketplace.json`.
- Fixed the project `codex-sync` route so it defines and returns `plugin_install`.
- Added `ProjectExecutionSession` and `ProjectExecutionEvent` models to store Decisions-owned execution history around Codex/Cursor/Pi/CLI work.
- Added `distr/core/kanban/project_execution.py` helpers for creating sessions, appending events, completing sessions, and listing ticket history.
- Wrapped `run_project_task(...)` so every project backend run now creates a durable session with route/backend/model/complexity, input packet, streamed events, output packet, and final status.
- Updated the direct ticket `send-to-cli` route to use the shared project backend runner instead of hard-coding Pi RPC.
- Added `GET /api/kanban/tickets/{ticket_id}/execution-sessions`.
- Reverted the visible `Project execution sessions` area from the ticket Report tab; execution sessions remain backstage data for orchestration and validation.
- Added a middle Workflows side panel for board context: select any local, Trello, or Jira board and view tickets grouped under each board column.

## Next Implementation Slice

The next slice should turn execution sessions into the validation control loop:

- classify the ticket maneuver before dispatch
- attach expected checks to the execution session
- decide the single user-facing status/result summary that should represent execution plus validation, instead of adding another visible trail
- move returned sessions into `validating`
- store validation evidence and failure reasons
- let the orchestrator send a focused correction back to the same project backend when validation fails
- move genuinely complete tickets forward with evidence, not just because a backend returned success

## Codex Integration State

- Codex CLI is available at `/opt/homebrew/bin/codex`.
- Codex version check returned `codex-cli 0.131.0-alpha.9`.
- DecisionsAI Codex plugin is installed locally at `~/plugins/decisions-codex`.
- Local marketplace contains the `decisions-codex` plugin entry.
- Current backend path uses `codex exec` through backend id `codex`.
- Current plugin role is behavior/context: it tells Codex how to treat DecisionsAI tickets and return structured result packets.
- Codex/project backend runs now tie into durable `project_execution_sessions` and `project_execution_events`.
- Remaining work: parse returned result packets into validation tasks and agent decisions.

## Notes

The repository already has extensive unrelated local changes. This work must only touch the workflow PRD/state files and workflow web UI files unless a later slice explicitly requires backend schema/routes.
