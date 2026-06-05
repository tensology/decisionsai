# Harness Goal Closure Audit - 2026-06-02

## Summary

The harness goal is now operationally proved for the first DecisionsAI workflow baseline set:

- Automatic UI-heavy routing selected Codex for a `UI critical` workflow ticket and did not emit a visual-baseline-not-ready warning.
- UI quality validation failed when screenshots/flow evidence were missing and passed when complete artifacts were attached.
- Visual taste learning recorded rejection labels and required an explicit `taste_checks.spacing_off` follow-up after repeated spacing feedback.
- Scheduled desktop actions were exercised through the agent-facing tool path for preview, create, list, disable, enable, reschedule, cancel, and scheduler run-log behavior.
- Recurring scheduled actions now have a built-in next-run fallback when `croniter` is unavailable.
- The final live proof originally used a legacy Cursor IDE work packet, then executed a real Codex CLI backend edit in a disposable git repo, completed the DecisionsAI workflow with a terminal result packet, and produced a next-action decision of `continue`.

## Visual Baseline Evidence

Baseline set:

- Name: `DecisionsAI Workflow Harness Gold`
- Hermes baseline ID: `5`
- Version: `2026-06-02`
- Readiness: `pass`
- Reference screen count: `5`
- Missing screen count: `0`

Reference screenshots:

- `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/visual-baselines/2026-06-02/01-workflows-overview.png`
- `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/visual-baselines/2026-06-02/02-rules-run-settings.png`
- `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/visual-baselines/2026-06-02/03-scheduled-actions-panel.png`
- `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/visual-baselines/2026-06-02/04-visual-baselines-panel.png`
- `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/visual-baselines/2026-06-02/05-runs-timeline.png`

## UI Harness Proof

Proof board:

- Board ID: `26060201`
- Project ID: `260602`
- Ticket ID: `2606020101`

Observed route:

- Backend: `codex`
- Complexity: `medium`
- Model: `gpt-5.3-codex-spark`
- Source: `harness_preference`
- Rationale: `Intake override 'ui critical' requested codex`
- Baseline warning present: `false`

Validation behavior:

- Missing-artifact validation verdict: `fail`
- Complete screenshot/flow validation verdict: `pass`
- Feedback labels recorded: `spacing_off`, `spacing_off`
- Follow-up without taste check: `fail`
- Follow-up with `taste_checks.spacing_off`: `pass`

## Scheduled Actions Proof

One-time keypress proof:

- Workflow ID: `302`
- Action: press `enter`
- Target app: `HarnessProofDefinitelyNotForeground`
- Safety: require target app in foreground
- Scheduler run result: `skipped`
- Run-log reason: foreground app could not be verified for the target app
- Late policy: `run_as_soon_as_possible`
- Final schedule state: disabled one-time action with `next_run_at = null`

Recurring Chrome proof:

- Workflow ID: `303`
- Initial schedule: weekdays at `08:30` Africa/Johannesburg
- Initial computed `next_run_at`: `2026-06-03T06:30:00`
- Rescheduled to: daily at `10:15` Africa/Johannesburg
- Rescheduled computed `next_run_at`: `2026-06-03T08:15:00`
- Lifecycle checks: preview, create, list, disable, enable, reschedule, and cancel by title all succeeded
- Final state: recurring Chrome proof workflow cancelled

## Live Authenticated IDE Handoff Proof

Proof report:

- `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/live-runs/harness-live-proof-20260602_220915.json`
- Latest full proof report with UI screenshots and positive scheduled action:
  `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/live-runs/harness-live-proof-20260602_221848.json`
- Final full proof report with Cursor packet, Codex backend edit, UI screenshots, scheduled action logs, and terminal DecisionsAI completion:
  `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/live-runs/harness-live-proof-20260602_223331.json`

Live workflow:

- Workflow ID: `310`
- Run ID: `121`
- Step ID: `1804`
- Project ID: `9` (`DecisionsAI`)
- Backend: `cursor` (legacy note: this proof predates the Cursor plugin migration)
- Engine: `cursor`
- Work packet: retired with the old VS Code-compatible extension flow.
- Project execution session ID: `3`

Observed behavior:

- The old proof created a Cursor IDE work packet in the project `.tickets` folder; current Cursor work routes through the Cursor CLI backend and local Cursor plugin.
- The work packet includes authenticated `continue` and `codex-events` callback URLs.
- Hermes recorded `backend_handoff_created`, `backend_handoff_updated`, worker dispatch/progress/completion, and `needs_input` events for the live run.
- The live `codex_needs_input` callback was accepted by a secured local server using `X-DecisionsAI-Internal-Token`.
- The workflow run persisted `status = waiting`, `next_action = needs_human_input`, and `human_intervention_state = needs_human_input`.
- Hermes recorded durable human-intervention memory with label `unclear_requirement`.
- Hermes and JSON proof reports redact `internal_token` callback values; the local `.tickets` work packet keeps the token only so the IDE extension can call back.
- A later terminal `codex_completed` event clears prior `needs_human_input` state, marks the handoff resolved/completed, and allows the next-action engine to choose `continue`.

Callback-auth fix:

- `create_app()` now exports the runtime internal API token to child backend processes via `DECISIONSAI_INTERNAL_API_TOKEN` when the environment does not already provide one.
- Codex/IDE handoff metadata now includes authenticated `continue_url` and `bridge_url` callback URLs.
- Hermes redaction now strips short and long sensitive query-parameter values such as `internal_token=...`.
- Redaction preserves non-secret evidence paths such as `real-worker-edit-...` and `codex-backend-edit-...` while still redacting standalone long tokens and sensitive query parameters.

## Live Codex Backend Edit Proof

Proof report:

- `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/live-runs/harness-live-proof-20260602_223331.json`

Live workflow:

- Workflow ID: `334`
- Run ID: `145`
- Step ID: `1831`
- Initial backend handoff: `cursor`
- Executed backend proof: `codex`

Generated Codex backend artifacts:

- Disposable git repo: `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/live-runs/codex-backend-edit-20260602_223331`
- Edited file: `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/live-runs/codex-backend-edit-20260602_223331/codex_backend_surface.txt`
- Diff: `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/live-runs/codex-backend-edit-20260602_223331/codex_backend_surface.diff`
- Verification log: `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/live-runs/codex-backend-edit-20260602_223331/codex_backend_verification.log`

Observed behavior:

- Codex CLI backend launched through `run_project_task` with `engine = codex` and `success = true`.
- The disposable file changed from `status: pending` to `status: completed`.
- Git status showed only the intended tracked edit plus the generated diff artifact.
- The report contains no live internal API token values after redaction.
- `CodexBackend` now requests `--sandbox workspace-write` by default for project implementation tasks, with `DECISIONSAI_CODEX_SANDBOX` as an override.

## Live UI Change Proof

Proof report:

- `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/live-runs/harness-live-proof-20260602_223331.json`

Live workflow:

- Workflow ID: `334`
- Run ID: `145`
- Step ID: `1831`
- Backend: `cursor`

Generated UI artifacts:

- Before HTML: `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/live-runs/ui-change-20260602_223331/before.html`
- After HTML: `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/live-runs/ui-change-20260602_223331/after.html`
- Before screenshot: `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/live-runs/ui-change-20260602_223331/before.png`
- After screenshot: `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/live-runs/ui-change-20260602_223331/after.png`
- Flow notes: `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/live-runs/ui-change-20260602_223331/flow.md`

Observed behavior:

- Playwright captured real before/after PNG evidence from a disposable proof UI.
- Hermes validation record `84` stored `validation_type = ui_quality` with `verdict = pass`.
- Hermes feedback event `2485` stored an `approved` UI label against the after screenshot.
- The report contains no live internal API token values after redaction.

## Terminal DecisionsAI Completion Proof

Proof report:

- `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/live-runs/harness-live-proof-20260602_223331.json`

Generated terminal artifacts:

- Disposable worker file: `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/live-runs/real-worker-edit-20260602_223331/worker_surface.txt`
- Diff: `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/live-runs/real-worker-edit-20260602_223331/worker_surface.diff`
- Verification log: `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/live-runs/real-worker-edit-20260602_223331/verification.log`

Observed behavior:

- A `codex_progress` bridge event was accepted by DecisionsAI.
- A `codex_completed` bridge event was accepted by DecisionsAI.
- The terminal result packet stored `status = completed` and `audit.final_verdict = pass`.
- Workflow run `145` and step `1831` both persisted as `completed`.
- `human_intervention_state` persisted as `resolved`.
- `worker_status` persisted as `completed`.
- Latest backend handoff persisted as `backend_id = codex`, `state = completed`.
- DecisionsAI next-action decision persisted as `continue`.

## Positive Scheduled Action Live Proof

Proof report:

- `/Users/paul/Library/Application Support/DecisionsAI/harness-proof/live-runs/harness-live-proof-20260602_223331.json`

Positive open-app action:

- Workflow ID: `336`
- Action: open `Calculator`
- Safety: bring app to front
- Scheduler run result: `completed`
- Run-log result: `Opened Calculator for scheduled action. Target app focus requested: Calculator.`

Foreground safety action:

- Workflow ID: `335`
- Action: press `enter`
- Target app: `HarnessProofDefinitelyNotForeground`
- Safety: require target app in foreground
- Scheduler run result: `skipped`
- Run-log reason: foreground app could not be verified for the target app

## Ticket Requirement Coverage

UI harness ticket:

- Phase 1 routing: covered by intake classification, override routing, route events, and plain-language rationale.
- Phase 2 UI definition of done: covered by missing-artifact failure and complete evidence pass.
- Phase 3 visual baseline: covered by baseline ID `5` with five real screenshots and readiness `pass`.
- Phase 4 validation/regression: covered by baseline comparison and missing candidate screenshot failures.
- Phase 5 preference learning: covered by repeated `spacing_off` labels and stricter follow-up validation.
- Live worker loop: covered by final proof workflow `334`, run `145`, terminal result packet `completed/pass`, and next-action decision `continue`.

Scheduled actions ticket:

- One-time scheduling: covered by one-time keypress action.
- Recurring scheduling: covered by weekday Chrome action and daily reschedule.
- Preview/list/cancel/disable/enable/reschedule: covered through `ScheduledActionTool`.
- Run log: covered by skipped scheduler run with explicit safety reason.
- Foreground safety: covered by require-foreground skip behavior.
- Positive desktop execution: covered by live direct `open_app` run for Calculator.
- Late/missed policy: covered by `run_as_soon_as_possible` run metadata.

## Verification

- `python3 -m py_compile scripts/harness_live_proof.py distr/core/hermes.py distr/core/project_cli_backends/registry.py distr/gui/web/server.py distr/core/workflow/scheduler.py distr/core/actions/desktop.py distr/gui/web/routes/settings/workflows.py`
- `pytest tests/core/test_scheduler_once_support.py tests/core/test_harness_operational_proof.py tests/core/test_hermes_learned_rules_context.py::test_backend_handoff_redacts_secrets_and_records_memory tests/core/test_codex_prefs.py tests/core/test_cursor_plugin_contract.py -q`
  - Result: `18 passed`
- Broad non-e2e harness suite across routing, Hermes, workflow API, run audit, UI quality, scheduled actions, Codex prefs, and IDE ticket metadata:
  - Result: `139 passed`
- `pytest tests/ui/test_workflow_result_packet_evidence_webkit.py -q -m e2e_playwright --browser webkit -rs`
  - Result: `1 passed`

## Caveats / Operator Notes

- The foreground-safety proof intentionally skips when the target app cannot be verified as foreground; positive desktop execution is separately covered by the Calculator open-app proof.
- The first baseline uses DecisionsAI workflow screens by default. The user can still replace or expand it with preferred gold-standard product screens later.
- The proof leaves disabled one-time scheduled actions in the database as run-log evidence.
- Cursor work now routes through the Cursor CLI backend and local Cursor plugin. The real autonomous edit proof is currently through Codex CLI backend, which is directly verified in the final live report.
