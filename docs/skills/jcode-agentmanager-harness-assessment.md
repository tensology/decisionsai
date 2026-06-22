# JCode And AgentManager Harness Assessment

Date: 2026-06-22

## Summary

`1jehuang/jcode` should be treated as a serious candidate for a DecisionsAI CLI backend and workflow harness. It is MIT licensed, Rust-based, exposes non-interactive `jcode run`, machine-readable `--json` and `--ndjson` modes, provider/auth doctor commands, ACP support, MCP import/config support, multi-session server/client mode, memory, replay, browser tooling, and swarm/session primitives.

AgentManager should remain reference-only for this pass. It is useful for operational patterns around agent lifecycle, kill switches, message delivery, workspace cleanup, scoped MCP config, stuck-agent diagnostics, persistence, and runbooks. It should not replace DecisionsAI's one-agent-per-board model.

## Reference Checkouts

| Repo | Local path | Source commit | License | Decision |
| --- | --- | --- | --- | --- |
| `1jehuang/jcode` | `/Users/paul/development/TENSOLOGY/DECISIONS/reference/jcode` | `446be1d3` | MIT | Assess for CLI backend and workflow harness |
| `simonstaton/AgentManager` | `/Users/paul/development/TENSOLOGY/DECISIONS/reference/AgentManager` | Existing reference checkout | MIT | Reference patterns only |

## JCode Findings

### What It Offers

JCode has a shape that fits DecisionsAI better than most reference repos:

- A normal CLI binary: `jcode`.
- A one-shot command: `jcode run "task"`.
- Machine-readable modes: `jcode run --json` and `jcode run --ndjson`.
- Persistent server/client mode: `jcode serve`, `jcode connect`.
- ACP adapter: `jcode acp`.
- Auth and provider diagnostics: `jcode auth-test --json`, `jcode auth doctor`, `jcode provider list --json`, `jcode provider-doctor --json`.
- Provider breadth: Claude, OpenAI, Gemini, Copilot, Azure, OpenRouter, Ollama, LM Studio, Cursor, Antigravity, OpenAI-compatible profiles, and more.
- MCP support: global `~/.jcode/mcp.json`, project `.jcode/mcp.json`, plus import fallback from Claude and Codex config.
- Session resume across harnesses: README claims resume support for Codex, Claude Code, OpenCode, and Pi sessions.
- Harness-level tools: browser automation, memory, session search, transcript injection, replay/video export, tool harness smoke test.

### Fit For DecisionsAI

Recommended first integration: add JCode as an optional project CLI backend, equivalent to Codex/OpenCode/Cline style one-shot execution.

Initial backend contract:

```text
backend id: jcode
display name: JCode
executable: jcode
availability: jcode --version
task command: jcode run --ndjson "<instruction>"
fallback task command: jcode run "<instruction>"
auth check: jcode auth-test --all-configured --no-smoke --json
provider catalog: jcode provider list --json
setup hint: curl -fsSL https://raw.githubusercontent.com/1jehuang/jcode/master/scripts/install.sh | bash
```

This should be optional and off by default until the binary is installed and at least one provider is authenticated.

### Workflow-Harness Fit

JCode's deeper features are promising, but they need guardrails before being wired into Decisions workflows:

- `serve/connect` can support durable workflow sessions, but Decisions must own the workflow run state.
- `--ndjson` streaming can map into Decisions event streams.
- `auth-test` and `provider-doctor` can feed the harness doctor.
- `.jcode/mcp.json` can become another projection target in the Decisions harness stack.
- Session resume may help recover failed Codex/Claude/Pi runs, but should be read-only assessed first.
- Memory/session search should not replace Decisions project memory until ownership and retention are defined.

### Guardrails Needed Before Full Use

- Disable or gate autonomous swarm spawning by default.
- Do not let JCode create arbitrary multi-agent teams inside a Decisions board run.
- Do not let self-development mode run without explicit human approval.
- Require Decisions callback/event reporting in prompts or wrapper logic.
- Require cwd/project scoping and clean abort behavior.
- Route MCP via Decisions-scoped config, not broad global imports, unless the user explicitly chooses global mode.
- Add CLI doctor checks before exposing JCode in the UI as ready.

## AgentManager Findings

### Useful Patterns To Borrow

AgentManager should inform DecisionsAI operational hardening:

- Kill switch layers: global halt, process tree kill, token/session invalidation, remote kill marker.
- Process lifecycle: process groups, orphan cleanup, startup recovery, signal handling.
- Stuck-agent diagnostics: detect no-output agents, inspect logs, interrupt, destroy, respawn.
- Message bus: typed messages (`task`, `result`, `question`, `info`, `status`, `interrupt`) and read tracking.
- Idle-agent delivery: push messages when an agent becomes idle; interrupt only as an explicit escalation.
- Shared context: persistent markdown context files, but map this to Decisions project memory instead of adding a second truth source.
- Scoped MCP config: per-agent/workspace allowlists rather than loading every credentialed server.
- Workspace cleanup: periodic cleanup of inactive workspaces/worktrees and stale persisted state.
- Runbooks: incident docs for crash recovery, stuck agents, high resource use, API unresponsive, storage/auth failures.

### What To Reject Or Defer

AgentManager should not be adopted as a platform layer:

- No replacement of DecisionsAI orchestration.
- No parent-child agent trees as the default model.
- No batch agent teams inside a board run.
- No independent task graph replacing Decisions tickets/workflows.
- No Claude-only process assumption.
- No default `--dangerously-skip-permissions` style posture.
- No GCS/Cloud Run dependency for local Decisions runtime.

## DecisionsAI Implementation Recommendation

### Phase 1: JCode Backend And Doctor Support

Adopt JCode as an optional CLI backend:

1. Add `JCodeBackend(OneShotCliBackend)` to `distr/core/project_cli_backends/registry.py`.
2. Add aliases: `jcode`, `j_code`, `j-code`.
3. Add setup support to `scripts/setup_project_clis.sh jcode`.
4. Extend `distr/core/harness_doctor.py` to detect `jcode`, `~/.jcode`, `.jcode/mcp.json`, and provider/auth readiness.
5. Add route/backend tests mirroring Codex/OpenCode/Cline.
6. Keep one-shot execution first; use `jcode run --ndjson` if event parsing is implemented, otherwise use plain `jcode run`.

### Phase 2: JCode Harness Projection

Add JCode as a harness surface in the Decisions harness stack:

1. Add `.jcode/mcp.json` projection support for project-scoped MCP.
2. Add reference skill projection only if JCode consumes skill/command files cleanly.
3. Add a `decisions-jcode-adapter` reference describing callback prompts, provider setup, and guardrails.
4. Validate `jcode auth-test --json` and `jcode provider-doctor --json` output shapes before relying on them.

### Phase 3: AgentManager Pattern Borrowing

Borrow small, Decisions-native primitives:

1. Board-agent kill switch/runbook.
2. Stuck-agent detector for the one board agent.
3. Idle delivery semantics for workflow messages.
4. Scoped MCP config per workflow run.
5. Workspace cleanup and stale session recovery.

Do not import AgentManager runtime code or UI as-is.

## Open Questions

- Does `jcode run --ndjson` emit stable event schemas suitable for Decisions streaming, or should Decisions initially treat it as plain text?
- Can JCode swarm/self-dev tools be disabled by CLI flags, config, or tool profile in a way Decisions can enforce?
- What is the safest provider default for Decisions: inherit existing JCode default, force explicit provider/model, or map from the project backend model picker?
- Can JCode session resume safely attach to a specific Decisions project and board without leaking across unrelated work?
- Should Decisions project memory feed JCode memory, or should JCode memory stay isolated until there is an explicit sync policy?

## Current Recommendation

Add JCode to the CLI backend list as optional and experimental. Treat AgentManager as an operations pattern library only. The next concrete implementation should be Phase 1: backend detection, setup script support, doctor reporting, and a one-shot JCode backend guarded by tests.
