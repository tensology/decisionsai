# Workflow Ticket CLI Execution PRD

Date: 2026-05-20

## Product Intent

The workflow area should let a user take queued tickets, route each ticket into the correct linked project, execute the work through a chosen coding backend such as Codex, Cursor, Claude Code, or Pi, and then see the live processing state and final report without guessing where the work went.

The CLI tab should not be a generic push button. It should be the visible execution cockpit for the selected workflow ticket and its linked project.

## Current System

Tickets can already be linked to workflows and projects. A workflow queue can show tickets, QID, priority, complexity, status, and board/project filters. A ticket can be sent to CLI through `POST /api/kanban/tickets/{ticket_id}/send-to-cli`.

The current CLI backend path is:

1. The ticket resolves its project from `ticket.linked_project_id`, falling back to the board default project.
2. The project resolves its folder from `Project.folder_location`.
3. Decisions builds a ticket instruction packet with `build_kanban_ticket_cli_instruction`.
4. Decisions creates an audit workflow/step for a legacy trail.
5. Decisions calls `run_project_task`.
6. `run_project_task` creates a `ProjectExecutionSession`.
7. The selected backend runs in the project folder.
8. Backend events are appended as `ProjectExecutionEvent` rows.
9. The final backend output is saved back onto the execution session.

For Codex specifically, the backend is currently `CodexBackend` in `distr/core/project_cli_backends/registry.py`. It runs:

```text
codex exec [--model <model>] [-c model_reasoning_effort="..."] [-c service_tier="..."] <instruction>
```

The process runs with `cwd` set to the linked project folder. This means Codex sees the project files because Decisions starts Codex inside the project directory. It does not currently create a rich ongoing chat inside the web UI. It is a one-shot command execution whose stdout/stderr stream is captured as events.

## Main Gap

The current Workflow CLI tab does not expose enough control. The user cannot choose backend, choose model, choose Codex reasoning effort, see the project folder clearly, type additional instruction, see whether this is a fresh Codex execution or continuing work, or understand the exact run/session relationship.

The tab also does not feel project-bound. If the selected ticket belongs to a different board/project, the CLI area should visibly swap to that project context.

## Target CLI Experience

When a ticket is selected in the workflow queue, the CLI tab should show:

- Ticket title, QID, workflow, board, project, project folder, and complexity.
- The selected backend for this run: Codex, Cursor, Claude Code, Pi, or auto.
- Model selector or model override.
- Codex-specific options when Codex is selected: reasoning effort and service tier.
- Branch policy from workflow run settings, including proposed branch name.
- A prompt/instruction box seeded with the generated ticket brief, editable before sending.
- A clear start button: `Start Codex run`, `Start Cursor run`, etc.
- A live execution timeline for that ticket.
- The current `ProjectExecutionSession` id and linked workflow run id if applicable.
- A final output/report panel when the execution ends.

The user should be able to understand: “This ticket is going to Codex, in this project folder, using this model, on this branch, with this instruction.”

## Codex Flow

The Codex flow should be:

1. User selects a workflow ticket.
2. Decisions resolves the linked project and project folder.
3. Decisions creates or reuses a ticket execution run record.
4. Decisions creates a branch name if branch-per-ticket is enabled.
5. Decisions creates a `ProjectExecutionSession` for the ticket.
6. Decisions starts `codex exec` in the linked project folder.
7. Codex receives the ticket brief, workflow context, validation rules, branch policy, and callback/session identifiers.
8. Decisions streams Codex output into the CLI tab as execution events.
9. Decisions marks the execution session completed or failed.
10. The orchestrator validates the result against the ticket and workflow criteria.
11. The ticket report is updated with duration, backend, model, branch, output, validation verdict, and evidence.

Codex does not need to be the orchestrator. Codex is the executor. Decisions remains the orchestrator and validator.

## Required Architecture

The existing `ProjectExecutionSession` and `ProjectExecutionEvent` models are the correct foundation. They should become the durable source of truth for CLI execution display.

What is missing is a stronger relationship between:

- Workflow queue ticket
- Workflow run
- Project execution session
- CLI backend process
- Ticket report

The system needs a ticket execution coordinator that can start a backend run, attach it to a workflow run or batch, stream events, track elapsed time, and notify the orchestrator when the executor returns.

## UI Scope

### Tickets Tab

The Tickets tab remains the queue manager. It should show QID, ticket, project badge, complexity, current execution status, elapsed time when active, and whether the ticket is blocked due to missing project.

It should support:

- Start selected ticket.
- Start filtered queue.
- Start whole queue.
- Show project lock or waiting reason.
- Keep global QID stable while filtering.

### Runs Tab

The Runs tab should become the live execution monitor.

It should show:

- Active ticket runs grouped by project or workflow.
- Each active ticket’s elapsed time.
- Backend, model, project, branch, current step, current phase.
- Per-run controls: stop, retry, reset, open report.
- Workflow-level run settings locked while active.

### CLI Tab

The CLI tab should become project-aware and ticket-aware.

It should show:

- Selected ticket and linked project.
- Project folder.
- Backend selector.
- Model selector.
- Codex reasoning effort/service tier when Codex is selected.
- Editable generated instruction.
- Start backend run button.
- Live event stream.
- Final output and error state.

### Report Tab

Add a workflow-level Report tab for completed ticket executions.

It should show:

- QID
- Ticket
- Board
- Project
- Backend
- Model
- Branch
- Started time
- Completed time
- Duration
- Status
- Final validation verdict
- Evidence/output link

## Orchestrator Rules

The orchestrator owns execution policy.

If workflow settings say one at a time per project, the orchestrator may run multiple tickets at once only across different projects. If async mode is enabled with a limit of three, the orchestrator starts up to three eligible ticket executions, respecting project locks. If branch-per-ticket is enabled, the orchestrator creates or instructs the executor to create the branch before work begins.

The executor returns output. The orchestrator decides whether it is complete.

## Completion Criteria

This feature is complete when a user can select a workflow, see its queued tickets, start tickets through Codex or another backend, watch multiple ticket executions live, see elapsed time, understand which project each execution belongs to, and review completed ticket reports with duration and validation result.

