# DecisionsAI Cursor Plugin

**DecisionsAI Cursor** lets Cursor act as a structured execution worker for
DecisionsAI tickets, workflows, and project runs.

DecisionsAI owns the board, ticket, project, workflow state, approval rules, and
audit trail. Cursor receives a bounded implementation packet, works in the
linked project folder through the Cursor CLI backend, and returns a clean
completion packet that DecisionsAI can write back to the ticket and workflow run.

## What It Adds

- Cursor-side operating instructions for DecisionsAI tickets and workflow steps.
- A stable result format for status, changed files, tests, evidence, blockers,
  and next actions.
- Better behavior when a ticket needs more information instead of guessed work.
- A plugin identity and starter prompts for the Cursor plugin UI.

## Architecture Position

Use the Cursor IDE/chat surface as the preferred execution path. The plugin is
the behavior and context layer for project-bound IDE work: it should create or
resume a DecisionsAI project IDE session from the current folder, report user
prompts and steering as they happen, and report completion or blockers back to
DecisionsAI. The Cursor CLI remains fallback transport for automation, setup
checks, and environments where the IDE bridge is unavailable.

## Project IDE Session Reporting

For ordinary Cursor project chats that do not include a workflow callback block,
the worker skill should still report the IDE turn through the local project
bridge:

```bash
python3 ~/.cursor/plugins/local/decisions-cursor/scripts/report_decisions_event.py \
  --turn-input "User prompt" \
  --turn-output "Short Cursor result summary"
```

Without `--callback-url`, the reporter posts to
`/api/ide/sessions/event` on `DECISIONS_API_BASE` or
`http://127.0.0.1:8765`. DecisionsAI resolves the project from the current
working directory, creates or resumes a Cursor IDE session, appends the user
prompt and Cursor completion to the linked Decisions chat, and records the run
events for orchestrator progress checks.

If DecisionsAI is switched off or unreachable, the reporter exits quietly by
default so Cursor work is not blocked. Use `--strict` only for setup diagnostics.

## Local Install / Reinstall

From the DecisionsAI repo root:

```bash
python3 plugins/cursor-ide/scripts/install_local.py
```

The installer copies this plugin to:

```text
~/.cursor/plugins/local/decisions-cursor
```

Restart Cursor or run **Developer: Reload Window**, then confirm
**DecisionsAI Cursor** appears in Cursor Settings -> Plugins.

## Result Contract

When Cursor finishes a DecisionsAI task, keep the final response short and
structured:

```text
Status: completed | failed | needs_input
Summary: ...
Files changed: ...
Tests: ...
Evidence: ...
Blockers: ...
Next step: ...
```
