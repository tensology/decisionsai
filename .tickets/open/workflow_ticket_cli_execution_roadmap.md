# Ticket: Workflow Ticket CLI Execution Roadmap

Status: open
Priority: high
Owner: DecisionsAI
Source PRD: `audit-docs/workflow-ticket-cli-execution-prd-2026-05-20.md`

## Goal

Turn workflow ticket execution into a visible, project-aware control loop where queued tickets can be started through Codex/Cursor/Claude/Pi, monitored live, validated by the orchestrator, and reported with duration and evidence.

## Work Items

### 1. Execution Data Contract

Status: partially complete

Define the durable relationship between workflow queue ticket, workflow run, project execution session, backend process, branch, elapsed time, and final report.

Acceptance:

- Each CLI execution session is tied to ticket id, workflow id, project id, optional workflow run id, backend, model, branch name, and origin.
- The API can return active and completed execution sessions by workflow id.
- The API includes elapsed time for active sessions and duration for completed sessions.

Progress:

- Added workflow-level execution-session API: `GET /api/kanban/workflows/{workflow_id}/execution-sessions`.
- Workflow CLI sends the selected workflow id into ticket-to-CLI execution, so new sessions belong to the visible workflow instead of only the temporary audit workflow.
- Workflow execution sessions now return ticket, board, project, backend, model, status, events, elapsed time, and completed duration.

Remaining:

- Branch name/lifecycle is not yet persisted on execution sessions.
- Existing sessions created before this change may still point at their temporary audit workflow.

### 2. CLI Tab Rebuild

Status: partially complete

Replace the current thin send button with a project-aware CLI control surface.

Acceptance:

- Selecting a ticket swaps the CLI tab to that ticket’s linked project.
- The tab shows project name and folder.
- User can choose backend and model.
- Codex-specific reasoning effort/service tier controls appear when Codex is selected.
- User can edit the generated instruction before starting.
- Starting creates a visible execution session and live event trail.

Progress:

- Added ticket CLI context endpoint for resolved project, folder, backend defaults, backend statuses, and generated instruction.
- Added backend/model/Codex option controls to the Workflow CLI tab.
- Added editable instruction support before starting a CLI run.
- Updated send-to-CLI to accept backend, model, Codex reasoning effort, service tier, and edited instruction.
- Verified the served Workflow page exposes the rebuilt CLI control surface.
- Added Ticket complexity to the Workflow CLI tab.
- Changing complexity now saves to the ticket and reloads the resolved route.
- CLI context and send-to-CLI now use `resolve_ticket_cli_route(...)`, so low/medium/high complexity chooses the configured backend/model instead of silently falling back to the project default.
- Workflow Orchestrator LLM settings now use serialized model dropdowns from `/api/projects/cli-models` for low/medium/high complexity routes.
- Forced Workflow CLI form controls into dark styling so browser default white controls do not take over.

Remaining:

- True process stop/retry controls are still pending because the current one-shot CLI backend does not persist a live process handle.

### 3. Codex Execution Contract

Status: pending

Make Codex runs explicit and inspectable.

Acceptance:

- Codex runs in the selected project folder.
- The generated instruction includes ticket, workflow, project, branch policy, validation rules, and callback/session identifiers.
- The command, model, reasoning effort, and service tier are recorded on the execution session.
- Codex output streams into `ProjectExecutionEvent`.

### 4. Tickets Tab Execution Actions

Status: in progress

Add clear start actions from the workflow ticket queue.

Acceptance:

- Start selected ticket.
- Start visible filtered queue.
- Start whole queue.
- Missing-project tickets are blocked and explain why.
- QID remains global and stable when filtered.

Progress:

- Added a per-ticket `Run` action in the workflow ticket queue.
- The action calls the canonical ticket workflow endpoint: `POST /api/kanban/tickets/{ticket_id}/send-to-workflow`.
- Starting a ticket switches to Runs, starts polling, reloads workflow detail, active runs, execution sessions, and ticket queue state.

Remaining:

- Add start visible filtered queue.
- Add start whole queue.
- Add stronger preflight copy for missing workflow/project context.

### 5. Runs Tab Live Monitor

Status: in progress

Make the Runs tab show active ticket executions, not just workflow run history.

Acceptance:

- Shows multiple active ticket executions at once.
- Shows ticket, QID, board, project, backend, model, branch, status, current phase, and elapsed time.
- Groups or labels project locks when one-at-a-time-per-project is enabled.
- Per-run controls exist: stop, retry, reset, open report.
- Workflow run settings are locked while active.

Progress:

- Added a Runs-tab section for CLI / IDE execution sessions.
- Runs tab now shows workflow-owned Codex/Cursor/Claude/Pi sessions separately from workflow step runs.
- Added expandable execution session details with instruction, output, recent events, folder, timing, and direct Open ticket action.
- Runs tab auto-refreshes while workflow-owned CLI sessions are queued or running.
- One-shot CLI backends now record the command shape as an execution event without dumping the full instruction into the command field.

Remaining:

- Persist live process handles before exposing a real stop button for one-shot CLI sessions.
- Add retry/correction actions that create a new session from the failed session and ticket context.
- Add branch name/lifecycle once branch creation is implemented.

### 6. Workflow Report Tab

Status: pending

Add a report tab for completed ticket executions.

Acceptance:

- Lists completed ticket executions for the selected workflow.
- Shows QID, ticket, board, project, backend, model, branch, started, completed, duration, status, validation verdict.
- Opens the detailed ticket execution report.

### 7. Orchestrator Integration

Status: in progress

Teach the orchestrator to manage queue execution policy.

Acceptance:

- Sequential mode runs one ticket at a time.
- Per-project mode blocks only tickets for the same active project.
- Async mode starts up to configured max eligible tickets.
- Branch-per-ticket is applied before execution.
- Failed validation can trigger correction or move to QA with clear reason.

Progress:

- Ticket workflow runs already create `AutoWorkflowRun`, start `WorkflowAgent`, dispatch the first step, and pass a structured ticket workflow brief into run metadata.
- `send_to_project_cli` workflow steps now prepend the rendered ticket workflow brief and current result packet context before sending instructions to the project CLI backend.

### 8. Workflow / Initiative / Orchestrator Cleanup

Status: pending

Audit and simplify the existing workflow, initiative, orchestrator, and project CLI plumbing before layering more execution UI on top.

Acceptance:

- Identify duplicated concepts between workflows, initiatives, project CLI audit workflows, ticket audit entries, and execution sessions.
- Decide which object owns orchestration state, execution state, validation state, and user-facing report state.
- Remove or deprecate redundant audit workflow creation where `ProjectExecutionSession` is the better source of truth.
- Document which APIs are canonical for ticket-to-workflow, ticket-to-CLI, workflow-run, and orchestrator check-in.
- Clean up naming so "workflow run", "project execution session", "audit entry", and "initiative action" are not used interchangeably.
- Add regression checks for the canonical execution path once cleanup decisions are made.

## Current Important Finding

Codex is currently a one-shot `codex exec` backend. It runs in the project folder and its output is captured, but the Workflow CLI tab does not yet expose project switching, input editing, backend/model choice, branch policy, or rich live control.
