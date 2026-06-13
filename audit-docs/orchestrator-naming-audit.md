# Orchestrator naming migration (completed 2026-06-11)

## Summary

Internal **Hermes** naming (orchestration layer) has been renamed to **Orchestrator** across code, database tables, settings keys, APIs, and tests. **No backward-compatibility shims** remain.

**Unchanged (external Nous Hermes Agent only):** `hermes_agent` backend, `hermes` / `hermes-agent` CLI aliases, `~/.hermes`, `docs/nous-hermes-agent.md`, `vendor/ecc/`.

## Module map (new)

| Role | Module |
|------|--------|
| Ledger API | `distr/core/orchestrator.py` |
| Routing | `distr/core/orchestrator_routing.py` |
| Memory | `distr/core/orchestrator_memory.py` |
| Validator | `distr/core/orchestrator_validator.py` |
| Proactive | `distr/core/orchestrator_proactive.py` |
| Daily triage | `distr/core/orchestrator_daily_triage.py` |
| Delegated workflows | `distr/core/delegated_workflow/` |
| DB models | `distr/core/db/orchestrator.py` |
| Memory API | `distr/gui/web/routes/orchestrator_memory.py` |
| Event wrapper | `distr/core/orchestration_events.py` |

## Database

Tables renamed `hermes_*` → `orchestrator_*` via `_migrate_legacy_hermes_schema_to_orchestrator()` in `migrations.py`.

Settings columns: `orchestrator_enabled`, `orchestrator_provider`, `orchestrator_model`, `orchestrator_validator_*`, `orchestrator_correction_*`, `orchestrator_memory_export_enabled`.

Board policy column: `kanban_boards.orchestrator_policy`.

## APIs (canonical only)

- `GET/POST /workflows/orchestrator-setup`
- `GET /workflows/{id}/orchestrator-events`
- `/api/orchestrator/memories`, `/api/orchestrator/activity`
- `/tickets/boards/{id}/orchestrator-learned-rules/{rule_id}`

## Remote app API naming

The mobile remote app uses canonical **`/api/tickets/*`** paths via `www.decisionsai.net/remote-app/src/lib/ticketApi.js`. Legacy **`/api/kanban/*`** is rewritten on the desktop server only; remote clients must not depend on the kanban prefix. Drift matrix: [remote-app-drift-audit.md](./remote-app-drift-audit.md).

## Dead code (still documented, not removed)

- `count_correction_attempts()`, `mark_correction_dispatched()` — correction auto-dispatch disabled
- `orchestrator_memory_export_enabled` — no export implementation
- `orchestrator_correction_provider/model` — unused LLM role keys

## Closed-loop steering (2026-06-11)

- `distr/core/workflow/steering_memory.py` — run-scoped `steering_log`, learned rules, adaptive workflow context
- Cursor/Codex bridge (`POST .../codex-events`) appends steering entries + human intervention memory
- `continue_waiting_step` records steering for all user continuations (not only IDE handoff)
- Next-step prompts inject `[WORKFLOW STEERING MEMORY]`; board learned rules inject via `build_standards_context(board_id=...)`
- Per-step `complexity` in harness (low for Playwright/computer_use) + UI field in loop step modal
- Tests: `test_steering_memory.py`, `test_orchestrator_steering_learning_loop.py`
- UI: Workflows → Runs → Memory (`wf-steering-memory-body`, `GET .../steering-memory`)
