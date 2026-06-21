# Workflow CLI Session Lifecycle Design

**Date:** 2026-06-21

## Goal

Make workflow CLI sessions behave like board-scoped background sessions instead of manual connect targets. When a user opens a workflow board and enters the workflow detail surface, DecisionsAI should automatically open or reattach the board's CLI session, keep it alive while the user remains anywhere inside `Workflows/Loops`, and let idle sessions expire after three minutes once the user leaves the workflows area.

## Summary

The workflow CLI already acts as the active execution surface for board and step work, but its lifecycle is still tied to a manual `Connect` button and to the `CLI` tab being visible. That no longer matches the intended experience.

This design changes the lifecycle to a page-scoped model:

- Session scope is `workflow + board + project + backend`.
- Entering the workflow detail surface auto-starts or reattaches the session for the selected backend.
- Remaining anywhere inside `Workflows/Loops` keeps the session warm, even while viewing `Tickets` or `Loop`.
- Leaving `Workflows/Loops` stops presence heartbeats immediately.
- If the session is idle and receives no workflow-area heartbeat for three minutes, it disconnects.
- If the session is still processing work, it must not be disconnected mid-run. It can expire only after it becomes idle.
- The manual connect button is removed. Session state becomes automatic and status-driven.

## User Experience

### Entering a workflow board

When a user opens a workflow board detail view and a CLI backend is selected:

- DecisionsAI should automatically open the board-scoped CLI session in the background.
- The user should not need to click a connect button.
- Model loading still happens from backend selection, but live session startup is automatic once the workflow board surface is active.

### Moving inside the workflows area

While the user remains inside `Workflows/Loops`:

- Heartbeats continue even if the `CLI` tab is not visible.
- Switching between `Tickets`, `Loop`, and `CLI` must not drop the session.
- Switching to another workflow inside the workflows area should preserve the active board session behavior according to the selected board/backend state.

### Leaving the workflows area

If the user navigates to another top-level area such as `Ticket Boards`, `Automations`, `Snippets`, or `Actions`:

- Workflow-area heartbeats stop immediately.
- The backend session remains available temporarily.
- If the session is idle and remains unpinged for three minutes, it disconnects.

### Returning after leaving

If the user returns to the same workflow board before expiry:

- The app should reattach to the existing session and transcript.

If the session already expired:

- The app should automatically create a fresh session for the selected backend when the workflow surface becomes active again.

## Session Scope Rules

### Identity

Each auto-managed workflow CLI session is keyed by:

- `project_id`
- `board_id`
- `workflow_id`
- `backend_id`

This keeps the session aligned with the board execution context instead of only the project-wide terminal state.

### Active route

The selected backend and active run route still determine where prompts and workflow CLI steps are dispatched. Auto-session management must not override execution-route logic. It only manages the underlying live session lifecycle for the current board/backend combination.

### One running session per project

The existing rule that only one session may be actively processing work at a time still holds. Auto-managed idle sessions may exist, but only one session may be actively running work for the project at once.

## Frontend Design

### Remove manual connect control

The `Connect` / `Open` button in the workflow CLI header is removed from the primary interaction flow.

The header retains:

- backend dropdown
- key icon/modal trigger
- model selector and capability controls
- session status dot and helper text

The status UI becomes informational rather than imperative.

### Workflow-area presence tracking

The workflow frontend adds a presence controller that answers:

- is the user inside `Workflows/Loops`?
- is a workflow board detail currently active?
- which `project_id`, `board_id`, `workflow_id`, and `backend_id` should be kept warm?

This controller is independent of which sub-tab is visible.

### Auto-open behavior

When the workflow board detail surface becomes active and a board/project/backend is available:

- load backend state
- load model inventory
- check for an existing live session
- reattach if one exists
- otherwise auto-start the session

This should happen on:

- first entry into the workflow board detail surface
- workflow board switches
- backend switches
- reloads while still on the workflow surface

### Presence heartbeat

While the user remains anywhere inside `Workflows/Loops`, the frontend sends a lightweight keepalive on an interval for the active workflow board session.

The heartbeat must be suspended when:

- user leaves the workflows area
- there is no active workflow board/project/backend context

### Reattachment

On refresh or return to the board:

- read stored board CLI state
- query backend live-session state
- if a valid live session exists for the same board/backend, reattach transcript and mark ready
- if not, auto-start a new session

## Backend Design

### Live session registry extensions

The existing live session registry gains workflow presence metadata:

- `workflow_id`
- `board_id`
- `last_presence_ping_at`
- `last_activity_at`
- `expires_after_seconds`
- `pending_disconnect`

`last_activity_at` tracks real work or transcript activity.

`last_presence_ping_at` tracks whether the workflow surface is still keeping the session warm.

### Idle expiry rule

A live session can be disconnected only when all of the following are true:

- it is not currently running work
- it has not received a workflow-area presence ping for at least 180 seconds
- it belongs to a workflow board session that is no longer actively present in the workflows area

If the session is still running work:

- it remains alive
- expiry is deferred until the run completes and the session becomes idle

### Keepalive transport

Two acceptable implementations exist:

1. add a lightweight REST keepalive endpoint for workflow sessions
2. reuse the existing websocket channel for heartbeat when attached

The preferred implementation is to support both:

- websocket heartbeat updates while attached to the terminal stream
- REST keepalive fallback when the user is still in workflows but not on the `CLI` tab

That keeps the board session alive even while `Tickets` or `Loop` is open and the terminal websocket may not be foregrounded.

### Session cleanup

Backend cleanup can be triggered by:

- opportunistic checks during keepalive/state reads
- session reconnect/attach flows
- a lightweight in-process sweeper

The cleanup path must use existing backend disconnect logic and preserve any final transcript snapshot before marking the session disconnected.

## Data and Persistence

### Local UI state

The workflow board local-storage state continues to store:

- selected backend
- selected model
- selected capability values
- whether a live session was previously attached

It must no longer treat manual connect as the source of truth. Presence and backend live-session state determine reconnection.

### Live session state

No database migration is required for v1.

Live session lifecycle data can remain in the in-process live session registry used by workflow/project CLI surfaces, as long as it can:

- resolve by board/workflow/backend
- track last presence ping
- distinguish idle from running
- support safe disconnect

## Error Handling

### Backend unavailable

If a backend cannot be started automatically:

- show the existing recovery/setup guidance
- keep the workflow surface usable
- do not silently mark the session active

### Authentication required

If the selected backend requires auth:

- auto-start attempts should surface the existing auth/setup message
- key icon/modal remains available where relevant
- the frontend should not loop indefinitely trying to reconnect

### Expired session

If a session expires because the user left workflows for more than three minutes:

- surface a short status note like `Session expired while away`
- auto-start a fresh session when the user returns to the workflow board surface

## Testing Strategy

### Frontend

- workflow CLI auto-starts when entering a workflow board detail page
- switching between `Tickets`, `Loop`, and `CLI` keeps the session alive
- leaving `Workflows/Loops` stops heartbeats
- returning before expiry reattaches the same session
- returning after expiry starts a new session
- backend switch changes the warm session target
- no manual connect button remains in the workflow CLI header

### Backend

- live session records workflow/board presence metadata
- keepalive updates `last_presence_ping_at`
- idle session disconnects after 180 seconds without workflows-area presence
- running session does not disconnect during active execution
- once the running session becomes idle, expiry becomes eligible again

### Integration

- reload while on the workflow page reattaches the saved live session
- board-scoped transcript survives reattachment
- workflow-run CLI steps continue dispatching into the active board session
- leaving workflows, waiting past expiry, and returning creates a fresh idle session cleanly

## Files Expected To Change

- `distr/gui/web/static/workflows/js/workflows.js`
- `distr/gui/web/templates/workflows/workflows.html`
- `distr/gui/web/routes/settings/projects.py`
- `distr/core/project_cli_backends/live_sessions.py`
- `distr/core/project_cli_backends/registry.py`
- workflow CLI tests under `tests/core/`

## Non-Goals

- redesigning project-wide terminal behavior outside workflows
- changing execution-route semantics
- introducing database-backed persistent session storage
- supporting cross-app keepalive outside `Workflows/Loops`

## Self-Review

This design intentionally keeps the change scoped to workflow-area presence and board-backed session lifecycle. It does not alter routing logic, step execution semantics, or provider catalog truthfulness. It also avoids introducing a new persistence layer before the lifecycle rules are proven in the current in-memory session model.
