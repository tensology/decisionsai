# Workflow Ticket Execution State

Date: 2026-05-20

## Implemented Before This State File

- Workflow ticket queue supports drag/drop allocation.
- Tickets can be reordered.
- Tickets show stable global QID.
- Board/project filters exist at the bottom of the ticket queue.
- Workflow run settings exist and persist:
  - sequential or async batch
  - one run for workflow or one at a time per project
  - async ticket limit
  - create branch per ticket
- Run settings lock while the workflow has active runs.
- Workflow runs can clear audit history.
- Project execution sessions exist for CLI work.
- CLI backends include Pi, Cursor CLI, Claude Code, and Codex CLI.
- Codex backend runs `codex exec` in the linked project folder.

## Current CLI State

The Workflow CLI tab has been rebuilt into a first project-aware control surface. Selecting a queued ticket now loads its resolved project context, generated instruction, backend options, model options, and Codex-specific controls.

It still does not yet provide:

- branch visibility
- active process stop/retry controls
- live elapsed timer
- streaming output from the underlying CLI process
- clear workflow-level separation between workflow run and project execution session

## Current Backend Reality

`POST /api/kanban/tickets/{ticket_id}/send-to-cli` resolves project, builds a ticket instruction, creates an audit workflow, and calls `run_project_task`.

`run_project_task` creates a `ProjectExecutionSession`, calls the backend, appends events, and stores the output packet.

For Codex, `CodexBackend` is one-shot:

```text
codex exec <instruction>
```

Optional args are supported for backend, model, reasoning effort, service tier, and edited instruction from the Workflow CLI tab.

## Next Priority

Build the CLI tab rebuild first, because it clarifies the handoff. After that, build the Runs tab live monitor and Report tab on top of the same execution session data.

## 2026-05-20 CLI Rebuild Start

Started the CLI tab rebuild.

Added:

- `GET /api/kanban/tickets/{ticket_id}/cli-context`
- backend/model/Codex options in the Workflow CLI tab
- editable generated instruction textarea
- `POST /api/kanban/tickets/{ticket_id}/send-to-cli` body support for backend, model, Codex reasoning effort, service tier, and instruction override

Still missing:

- live polling/streaming display for an active execution session
- visible branch name and branch lifecycle
- per-session stop/retry controls
- workflow-level active/completed execution queries

Verified:

- Python syntax: `py_compile distr/gui/web/routes/kanban.py`
- JavaScript syntax: `node --check distr/gui/web/static/workflows/js/workflows.js`
- Served workflow HTML contains the new CLI controls
- Browser check confirmed the CLI tab opens and exposes the Start CLI run control surface

## 2026-05-20 Execution Session Visibility

Added workflow-level CLI / IDE execution session visibility.

Changed:

- New API: `GET /api/kanban/workflows/{workflow_id}/execution-sessions`
- Workflow CLI sends `workflow_id` when starting ticket execution.
- New execution sessions now belong to the visible workflow, while the temporary audit workflow remains evidence.
- Runs tab now has a CLI / IDE execution sessions section.
- Execution session rows expose ticket, board, project, backend, model, origin, status, latest event, elapsed time, duration, and error.
- Workflow ticket queue and CLI ticket list can identify active CLI sessions and lock/remove badges accordingly.

Still missing:

- Streaming process output.
- Stop/retry controls per execution session.
- Branch creation and branch name visibility.
- A proper completed execution report view.

## 2026-05-20 Execution Session Detail Pass

Added the next Runs-tab slice.

Changed:

- CLI / IDE execution session rows can expand into a session detail/report.
- Expanded sessions show timing, project folder, instruction, captured output, recent events, and errors.
- Each session row can open its linked ticket modal directly.
- The Workflow Runs tab auto-refreshes execution sessions while any session is queued or running.
- One-shot CLI backends now emit a `command_start` event showing the command shape and working directory, while keeping the full instruction in the instruction packet instead of duplicating it in the command display.

Still missing:

- Real stop control for Codex/Cursor/Claude one-shot CLI sessions. This needs persisted process handles before the UI should expose it.
- Retry/correction action.
- Branch-per-ticket implementation and branch display.
- Dedicated completed execution report tab.

## 2026-05-20 Ticket Run Wiring

Clarified and wired the ticket-to-workflow run path.

Current behavior:

- A queued workflow ticket can be started from the Tickets tab with `Run`.
- The UI calls `POST /api/kanban/tickets/{ticket_id}/send-to-workflow`.
- The backend resolves the workflow from the explicit workflow id, ticket link, or board default.
- The backend builds a structured ticket workflow brief and stores it in `AutoWorkflowRun.run_data`.
- `start_workflow_run` creates the run, starts `WorkflowAgent`, records the ticket audit entry, marks the ticket running, and dispatches the first step.
- If a step is `send_to_project_cli`, the project CLI instruction now receives the rendered ticket workflow brief plus the current result packet context before the step instruction.

Still missing:

- Start visible filtered queue.
- Start whole queue.
- Proper retry/correction from failed CLI sessions.
- Branch-per-ticket execution.

## 2026-05-20 Complexity Routing Fix

Fixed the Workflow CLI route so ticket complexity actually controls the implementation backend/model.

Changed:

- `GET /api/kanban/tickets/{ticket_id}/cli-context` now resolves backend/model through `resolve_ticket_cli_route(...)` instead of falling back to the project default coding backend.
- `POST /api/kanban/tickets/{ticket_id}/send-to-cli` now uses the same complexity route when the UI has not explicitly overridden backend/model/Codex options.
- Workflow CLI tab now exposes Ticket complexity beside Project/Folder/Backend/Model.
- Changing Ticket complexity in the Workflow CLI tab saves to the ticket, clears the stale CLI context, and reloads the resolved route.
- Workflow Orchestrator LLM settings now use real serialized model dropdowns from `/api/projects/cli-models` for low/medium/high complexity routing instead of free-text model inputs.
- Workflow CLI form fields were forced into dark form styling so browser default white controls do not take over.

Verified:

- Python syntax: `py_compile distr/gui/web/routes/kanban.py`
- JavaScript syntax: `node --check distr/gui/web/static/workflows/js/workflows.js`
- Served workflow HTML contains the Ticket complexity control and route model loader.
- Browser check confirmed `#wf-cli-ticket-complexity` exists with low/medium/high options and `#wf-cli-model` renders dark with white text.
