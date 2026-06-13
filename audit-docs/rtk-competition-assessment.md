# RTK competition reference assessment

Reference clone: `../COMPETITION/rtk` (sibling to this repo; sync with `scripts/sync_competition_reference_rtk.sh`).

Upstream: [rtk-ai/rtk](https://github.com/rtk-ai/rtk) — Rust CLI proxy that compresses common dev-command output before it reaches LLM context (claims ~60–90% token savings on shell workflows).

## What DecisionsAI already does

- Project CLIs: Pi, Cursor CLI, Codex CLI, Claude Code, Cursor IDE, Codex IDE handoff.
- Workflow complexity routing and global execution config.
- Hermes plugin surfaces for Codex/Cursor.

## What RTK adds

- Transparent bash-hook rewrites (`git status` → `rtk git status`) for supported agents.
- Direct `rtk <cmd>` wrappers for built-in tools that bypass hooks (Read, Grep, Glob in Claude Code).
- Per-agent init: `rtk init -g`, `--codex`, `--agent cursor`, `--agent pi`, etc.
- Token savings analytics via `rtk gain`.

## Integration fit (initial)

| Area | Fit | Notes |
|------|-----|-------|
| DecisionsAI CLI tab (Pi RPC / one-shot CLIs) | Medium | Hooks help bash subprocesses; Pi/Cursor built-in tools need explicit `rtk` calls or shell commands |
| Workflow ticket dispatch | Medium | Could wrap git/test/lint commands in harness before sending to agents |
| Nous Hermes Agent (`~/.hermes`) | Medium | RTK plugin for external Hermes CLI only — not DecisionsAI Orchestrator |
| IDE handoff packets | Low | IDE chooses its own tools; RTK is CLI/shell oriented |
| Server-side model list APIs | None | RTK does not replace Cursor/Codex model APIs |

## Risks / constraints

- Telemetry is opt-in by default; DecisionsAI setup sets `RTK_TELEMETRY_DISABLED=1` during hook init.
- Windows native shell lacks auto-rewrite hook; WSL recommended upstream.
- Another crate named `rtk` exists on crates.io — install via official script or Homebrew `rtk`, not `cargo install rtk`.
- Name collision with Rust Type Kit is documented upstream.

## Current DecisionsAI setup wiring

`scripts/setup_project_clis.sh`:

- `rtk` target installs RTK (Homebrew or official install script).
- `all` and per-CLI targets run `init_rtk_agent_hooks` when the matching CLI is present.
- Startup (`bin/decisions.sh`) checks for `rtk` on PATH but does not install it (same pattern as other CLIs).

## Runtime integration (implemented)

Server-side paths that bypass agent bash hooks now route through `distr/core/rtk_support.py`:

- Workflow `run_command` steps (`step_executor._run_command`, `workflow._exec_run_command`) call `rtk rewrite` before `subprocess.run`, then execute the rewritten command (e.g. `git status` → `rtk git status`).
- Project handoff git snapshots (`registry._git_status_short`) use the same rewrite path.
- `scripts/verify_agent_harness_setup.py` re-runs `rtk init` for installed agents (and Hermes when `~/.hermes` exists) on startup, alongside ECC/Codex/Cursor plugin sync.

Agent CLIs still rely on `scripts/setup_project_clis.sh` hook init; disable server rewrite with `DECISIONS_RTK_DISABLED=1`.

## Next integration steps (optional)

1. Measure token savings on real DecisionsAI workflow runs (git status, cargo test, pytest) via `rtk gain`.
2. Optional UI: show RTK install status in global workflow execution panel (read-only).
