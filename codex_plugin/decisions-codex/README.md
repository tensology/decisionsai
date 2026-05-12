# DecisionsAI Codex Plugin

This plugin is the Codex-side wrapper for DecisionsAI project work.

It is intentionally small: DecisionsAI owns project selection, ticket context,
workflow state, and approval policy. Codex receives a bounded implementation
instruction, works in the project folder, and reports a clear result that
DecisionsAI can write back to the workflow, ticket audit trail, and Telegram.

## Current Integration Points

- Project CLI backend id: `codex`
- Expected command: `codex exec "<instruction>"`
- Optional model flag: `codex exec --model <model> "<instruction>"`
- DecisionsAI owns model selection per project. If a project has a Codex model
  selected, the backend passes it with `--model`; otherwise Codex uses its own
  configured default.
- DecisionsAI sends structured ticket context via the same project CLI adapter
  layer used by Pi, Cursor CLI, and Claude Code.
- Workflow steps can use the selected project backend when a ticket/project is
  routed to CLI execution.
- Workflow outputs must return enough detail for the next DecisionsAI step,
  ticket audit trail, and orchestrator/sub-agent handoff to continue.

## Local Install / Reinstall

From the DecisionsAI repo root:

```bash
python3 codex_plugin/decisions-codex/scripts/install_local.py
```

The installer copies this plugin to `~/plugins/decisions-codex` and registers it
in `~/.agents/plugins/marketplace.json`. It is safe to rerun after edits. Restart
Codex or reload plugins, then enable **DecisionsAI Codex** from the plugin list.

Before running a real workflow task, confirm the backend is available:

```bash
codex_plugin/decisions-codex/scripts/check_codex_backend.sh
```

DecisionsAI can still select the `codex` backend before the plugin is enabled;
the plugin adds Codex-side operating instructions, while the DecisionsAI adapter
handles project selection, command execution, stdout capture, and workflow
result persistence.

## Result Contract

When Codex finishes a DecisionsAI task, keep the final response short and
structured:

```text
Status: completed | failed | needs_input
Summary: ...
Files changed: ...
Tests: ...
Next step: ...
```

DecisionsAI captures stdout/stderr from the CLI backend and stores the result
against the originating ticket/workflow audit row.

## Workflow Contract

For workflow-sourced tasks:

- Treat the DecisionsAI instruction as the active ticket/workflow scope.
- Use any result-packet context at the top of the prompt as prior step state.
- Keep file changes inside the project folder.
- Include changed files, commands/tests run, blockers, and the recommended next
  workflow action in the final response.
- If more information or permission is required, return `Status: needs_input`
  and state the exact question DecisionsAI should ask the user, including via
  Telegram when the Initiative setting requires approval.
