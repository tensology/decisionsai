# DecisionsAI Multi-Action Orchestration Regression

Date: 2026-05-13

This note captures the hardening pass for large multi-action requests across chat, Telegram, Workflows, project CLI backends, and the Codex path.

## What Was Tightened

- Large or dense user instructions are now detected before model execution and wrapped with an explicit action-queue contract.
- The model-facing instruction tells the agent to preserve the full original request, split it into ordered actions, verify tool results before dependent steps, pause for risky or external actions, and reconcile completed, skipped, failed, and blocked work.
- The same multi-action intake path is applied to normal chat and Telegram input, including image/vision paths and the Ollama provider path.
- WorkflowAgent step prompts now explicitly handle multi-action instructions by queueing actions, checking dependency results, and stopping cleanly on blockers.
- Workflows now send a concise Telegram message when a run pauses for user feedback.
- Workflow completion reports are forwarded to Telegram in a short, readable form when Telegram is connected.
- The Workflows active-runs API route was reordered so `/api/workflows/active-runs?limit=50` is no longer swallowed by `/api/workflows/{workflow_id}` and returning a 422.
- The Workflows UI regression harness now waits for page readiness without relying on network-idle, which is inappropriate for a page with active event streams and polling.
- The Kanban workflow E2E now seeds its own board, lanes, default workflow, and ticket instead of depending on a manually prepared `TEST PROJECT` board.
- Project backend routing now includes explicit editor-extension backends for Cursor IDE and VS Code IDE. These create structured `.tickets` work packets, open the editor, and leave workflow steps eligible to wait for review/continuation instead of treating editor handoff as completed engineering.

## CLI And Codex Readiness

Checked on this OS:

- `codex`: `/Applications/Codex.app/Contents/Resources/codex`
- `cursor-agent`: `/Users/paul/.local/bin/cursor-agent`
- `claude`: `/Users/paul/.local/bin/claude`
- `pi`: `/opt/homebrew/bin/pi`
- `cursor`: `/usr/local/bin/cursor`
- `code`: `/usr/local/bin/code`

Project CLI routing has Pi as the default adapter and includes Cursor CLI, Claude Code, Codex, Cursor IDE, and VS Code IDE adapter entries. The regression test proves a workflow ticket can route through a project selected as `codex`, preserve backend/model metadata, advance to validation, and complete. The IDE backend test proves an editor work packet is written with workflow metadata and the editor is opened through the shared backend layer.

Checked editor extension readiness on this OS:

- Cursor has `decisionsai.decisionsai` installed.
- VS Code has `decisionsai.decisionsai` installed.

The local repo does not contain a component named `CLI Anything`. The relevant existing local piece is `distr/core/agent/tools/integrations/unified_cli.py`, but it is Pi-specific. The external CLI-Anything project is useful as a design reference for structured harnesses: DecisionsAI should keep moving editor and GUI integrations toward typed setup/status/execute/result contracts instead of ad-hoc paste automation.

## Telegram Behavior Covered

Covered by automated tests:

- A waiting workflow sends Telegram a clear pause message with run and step identifiers.
- Disconnected Telegram managers do not receive fake or noisy sends.
- Completed workflows send a concise summary instead of dumping full internal report text.

Remaining manual/live gap:

- A real Telegram reply such as `yes continue`, `retry`, `skip`, or `use X` still needs a live bot-session test to prove it resumes the intended waiting run through the full production transport.

## Regression Results

Passed:

- `python3 -m py_compile distr/core/agent/services/llm/bulk_instruction.py distr/core/agent/services/llm/core_mixin.py distr/core/agent/services/llm/providers/ollama.py distr/core/workflow_agent.py distr/app/workflow.py distr/app/signals.py distr/gui/web/routes/settings/workflows.py distr/core/project_cli_backends/registry.py`
- `python3 -m pytest tests/core/test_multi_action_intake.py tests/core/test_system_prompt_orchestration_section.py tests/core/test_workflow_telegram_notifications.py tests/core/test_codex_workflow_backend_regression.py`
- `python3 -m pytest tests/core/test_project_ide_backends.py tests/core/test_codex_workflow_backend_regression.py tests/core/test_multi_action_intake.py tests/core/test_workflow_telegram_notifications.py`
- `python3 -m pytest -m e2e_playwright tests/ui/test_workflows_playwright.py --browser chromium`
- `python3 -m pytest -m e2e_playwright tests/ui/test_workflows_active_run_webkit.py tests/ui/test_kanban_workflow_e2e_webkit.py --browser webkit`

Result detail:

- Core IDE/Codex/orchestration/Telegram regression pack: 9 passed.
- Workflows Chromium UI suite: 22 passed.
- WebKit workflow run bar plus Kanban workflow E2E: 2 passed.

## Remaining Gaps

- Add a live Telegram continuation test against the actual relay/bot session.
- Add a browser regression for project CLI backend selection in the Projects UI, including visible model/options updates.
- Add a full development-cycle workflow test that starts from a project ticket, routes into the selected CLI or IDE backend, writes evidence back to the workflow step/ticket, pauses for approval, resumes, and summarizes.
- Upgrade the editor extension from "submitted to editor" callbacks to true result callbacks when Cursor/VS Code exposes reliable completion/status signals for the editor agent session.
- Extend destructive/external action confirmation coverage from prompt-level behavior into concrete tool-level tests.
