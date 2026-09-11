# Workflow and automation harness audit

Date: 2026-09-02

## Surface map

- DecisionsAI workflow engine: canonical Development workflow, role-aware coordination plan, step-level model policy, local and hosted project CLI backends, independent review, validation, correction, and reporting.
- DecisionsAI harness projection: runtime workflow pre-chain merge, bundled skills registry, Headroom, Agent Watchdog, browser QA, Playwright, and project skill projection.
- Automation engine: first-class automation records, durable Development threads, scheduler, run ledger, imported Codex/Cursor/Claude schedules, manual run route, and background execution.
- UI: Development Scheduled tasks workspace, import source menu, automation editor, provider/model catalogs, run history, and workflow editor.
- Local harnesses present: Codex commands, Pi skills, native DecisionsAI skills registry, Cursor/Codex MCP configurations, and RTK.

## Confirmed findings

| ID | Type | Finding | Evidence | Confidence | Effort |
| --- | --- | --- | --- | --- | --- |
| WF-1 | Update | The bundled Development v12 definition is useful for local and hosted development, but the live database still holds the older six-step definition with no canonical version and a remote-free resolved model plan. | Workflow 374 has six steps and no `canonical_workflow_version`; the bundled preset has seven steps, `prefer_free_local`, adaptive multi-model routing, independent validation, correction, final polish, and reporting. | High | Medium |
| WF-2 | Enhance | Runtime pre-chain merging correctly supplies the default harness even when the stored workflow pre-chain is empty, but the UI and serialized workflow do not make that effective baseline obvious. | `skill_provision.py` calls `merge_harness_pre_chain`; live workflow 374 persists `pre_chain: []`. | High | Small |
| WF-3 | Update | The live Ideation workflow is not executable because it has zero steps. | Workflow 399 serializes with an empty step list. | High | Small |
| WF-4 | Update | Two generated issue workflows are not adequate development workflows. They contain 10 or 11 loosely scoped `run_command`/Playwright steps with no step configuration, review contract, model policy, or correction loop. | Workflows 411 and 412 in the live database. | High | Medium |
| AU-1 | Update | Import immediately executes after source selection and gives no opportunity to choose provider, model, complexity, reasoning, execution environment, or whether existing imports should be updated. | `studio.js::importAutomationSource` posts only `{sources}`; `AutomationImportRequest` accepts only `sources`. | High | Medium |
| AU-2 | Update | Codex imports inherit `backend=codex` and default `provider=openai`, so source identity is incorrectly treated as the future execution route. | `_normalized_record` in `automation/imports.py`; live imported automations are predominantly OpenAI/Codex. | High | Medium |
| AU-3 | Fix | Duplicate-safe imports always skip an existing import, so a corrected route cannot repair already imported tasks. | `import_automations` appends `already_imported` without applying route overrides. | High | Small |
| AU-4 | Fix | Active automation runs can survive a process restart as permanent `running` rows and block later scheduled execution. | Live automations 4, 5, and 7 have stale running rows; startup only cleans orphaned workflow runs. | High | Small |
| AU-5 | Fix | Importing the automation store during database bootstrap emits a circular-import migration warning. | Importing `distr.core.automation.store` reproduces the warning from `ensure_automation_schema`. | High | Small |
| AU-6 | Enhance | Complexity is not carried from imported automation configuration into Development routing assessment, so adaptive execution cannot use the operator's declared task complexity. | Import action config has no complexity; `_dispatch_development_automation` does not pass a routing assessment. | High | Small |

## Rejected findings

- The default harness is not actually absent from workflow steps. The persisted pre-chain is empty, but runtime projection merges the Decisions harness stack, Headroom, Watchdog, browser QA, and Playwright.
- The bundled Development workflow is not Codex-only. Its current definition supports local-first planning/implementation, hosted fallback, independent review, Codex final polish when changes warrant it, and a low-cost reporting pass.
- Automations do have durable threads and scheduler timestamps. The main defects are route import, stale-run recovery, and old live records, not a missing scheduler model.

## Benchmarks and verification commands

```text
python -m pytest -q tests/core/test_loop_presets.py tests/core/test_workflow_coordination_plan.py tests/core/test_execution_contracts.py
python -m pytest -q tests/core/test_scheduled_automation_imports_and_threads.py tests/core/test_automation_store.py tests/core/test_automation_tool_execution.py tests/core/test_channel_automations.py
python -m pytest -q tests/ui/test_development_harness_playwright.py -k scheduled_import
python -m py_compile distr/core/automation/imports.py distr/core/automation/scheduler.py distr/core/automation_orchestrator.py
```

## Recommended next action

Upgrade only the canonical Development definition to the bundled version while preserving its history, make the import operation explicitly route-aware through a clean modal, allow route updates for prior imports, carry complexity into deterministic Development routing, and recover orphaned automation runs at startup. Keep the unrelated generated workflows visible for manual review rather than deleting or silently rewriting them.

## Audit result

- DecisionsAI workflow harness: MISS until the live canonical workflow is upgraded.
- Runtime harness projection: PASS.
- Adaptive local/hosted routing in the bundled workflow: PASS.
- Automation import routing: MISS.
- Automation scheduler persistence: PASS.
- Automation orphan recovery: MISS.
- Codex, Cursor, Pi, and RTK availability: PASS for detected surfaces.
- Claude, Gemini, and Cline project surfaces: SKIP because they are not present in this project.

## Remediation outcome

The confirmed defects were addressed after this audit snapshot:

- Canonical Development workflow 374 was upgraded to version 13 with seven stages. Its former definition and run evidence were preserved in archived audit workflow 413.
- Live routing was refreshed. Planning, implementation, and correction now start on local Muse Glimmer; review, validation, and reporting use an independent local Ollama model; final production polish remains on Codex Auto.
- The import source action now opens a route modal for provider, model, complexity, reasoning, execution location, adaptive routing, and updates to existing imports.
- Existing imports can be rerouted without duplication while original route metadata is retained.
- All ten discovered Codex schedules now use Ollama/Muse Glimmer as their local starting route and own independent Development threads in adaptive route mode.
- Automation complexity and the selected starting route now enter the deterministic Development routing assessment.
- Startup reconciliation closes orphaned automation runs, and the circular migration initialization path was removed. Three stale live runs were recovered.
- Verification passed: 197 focused backend tests, one Playwright import-flow test, Python compilation, dependency checks, and `git diff --check`.
