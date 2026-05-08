---
id: ticket_20260506_013527
title: Diagnostic — workflow runs (tool-call arguments, reports, step routing, Playwright vs tests)
project: Player1Sport
project_id: 4
workspace_folder: /Users/paul/development/WORK/INTRICODE/www.player1sport.com/
created: 2026-05-06 01:35:27
status: open
---

# Diagnostic ticket — failing workflow run reports and step routing

## Description

Create a diagnostic ticket to fix the failing workflow run reports and step routing.

**Problem:**

- The workflow repeatedly fails due to two main issues:
  - Step 1 (Analyze Ticket Requirements), step 3 (Implement Code Changes), and step 5 (Perform Final QA and Cross-Browser Testing) crash because the tool-call arguments are being passed as a JSON string instead of a structured dictionary/object (validation error: `tool_calls.0.function.arguments` must be dict).
  - Steps that use Playwright are being used for non-browser tasks (e.g., validate planning completeness, execute unit/integration tests), and Playwright steps also fail when they try to hit localhost URLs that are not running (`ERR_CONNECTION_REFUSED`).

**Ordered responses / visibility limitation:**

- The assistant currently cannot see the full "ordered response" payloads from workflow reports; only step labels and high-level error summaries appear in the report text shown to the assistant. We need a way to capture and persist the exact failure details including full traceback/logs and any ordered response artifacts so they can be reviewed.
- Investigate whether the workflow engine/database stores ordered responses or step outputs, and add a way to surface them in the UI / workflow report output (or export them) so we can diagnose reliably.

**Workflow design fixes requested:**

- Add discernment/routing between steps: each step should be aware of what should happen next based on validation outcomes, not a strict linear sequence.
- Rework step types:
  - Step 1 should be an agent reasoning/planning step (not Playwright) producing clear outputs (plan, acceptance criteria, risks).
  - Step 2 should validate planning completeness using a text/LLM validation step, not a browser automation step.
  - Unit and integration tests should not be Playwright unless explicitly running end-to-end browser tests; use appropriate test runner execution for unit/integration.
  - Keep Playwright only for actual web UI QA/cross-browser or E2E checks, and ensure the correct base URL/server startup prerequisites exist (or add a server start step).

**Success criteria:**

- Workflow runs no longer fail due to argument formatting.
- Step outputs and failures include full details (traceback/logs and any ordered response artifacts).
- Steps use correct action types and routing logic (on pass/on fail) to loop back when validation fails.
- Test steps run against a running local server or explicitly spin one up, and unit/integration tests use the correct runner.

## Requirements

1. **Tool-call arguments (dict vs JSON string)**  
   - Reproduce failure on workflow agent steps using the same provider that errors (likely **Ollama** with Pydantic v2 message validation).  
   - Ensure conversation history passed to the workflow LLM never leaves `assistant.tool_calls[].function.arguments` as a bare JSON string when the client requires `dict` (mirror `OllamaLLMService._normalize_tool_call_arguments` in ```157:200:distr/core/agent/services/llm/providers/ollama.py```).  
   - Audit ```258:273:distr/core/workflow_agent.py``` (`_validated_messages_openai`) and ```421:451:distr/core/workflow_agent.py``` (`_append_assistant_with_tool_calls`): OpenAI-format history uses `json.dumps` for arguments; Ollama path reuses that list and may reject it on the **second** tool round.

2. **Workflow report verbosity**  
   - Today ```91:119:distr/core/workflow_engine/agent_bridge.py``` truncates each step result to **200 characters** in `_generate_report`. Extend reporting (optional verbosity level, “diagnostic” attachment, or link to full step result rows) so failures include traceback/stderr and structured artifacts—not only titles and short snippets.

3. **Persistence / UI**  
   - Confirm what `AutoWorkflowStepResult` (and related models) already store for each run; expose full output + exit metadata in Kanban/workflow UI or export (JSON/markdown).  
   - Define what “ordered response” means in this product (provider response vs step output); store and surface it explicitly if missing.

4. **Step types and routing**  
   - Map current `action_type` handlers in ```28:44:distr/core/workflow/step_executor.py``` (`execute_code`, `playwright`, `agent_instruction`, etc.).  
   - Redesign Player1Sport preset: planning → `agent_instruction` or LLM-validation step; tests → `run_command` / `execute_code` invoking pytest/etc.; Playwright only for real browser QA; add conditional edges (pass/fail/retry) in workflow definition or dispatcher.

5. **Localhost / Playwright**  
   - Document prerequisite: dev server running or add an explicit “start server” step before any localhost Playwright navigation.  
   - Fail fast with a clear message when `ERR_CONNECTION_REFUSED` instead of ambiguous step failure.

## Context

- **Project:** Player1Sport (ID: 4)  
- **Folder:** `/Users/paul/development/WORK/INTRICODE/www.player1sport.com/`

## Related files (DecisionsAI codebase)

| Area | Path |
|------|------|
| Workflow LLM + tool history | `distr/core/workflow_agent.py` |
| Ollama tool argument normalization (reference) | `distr/core/agent/services/llm/providers/ollama.py` |
| Step execution routing | `distr/core/workflow/step_executor.py`, `distr/core/workflow/dispatcher.py` |
| Report truncation / agent queue | `distr/core/workflow_engine/agent_bridge.py` |
| Run persistence (ORM) | `distr/core/db/workflow.py` (`AutoWorkflowRun`, `AutoWorkflowStepResult`, etc.) |
| Playwright execution | `distr/core/workflow_engine/test_loop.py` (via `TestLoopService._execute_playwright`) |
| Web workflow UI | `distr/gui/web/static/workflows/js/workflows.js` |

## Conversation context

- Auto-generated diagnostic request: failures clustered around tool-call argument validation, Playwright misuse for non-browser steps, localhost not running, and insufficient detail in workflow reports shown to the assistant (200-char truncation and missing ordered-response payloads).

---
*Formatted for Player1Sport — technical anchors point at DecisionsAI repo paths.*
