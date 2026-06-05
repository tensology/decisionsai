# DecisionsAI Codex Plugin

![DecisionsAI Codex](./assets/logo.png)

**DecisionsAI Codex** lets Codex act as a structured execution worker for
DecisionsAI tickets, workflows, and project runs.

DecisionsAI owns the board, ticket, project, workflow state, approval rules, and
audit trail. Codex receives a bounded implementation packet, works in the linked
project folder, and returns a clean completion packet that DecisionsAI can write
back to the ticket and workflow run.

## What It Adds

- Codex-side operating instructions for DecisionsAI tickets and workflow steps.
- A stable result format for status, changed files, tests, evidence, blockers,
  and next actions.
- Better behavior when a ticket needs more information instead of guessed work.
- A plugin identity, icon, logo, and starter prompts for the Codex plugin UI.

## Technical Accreditation

This plugin is part of the DecisionsAI workflow execution stack:

| Component | Role |
|---|---|
| DecisionsAI | Owns boards, tickets, projects, workflow state, approvals, and audit history. |
| Hermes | Internal orchestration ledger for run events, validation records, correction attempts, and portable workflow memory. |
| Codex | Executes bounded project work from a ticket packet and returns evidence. |
| CLI / IDE adapters | Provide interchangeable execution transport for Codex, Cursor, and other project executors. |

Hermes is treated as underlying architecture. The product UI should describe the
user-facing behavior as workflow orchestration, validation, and run history
rather than advertising the internal engine by name.

## Architecture Position

Treat the Codex IDE/chat surface as the preferred execution path. It is where
the human is already steering work, so DecisionsAI should create or resume a
project IDE session from the current folder and record prompt, progress, and
completion events there. The Codex CLI remains a fallback transport for
automation, setup checks, and environments where the IDE bridge is unavailable.
When Codex or the bridge is missing, DecisionsAI should show setup state instead
of pretending the IDE handoff is available.

Treat this plugin as the IDE behavior and context layer. Its job is to make
Codex understand DecisionsAI work packets and return structured, useful
results: what changed, which files were touched, which tests ran, what failed,
what is blocked, and what the next recommended step is.
Those reports become DecisionsAI workflow checkpoints when the IDE session is
attached to a workflow run.

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
- Workflow packets can include a Decisions callback URL. Codex should report
  steering, waiting, interruption, progress, completion, and failure events back
  through `scripts/report_decisions_event.py` so DecisionsAI can write them into
  the execution session and Hermes run ledger.

## Codex Feedback Bridge

When a DecisionsAI workflow sends work to Codex, the prompt may include a
`[DECISIONS CODEX CALLBACK]` block. That block contains the workflow id, run id,
step id, ticket id, project id, execution session id, callback URL, and reporter
script path.

Codex should call the reporter whenever the work materially changes state:

```bash
python3 ~/plugins/decisions-codex/scripts/report_decisions_event.py \
  --callback-url "http://127.0.0.1:8765/api/workflows/1/runs/2/codex-events" \
  --event-type user_steer \
  --status observed \
  --message "Human steering changed the implementation target."
```

Those events are stored against the DecisionsAI execution session and Hermes
ledger. Useful steering feedback can also become adaptive workflow context so
future tickets inherit the principle rather than repeating the mistake.

## Project IDE Session Reporting

For ordinary Codex project chats that do not include a workflow callback block,
the worker skill should still report the IDE turn through the local project
bridge:

```bash
python3 ~/plugins/decisions-codex/scripts/report_decisions_event.py \
  --turn-input "User prompt" \
  --turn-output "Short Codex result summary"
```

Without `--callback-url`, the reporter posts to
`/api/ide/sessions/event` on `DECISIONS_API_BASE` or
`http://127.0.0.1:8765`. DecisionsAI resolves the project from the current
working directory, creates or resumes a Codex IDE session, appends the user
prompt and Codex completion to the linked Decisions chat, and records the run
events for orchestrator progress checks.

If DecisionsAI is switched off or unreachable, the reporter exits quietly by
default so Codex work is not blocked. Use `--strict` only for setup diagnostics.

## Codex Plugin Display

The plugin manifest lives at `.codex-plugin/plugin.json` and declares:

- Display name: **DecisionsAI Codex**
- Category: **Developer Tools**
- Brand color: `#f97316`
- Composer icon: `./assets/icon.png`
- Logo: `./assets/logo.png`
- Screenshot: `./assets/voice.gif`

If the plugin appears without the current icon, logo, or hero image, reinstall it
with the command below. Codex caches local plugins by version, so bump the plugin
version before reinstalling when display assets change.

## Legal And Policy References

The plugin points back to the DecisionsAI public policy documents:

| Document | URL | Policy check date |
|---|---|---|
| Privacy Policy | <https://www.decisionsai.net/privacy> | 2026-05-22 |
| Terms and Conditions | <https://www.decisionsai.net/terms> | 2026-05-22 |

Website check note: on 2026-05-22, both public routes returned HTTP 200, but no
visible "last updated" date was exposed in the fetched page content. Keep the
site policy pages updated separately and make their document dates visible.

Policy coverage should include connected account streams, WhatsApp/Telegram
media, Gmail/Jira/Trello content, IRC/shared chat rooms, voice-note
transcription, screenshots/images, project folders, CLI/IDE execution output,
model-provider calls, workflow audit trails, and internal orchestration memory.

## Open In Codex

After reinstalling, restart Codex or reload plugins, then use:

- [View DecisionsAI Codex](codex://plugins/decisions-codex?marketplacePath=%2FUsers%2Fpaul%2F.agents%2Fplugins%2Fmarketplace.json)
- [Share DecisionsAI Codex](codex://plugins/decisions-codex?marketplacePath=%2FUsers%2Fpaul%2F.agents%2Fplugins%2Fmarketplace.json&mode=share)

## Native Work Loop Target

The intended loop is:

1. DecisionsAI sends Codex a clear goal, acceptance criteria, project context,
   workflow state, and any prior evidence.
2. Codex works inside the project folder through the IDE/chat surface whenever
   possible, using the CLI only as fallback transport.
3. Codex reports prompts, progress, and evidence using the result contract below
   or the project IDE session reporter.
4. DecisionsAI stores the result against the workflow step and ticket.
5. DecisionsAI decides whether to continue, retry, ask the human, escalate to a
   smarter backend/model, or close the ticket.

Workflow-attached work should use the workflow callback. Free project chats
should use the IDE session reporter, which resolves the Decisions project from
the current working directory.

## Local Install / Reinstall

From the DecisionsAI repo root:

```bash
python3 codex_plugin/decisions-codex/scripts/install_local.py
```

The installer copies this plugin to `~/plugins/decisions-codex` and registers it
in `~/.agents/plugins/marketplace.json`. It is safe to rerun after edits. Restart
Codex or reload plugins, then enable **DecisionsAI Codex** from the plugin list.

The marketplace entry should look like this:

```json
{
  "name": "decisions-codex",
  "source": {
    "source": "local",
    "path": "./plugins/decisions-codex"
  },
  "policy": {
    "installation": "AVAILABLE",
    "authentication": "ON_INSTALL"
  },
  "category": "Developer Tools"
}
```

If **DecisionsAI Codex** does not appear in Codex after reinstalling:

1. Restart the Codex desktop app so it rereads `~/.agents/plugins/marketplace.json`.
2. Open the Codex plugins area and install/enable **DecisionsAI Codex**.
3. Confirm the copied plugin exists at `~/plugins/decisions-codex/.codex-plugin/plugin.json`.
4. Confirm the copied assets exist at `~/plugins/decisions-codex/assets/icon.png`
   and `~/plugins/decisions-codex/assets/logo.png`.
5. Confirm `codex` is on PATH with the backend check below.

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
Evidence: ...
Blockers: ...
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
