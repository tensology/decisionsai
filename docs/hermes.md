# Hermes

Hermes is the DecisionsAI orchestration ledger and memory layer. It is the part of the app that keeps chat, ticket boards, projects, workflows, automations, browser evidence, IDE activity, and proactive planning connected to the same story.

It is not a separate chatbot. Hermes is the shared context behind the main orchestrator so DecisionsAI can understand what project you are working on, what changed, what stalled, what finished, and which surface should speak back to you.

## What Hermes Does

- Records normalized events for chats, tickets, workflow runs, automation runs, browser checks, IDE handoffs, validation, and corrections.
- Keeps work attached to projects and execution sessions where possible, including free Codex and Cursor conversations.
- Stores durable user-memory signals and machine-activity summaries without needing to keep every raw conversation forever.
- Helps route work between the local agent, workflows, automations, browser evidence, Codex, Cursor, Claude-compatible harnessing, and CLI fallbacks.
- Gives the web UI a single timeline for what happened instead of scattering state across tools.

## Where It Shows Up

| Surface | How Hermes Helps |
|---|---|
| Chat | Keeps conversation, project context, and useful memory connected. |
| Ticket Boards | Sends selected tickets into the orchestrator with board, ticket, and project context. |
| Workflows | Records route decisions, run progress, validation evidence, and correction loops. |
| Automations | Keeps scheduled instruction runs tied to real workflow runs and history. |
| Browser / Playwright | Stores screenshots, console logs, URLs, and evidence as workflow context first. |
| [Codex](../codex_plugin/decisions-codex/README.md) / [Cursor](../cursor_plugin/decisions-cursor/README.md) / [Claude-compatible harness](../vendor/ecc/docs/HERMES-SETUP.md) | Receives project/session activity so IDE work can continue a thread instead of disappearing into a separate editor. |
| Proactive Planning | Looks across linked sources and project activity to support daily planning and stuck-work nudges. |

## Source Map

- Core ledger helpers: [`distr/core/hermes.py`](../distr/core/hermes.py)
- Routing policy: [`distr/core/hermes_orchestrator.py`](../distr/core/hermes_orchestrator.py)
- Proactive checks: [`distr/core/hermes_proactive.py`](../distr/core/hermes_proactive.py)
- Memory compaction: [`distr/core/hermes_memory.py`](../distr/core/hermes_memory.py)
- Database models: [`distr/core/db/hermes.py`](../distr/core/db/hermes.py)
- Codex bridge: [`codex_plugin/decisions-codex/README.md`](../codex_plugin/decisions-codex/README.md)
- Cursor bridge: [`cursor_plugin/decisions-cursor/README.md`](../cursor_plugin/decisions-cursor/README.md)
- ECC harness setup reference: [`vendor/ecc/docs/HERMES-SETUP.md`](../vendor/ecc/docs/HERMES-SETUP.md)
