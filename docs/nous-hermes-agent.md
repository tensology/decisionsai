# Nous Hermes Agent integration

This document is about **[Hermes Agent](https://hermes-agent.nousresearch.com/)** by Nous Research — an open-source autonomous agent (`hermes` CLI, state under `~/.hermes`). It is **not** DecisionsAI's Orchestrator (see [orchestrator.md](orchestrator.md)).

## Three layers (no confusion)

```text
┌─────────────────────────────────────────────────────────────┐
│  DecisionsAI Orchestrator                                    │
│  Ledger, routing, memory, workflow/ticket supervision        │
│  (DB: orchestrator_events*, settings: hermes_*)                    │
└───────────────────────────┬─────────────────────────────────┘
                            │ dispatches / records
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
  Project CLIs          IDE handoff        Nous Hermes Agent
  Pi, Cursor CLI,       Codex IDE,         hermes chat / gateway
  Codex CLI, Claude     Cursor IDE         ~/.hermes + plugins
```

| Layer | What it is | Install signal |
|---|---|---|
| **Orchestrator** | Inside DecisionsAI | Always present when the app runs |
| **Project CLIs / IDEs** | Pi, `cursor-agent`, `codex`, Claude Code, IDE plugins | `scripts/setup_project_clis.sh` |
| **Nous Hermes Agent** | Standalone operator agent | `curl …/install.sh \| bash` → `hermes setup` → `~/.hermes` |

On this machine, Nous Hermes is **not installed** (`hermes` not on PATH, `~/.hermes` missing). That is expected unless you explicitly install it.

## What Nous Hermes Agent does

- **Persistent operator memory** — `~/.hermes/memories/`, skills, cron, sessions.
- **Multi-surface chat** — CLI, TUI, optional gateway (Telegram, Discord, Slack, WhatsApp, …).
- **Tool execution** — terminal, browser, sandbox backends, subagents.
- **Plugin system** — Python plugins under `~/.hermes/plugins/` with `pre_tool_call` hooks.

ECC documents Hermes as an **operator shell** that can import ECC skills ([`plugins/ecc/docs/HERMES-SETUP.md`](../plugins/ecc/docs/HERMES-SETUP.md)). DecisionsAI already covers much of that role via the Orchestrator + workflows + Telegram/WhatsApp integrations.

## RTK + Hermes Agent

When Nous Hermes **is** installed, RTK can register a rewrite plugin:

```bash
rtk init -g --agent hermes --auto-patch
```

That writes `~/.hermes/plugins/rtk-rewrite/` and enables it in `config.yaml`. The plugin rewrites `terminal` tool commands through `rtk rewrite` before execution — same token-compression idea as Pi/Cursor hooks.

DecisionsAI's server-side `distr/core/rtk_support.py` covers workflow `run_command` steps; it does **not** replace this plugin.

## How they should work together

### Today (no Hermes Agent install required)

1. **Orchestrator** owns ticket → workflow → route → evidence.
2. **Project CLIs** execute work (`run_project_task`, Pi RPC, one-shot Cursor/Codex).
3. **IDE handoff** writes work packets; plugins report back.
4. **RTK** compresses shell output (hooks on CLIs + server `run_command` rewrite).

### With Nous Hermes Agent installed (recommended wiring)

1. **Install** — `scripts/setup_project_clis.sh hermes-agent` (or upstream install script).
2. **RTK** — `rtk init -g --agent hermes --auto-patch` after RTK is on PATH.
3. **ECC skills** — import shared skills into `~/.hermes/skills/` per ECC Hermes setup doc (optional).
4. **DecisionsAI** — treat Hermes Agent as another **execution surface**, not as the Orchestrator:
   - Orchestrator still records `orchestrator_events` for workflow/ticket truth.
   - Hermes Agent runs long-lived operator automations (cron, gateway) that DecisionsAI does not need to duplicate.
   - Project-scoped coding stays on Pi / Cursor / Codex unless you explicitly route tickets to `hermes`.

### Future integration (not implemented yet)

A clean fit in DecisionsAI would be:

- Add `hermes_agent` to `project_cli_backends` (like Pi): `hermes chat -p "<instruction>"` or RPC if Hermes exposes it.
- Map Orchestrator handoff packets to Hermes session context (project folder, ticket id, callback URL).
- Emit Orchestrator events when Hermes gateway or cron completes work tied to a ticket.

Until that backend exists, install Hermes Agent for **personal operator** use alongside DecisionsAI; use the Orchestrator for **supervised ticket/workflow** use.

## Install (optional)

```bash
# From DecisionsAI repo
NONINTERACTIVE=1 bash scripts/setup_project_clis.sh hermes-agent

hermes setup   # first-time config (~/.hermes)
rtk init -g --agent hermes --auto-patch   # if RTK installed
```

Verify:

```bash
which hermes
test -d ~/.hermes && echo "Hermes home OK"
```
