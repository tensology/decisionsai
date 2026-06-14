# Orchestrator

The **Orchestrator** is DecisionsAI's internal coordination layer: event ledger, routing policy, durable memory signals, and execution handoffs. It keeps chat, ticket boards, projects, workflows, automations, browser evidence, IDE activity, and proactive planning on one timeline.

It is **not** the [Nous Hermes Agent](nous-hermes-agent.md) (the separate `hermes` CLI that installs to `~/.hermes`).

## What the Orchestrator does

- Records normalized events for chats, tickets, workflow runs, automation runs, browser checks, IDE handoffs, validation, and corrections.
- Keeps work attached to projects and execution sessions where possible, including free Codex and Cursor conversations.
- Stores durable user-memory signals and machine-activity summaries without keeping every raw conversation forever.
- Routes work between the local agent, workflows, automations, browser evidence, Codex, Cursor, Claude-compatible harnessing, and project CLI fallbacks.
- Gives the web UI a single timeline for what happened instead of scattering state across tools.

## Where it shows up

| Surface | How the Orchestrator helps |
|---|---|
| Chat | Keeps conversation, project context, and useful memory connected. |
| Ticket boards | Sends selected tickets into routing with board, ticket, and project context. |
| Workflows | Records route decisions, run progress, validation evidence, and correction loops. |
| Automations | Keeps scheduled instruction runs tied to real workflow runs and history. |
| Browser / Playwright | Stores screenshots, console logs, URLs, and evidence as workflow context first. |
| [Codex](../plugins/codex-ide/README.md) / [Cursor](../plugins/cursor-ide/README.md) / [Claude harness](../plugins/ecc/docs/HERMES-SETUP.md) | Receives project/session activity so IDE work can continue a thread instead of disappearing in the editor. |
| Proactive planning | Looks across linked sources and project activity for daily planning and stuck-work nudges. |

## Source map

| Role | Module |
|---|---|
| Core ledger | [`distr/core/orchestrator.py`](../distr/core/orchestrator.py) |
| Execution routing | [`distr/core/orchestrator_routing.py`](../distr/core/orchestrator_routing.py) |
| Proactive checks | [`distr/core/orchestrator_proactive.py`](../distr/core/orchestrator_proactive.py) |
| Memory | [`distr/core/orchestrator_memory.py`](../distr/core/orchestrator_memory.py) |
| Validator | [`distr/core/orchestrator_validator.py`](../distr/core/orchestrator_validator.py) |
| Delegated workflows | [`distr/core/delegated_workflow/`](../distr/core/delegated_workflow/) |
| Database models | [`distr/core/db/orchestrator.py`](../distr/core/db/orchestrator.py) |
| Workflow setup API | `GET/POST /workflows/orchestrator-setup` |
| Run timeline API | `GET /workflows/{id}/orchestrator-events` |
| Run steering memory | `GET /workflows/{id}/runs/{run_id}/steering-memory` |
| Steering log writer | [`distr/core/workflow/steering_memory.py`](../distr/core/workflow/steering_memory.py) |
| Memory API | `/api/orchestrator/memories`, `/api/orchestrator/activity` |

## Learned rules (not Nous Hermes memory)

Board-scoped **Orchestrator learned rules** live in `orchestrator_learned_rules` (`OrchestratorLearnedRule`). They are written by `record_learning_signal()` when validation fails, IDE handoffs return feedback, or harness events arrive. Routing reads them via `build_learned_rules_context()`.

This is separate from Nous Hermes Agent memory in `~/.hermes`.

API: `PATCH /tickets/boards/{id}/orchestrator-learned-rules/{rule_id}`

## Workflow steering memory (closed loop)

During an active run, IDE/CLI bridge events, harness steers, continuations, and UI feedback append to `run_data.steering_log`. Meaningful feedback also writes board learned rules and workflow adaptive context.

- **Ingestion:** `POST /workflows/{id}/runs/{run_id}/codex-events` (Cursor and Codex), `POST .../continue`, `POST .../steer`, `POST .../ui-feedback`
- **Next step:** `build_steering_context_for_run_id()` and `build_standards_context(board_id=...)` inject into step prompts; `resolve_execution_route()` reads learned rules
- **UI:** Workflows → Runs → **Memory** shows the steering log, board learned rules (enable/disable), adaptive workflow memory, and a preview of what the next step will see

## Related: Nous Hermes Agent

Optional external operator CLI (`hermes_agent` backend). See [Nous Hermes Agent integration](nous-hermes-agent.md).

## Migration notes

Full rename inventory: `audit-docs/orchestrator-naming-audit.md`
