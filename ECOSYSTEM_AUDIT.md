# DecisionsAI Ecosystem Audit
*Date: 2026-04-29*

---

## Executive Summary

DecisionsAI is a well-structured Python desktop AI assistant whose core architecture is sound, but shows signs of organic growth: a large workflow engine that was recently refactored through extraction (service.py → dispatcher + mixins) still carries several backward-compat shims, two dual-path execution routes for the same step types, and a context-delivery mechanism (the `_agent_report_queue`) that has exactly one consumer in the entire codebase. The ticket-board, workflow, and voice-agent subsystems share a SQLite database but synchronize state through a mixture of direct DB writes, a module-level counter+WebSocket push mechanism, and a signal/queue bridge across a `multiprocessing.Process` boundary — creating three separate synchronization planes that can drift. The migration system (1,927 lines of raw SQL `ALTER TABLE` checks) has outgrown its pattern and is a reliability risk. The tooling layer is well-engineered (semantic retrieval, tiered model support, warm cache) but exposes two code paths — warm-cache retrieval and cold-instantiation fallback — that are simultaneously maintained, with debug logging that suggests the fallback fires regularly.

---

## Findings by Subsystem

### 1. Codebase & Framework Design
**Status:** Moderate

**Layout is logical but layering is partially broken.**
The directory tree follows a sensible breakdown:
```
distr/core/          — domain logic (workflow, kanban, agent, integrations, db)
distr/app/           — application lifecycle (Qt process, agent spawning)
distr/gui/           — web UI server (FastAPI) + Qt oracle window
```
However several cross-layer imports violate the hierarchy:

- `distr/core/workflow/post_execution.py` imports directly from `distr/gui/web/workflow_events.py` (line 18) and again inside methods (lines 83–84). Core domain code should not import from the GUI layer; the GUI module should subscribe to domain events, not the reverse.
- `distr/core/workflow/dispatcher.py` similarly imports `from distr.gui.web.workflow_events import increment_workflow_updated` (line 26) and `from distr.gui.web.kanban_events import increment_kanban_updated` (lines 417, 591). Every step execution now carries a hard dependency on the web server's shared-state module.
- `distr/core/workflow/step_executor.py` imports `from distr.core.workflow.dispatcher import _runs_lock, _active_runs` (lines 514, 578) — a mixin reaching back into the class that hosts it.

**The workflow refactor is half-done.**
`service.py` (779 lines) ends with a block that re-exports every function from `dispatcher.py` via `from distr.core.workflow.dispatcher import ... # noqa: F401` (lines 761–774). This means callers importing from `service.py` silently get `dispatcher.py` symbols; any direct import of `service.start_workflow_run` goes through two module boundaries. The re-export block also exports private internals (`_RunContext`, `_active_runs`, `_runs_lock`, `_cleanup_run`).

**Module size outliers:**
- `distr/core/agent/session.py`: 1,651 lines — manages pipeline, config, hot-swap, STT/LLM/TTS lifecycle, signal bridging, and command dispatch. It should be split into at least `config_builder.py`, `pipeline_manager.py`, and `hot_swap.py`.
- `distr/core/db/migrations.py`: 1,927 lines — 19 migration functions, 101 `ALTER TABLE` / `ADD COLUMN` calls. See Section 7.
- `distr/core/integrations/whatsapp/manager.py` and `telegram/manager.py`: ~1,060 lines each.

---

### 2. Workflow Engine
**Status:** Moderate (recently improved, residual issues remain)

**Execution path is well-structured post-refactor.**
`StepDispatcher` correctly composes `StepExecutorMixin` and `PostExecutionMixin`. The `_RunContext` dataclass tracks per-run agent + event loop. Idempotency guards exist at both `run_isolated` and `run_in_workflow` entry points.

**Dual environment-variable + thread-dict context is a reliability risk.**
`dispatcher.py` maintains two parallel stores for per-run context:
1. `_workflow_thread_env: Dict[int, Dict[str, str]]` — keyed by OS thread ID (lines 56–78)
2. `os.environ["DECISIONS_WORKFLOW_RUN_ID"]` etc. — process-wide (lines 288–291)

Both are updated together (`_set_workflow_thread_env` + `os.environ.[]` assignment) and both are consumed by `get_current_workflow_env()` which falls back from (1) to (2). The comment on line 55 acknowledges `os.environ` is kept "for backward compat with tools that haven't migrated." This dual state is never cleared atomically: `_clear_workflow_env()` (line 169) calls both `os.environ.pop` and `_clear_workflow_thread_env`, but `complete_run()` calls `_clear_workflow_env()` while `cancel_run()` does not (line 326). A cancelled run leaves stale env vars until the next run overwrites them.

**`time.sleep` blocks the routing thread.**
`post_execution.py:94` and `dispatcher.py:375` both call `time.sleep(wait_before)` synchronously in the callback/routing path. If `wait_before_next` is set on several consecutive steps, the thread handling routing is blocked for the full cumulative wait. For the recording playback path, the timeout watchdog (step_executor.py:294) also uses `time.sleep` inside a daemon thread — which is safe but means 300 watchdog threads could exist concurrently for 300 in-flight recording steps.

**`asyncio.run()` inside `_run_send_to_project_cli` is dangerous.**
`step_executor.py:192` calls `asyncio.run(terminal_runtime.get_or_create_session(...))`. If this method is ever called from an already-running asyncio context (e.g., if routing is ever moved onto an async path), this will raise `RuntimeError: This event loop is already running`. The surrounding code has no guard for this.

**Fallback agent path is documented-but-present tech debt.**
`step_executor.py:366–403` contains a fallback `WorkflowAgent` instantiation path for when no `RunContext` exists ("no tools — only text responses"). The comment says "Workflow should be started via `start_workflow_run()`." This path will silently produce degraded results if triggered. The fallback is reached via the `_get_run_context` return value and is not guarded by any feature flag.

**`continue_waiting_step` in dispatcher.py restores only env, not thread-dict.**
`dispatcher.py:376` sets `os.environ["DECISIONS_WORKFLOW_STEP_ID"]` and calls `_update_workflow_thread_step` (which mutates the thread-dict if the entry exists) — but only if the entry was already set for that thread. A workflow resumed from a different request thread will have the thread-dict entry absent, and `get_current_workflow_env()` will fall back to the process-wide `os.environ`, which at that point holds whatever the last run set.

---

### 3. Tooling Layer
**Status:** Good (with minor duplication)

**Two parallel instantiation paths are both actively maintained.**
`load_tools()` (loader.py:520–639) has two distinct code paths:
1. **Cache path** (line 540): If `user_message` is provided and `_tool_cache` is non-empty, delegates entirely to `ToolRetriever.retrieve()` and returns cached instances.
2. **Cold path** (line 557+): Re-instantiates all tools from `_get_tool_definitions()` at call time.

The cold path is still reached in the `RequestToolTool` injection flow (`core_mixin.py:864–873`) and whenever `warm_tool_cache` fails at startup. Debug log at line 597–598 (`"screenshot_analyzer NOT in specialized tools!"`) suggests this cold path fires in production and engineers are tracking it. The cold path re-instantiates ~75 tool objects per LLM call — not trivially cheap.

**`TOOL_DESCRIPTIONS` is the single source of truth for semantic retrieval**, which is correct. But tool names in `TOOL_REGISTRY` use class names as keys while `_tool_cache` is keyed by `tool.name` (the instance's string attribute). `ToolRetriever._collect_descriptions()` uses `TOOL_DESCRIPTIONS.get(class_name)` and logs a warning when missing. Any mismatch between class name and `TOOL_DESCRIPTIONS` key silently degrades retrieval quality.

**`tool_audit.py` re-names its own API parameter.** `record_tool_execution()` accepts `routing_path` and `routing_hint` (line 8–9) and says "routing_hint is an alias for routing_path" (line 33). A single unified parameter would be cleaner.

**`ALWAYS_ON_NAMES` is a module-level set in `tool_retriever.py`** that is never configurable at runtime without a restart. If a user wants `screenshot_analyzer` always available, they cannot add it without code changes.

---

### 4. Project System
**Status:** Moderate

**Project resolution is duplicated across multiple callers.**
`step_executor.py` resolves `linked_project_id` from two places in `_run_code_type` (lines 73–87) and `_run_send_to_project_cli` (lines 155–181) using nearly identical query patterns. The same "check step.linked_project_id, then fall back to run.run_data.project_id" logic is copy-pasted rather than extracted to a helper.

**`send_to_cli` bypassing logic is a silent no-op.**
`_run_send_to_project_cli` returns `{"passed": True, "skip_wait": True}` with the message "Bypassed: no linked project context available" (line 185–189) when no project is found. The step is marked as passed even though nothing was sent. Downstream routing and auditing see a green step that actually did nothing.

**`KanbanBoard.default_project_id`** (db/kanban.py:36) is declared but there is no code path in `kanban/agent.py` that injects this into the workflow run's `run_data.project_id` when the board has a default project. The resolution in `step_executor.py` can only find a project if the run was started with explicit `run_metadata={"project_id": N}`.

---

### 5. Ticket Boards and Subagents
**Status:** Moderate

**`workflow_status` synchronization is best-effort.**
`KanbanTicket.workflow_status` (db/kanban.py:98) is written in two places: `start_workflow_run()` (dispatcher.py:283) sets it to `"running"`, and `_finalize_terminal_run()` (dispatcher.py:158) sets it to the final status. If the app crashes between these two writes, the ticket remains permanently in `"running"` state with no recovery path.

**`parent_ticket_id` subagent hierarchy exists in the schema but has no service-layer enforcement.**
`KanbanTicket.parent_ticket_id` (db/kanban.py:101) allows a tree of subagent-spawned tickets, but there is no query in `kanban/agent.py` that respects this hierarchy when selecting the next ticket to process. The agent picks tickets from the source lane by position, not by parentage.

**WhatsApp and Telegram bridge ticket creation is undocumented in the schema.**
`KanbanTicket.whatsapp_message_id` and `whatsapp_message_wa_id` (db/kanban.py:94–96) link a ticket back to its originating WhatsApp message, but there is no `telegram_message_id` equivalent. Telegram-originated tickets have no traceability back to the source message in the schema.

**`IntegrationReconnectMixin` is correctly extracted** (integrations/base.py) and both WhatsApp and Telegram managers now use it. This is the right pattern and works well.

---

### 6. User Interface
**Status:** Good

**WebSocket push is correctly fire-and-forget**, with stale connection pruning in both `kanban_events.py` and `workflow_events.py`. The ring-buffer catch-up in `workflow_events.py` (`_event_log`, maxlen=200) allows reconnecting clients to recover missed events.

**`kanban_events.py` has no catch-up buffer**, only a counter. A client that disconnects and reconnects will see the current counter but not which specific events it missed, requiring a full board reload. `workflow_events.py` has the superior pattern; `kanban_events.py` should match it.

**`workflow_events.py` push payload is too minimal** — it sends only `{"type": "workflow_updated", "version": N}` (line 44). Clients must re-fetch the entire workflow state on every push. The kanban push includes `board_id`, `event`, and `payload`, which is strictly better. Workflow WS should be upgraded to the richer format.

**The `AgentSession` class (`session.py:1,651` lines) conflates concerns.** `_load_config()` (278–456) queries the database directly even before `_create_services()` has run, using raw `session = get_session()` calls with manual `session.close()` (lines 252–266, 332–391) instead of the context manager pattern used everywhere else in the codebase. If `session.close()` is not reached due to an exception inside the try block, the session leaks.

**Hot-swap is functionally correct** but sets a private Pipecat attribute directly: `setattr(self.tts_service, '_FrameProcessor__started', True)` (session.py:1116). This is a name-mangled internal Pipecat attribute and will silently break if Pipecat changes the attribute name.

---

### 7. Docs, Preferences, and Configuration Consistency
**Status:** Weak

**`migrations.py` is a 1,927-line time bomb.**
The module contains 19 separate `run_*` functions, each doing `SELECT ... LIMIT 1` to check whether a column exists, then `ALTER TABLE ADD COLUMN` if it does not. All 19 are called from `run_migrations()`. There is no versioning, no idempotency guarantee beyond try/except, and no rollback. Adding a migration requires editing this single file, which accumulates conflicts as the team grows. A lightweight version-table approach (Alembic or a custom `schema_version` integer) would replace all 101 ALTER statements with versioned, reversible migrations.

**`DEFAULT_SETTINGS` in `settings.py`** (lines 25–100+) is a flat dictionary with 90+ keys covering everything from `load_splash_sound` to `kanban_agent_orchestrator_model`. There is no grouping, no type annotation, and no validation schema. Settings that belong to integrations (kanban agent, Telegram, Jira/Trello) are mixed with audio settings and UI preferences.

**Settings service (`settings_service.py`) is partially adopted.** The `update_setting()` helper and `save_general_settings()` are well-designed. However, many settings route handlers in `distr/gui/web/routes/settings/` still call `load_settings_from_db()` + `save_settings_to_db()` directly (e.g., `advanced.py:44`, `initiative.py:45`). The service layer is not consistently enforced.

**Two separate initiative-settings sources.** `settings/initiative.py` defines `DEFAULTS` and `INITIATIVE_FIELDS` locally (lines 10–26) rather than referencing `DEFAULT_SETTINGS` in `settings.py`. If a default changes in one place it will not propagate to the other.

**LLM model resolution is duplicated.** `initiative/service.py:51–72` defines `_litellm_model()` which maps provider names to litellm model strings. The same mapping exists in `distr/core/llm_factory.py`. Two authoritative maps will diverge.

---

### 8. Context Management
**Status:** Moderate**

**`_agent_report_queue` has exactly one consumer at a fragile call site.**
`WorkflowAgentBridge.queue_report_to_agent()` pushes reports to the module-level `_agent_report_queue` (agent_bridge.py:46). The only consumer is `on_workflow_finished` in `distr/app/signals.py:349`, which is connected to the `workflow_finished` Qt signal. This means:
1. Reports are only consumed when `workflow_finished` fires.
2. If the signal fires before the consumer connects (early startup), reports accumulate without bound — the queue has no max size.
3. The consumer (signals.py:354) does NOT call `get_pending_reports(session_id=session_id)` — it calls `get_pending_reports()` without a session filter, then manually re-queues non-matching items (lines 354–360). This is a correct pattern but brittle: if the same session fires `workflow_finished` twice (e.g., isolated step + full run), the second call will see an empty queue because the first already drained it.

**Context assembly flow is bidirectional in theory but unidirectional in practice.**
`assemble_step_context()` (context_assembly.py:81) assembles context **into** a step before execution. After execution, `_augment_agent_result_with_tool_evidence()` (step_executor.py:663) appends tool evidence to the result. But the assembled context (including `StepInputContext.resolved_variables`) is not persisted back to the database — it is recomputed from scratch on every step. If `prior_results` changes between steps (e.g., due to a concurrent run or a manual edit), the resolved variables for a future step will silently differ from what the current step saw.

**`_build_agent_prompt` opens two separate DB sessions for the same workflow** (step_executor.py:435, 475). The first loads `wf` and `step_obj`; the second loads `AutoWorkflowStepResult` records. Between these two queries, another thread could commit a new step result, causing an inconsistent view of workflow history within a single step's prompt assembly.

**Chat context in `AgentSession._load_config()`** queries the DB twice for the current chat: once via `Settings` (line 337) and once via `ChatManager` if it exists (line 374). On hot reload, the DB query runs before `ChatManager` is initialized, so the fallback path (lines 374–391) is only reached when `_load_config` is called again after creation — but `_load_config` is only called explicitly in one other place (`reload()`). The two paths can yield different chat IDs if the DB was updated between the two calls.

**No context window trimming is visible in the search results** for `base_service.py`. If the agent accumulates a long session, message history grows unboundedly until the LLM provider returns a context-length error. There is no `MAX_HISTORY` or `trim_history` pattern visible in the grep results.

---

### 9. Overall Coherence
**Status:** Moderate

**The core feedback loop — user speaks → agent acts → workflow runs → board updates — is wired correctly** via the signal→queue→event_queue pipeline, with `workflow_finished` → `on_workflow_finished` → agent command queue for voice reporting. However the three synchronization planes (DB writes, WebSocket push counter, Qt signal bridge) are not atomically consistent:

1. A workflow step's result is written to `AutoWorkflowStepResult` (DB) inside `_record_result`.
2. `increment_workflow_updated()` is called immediately after (workflow_events.py), pushing the version counter.
3. The kanban ticket's `workflow_status` is updated later in `_finalize_terminal_run()`.

A web UI client that receives the `workflow_updated` WebSocket event and immediately fetches the ticket's status may see stale `workflow_status` because (3) hasn't run yet.

**The chat↔workflow link (`AutoWorkflow.chat_id`) enables audit mirroring** via `_append_workflow_step_audit()`, which writes tool execution evidence into the chat's audit session. This is a good pattern for observability but creates a write-path dependency: every workflow step now touches both the `auto_workflow_step_results` table AND the audit system, doubling the DB write surface per step.

**`KanbanAgentCheckIn` in `kanban/agent.py`** calls `start_workflow_run()` from `distr.core.workflow.service` (line 18), which is actually the dispatcher's `start_workflow_run` re-exported via service.py. The board agent, workflow dispatcher, and ticket model are all coherently linked. What is missing is a rollback path: if the workflow run fails and the ticket was not moved to the done lane, the board agent does not retry on its next tick (there is no `retries` counter on `AgentStatus`).

---

## Critical Issues (P0)

- **Context-rot from dual env-var stores**: `cancel_run()` (dispatcher.py:309–328) does not call `_clear_workflow_env()`, leaving `DECISIONS_WORKFLOW_RUN_ID` / `DECISIONS_WORKFLOW_ID` pointing at the cancelled run. The next workflow started from the same thread will overwrite them, but a tool that reads `get_current_workflow_env()` between cancel and restart will get stale run/step IDs. [dispatcher.py:309–328]

- **`_agent_report_queue` has no size bound**: Reports from all workflow completions accumulate in an in-process `queue.Queue()` (agent_bridge.py:16) with no `maxsize`. In a long session with many workflow runs that complete before `workflow_finished` is handled (or if `workflow_finished` never fires, e.g. in the web-only path without a Qt signal), this queue grows without bound. [agent_bridge.py:16]

- **`asyncio.run()` inside a sync routing callback** in `step_executor.py:192` (`_run_send_to_project_cli`). If the call chain ever enters an already-running event loop (e.g. if routing is refactored to be async), this raises `RuntimeError` and silently swallows the exception in the outer try/except. [step_executor.py:192]

- **DB session leak in `AgentSession._load_config()`**: Lines 252–266 and 332–391 open `session = get_session()` with manual `session.close()` calls. Exceptions thrown between `get_session()` and `session.close()` (e.g. the warning log at line 352) leave the session open. SQLite does not surface this immediately but it causes resource exhaustion under restart cycles. [session.py:252–266, 332–391]

---

## High Priority Issues (P1)

- **Core importing GUI module** (`post_execution.py:18`, `dispatcher.py:26`): `distr/core/workflow/` imports `distr/gui/web/workflow_events.py` and `kanban_events.py`. This inverts the dependency hierarchy. The workflow engine should publish domain events (e.g. via signals or a simple callback registry) and the GUI layer should subscribe. [post_execution.py:18, dispatcher.py:26]

- **`migrations.py` pattern does not scale**: 1,927 lines of `try: SELECT; except: ALTER TABLE` with no version tracking. Adding a column that already exists in some installs but not others creates ambiguous state. [migrations.py:1–1927]

- **`service.py` re-exports private dispatcher internals**: `from distr.core.workflow.dispatcher import _RunContext, _active_runs, _runs_lock` etc. (service.py:761–774). Callers of `service.py` now have access to mutable internal state of the dispatcher. [service.py:761–774]

- **`send_to_cli` bypass is a silent false-positive**: When no project is found, `_run_send_to_project_cli` returns `{"passed": True, "skip_wait": True}` with a bypass message. The step is marked passed in the audit trail even though no CLI command was sent. [step_executor.py:183–189]

- **Two parallel `_litellm_model` mappings**: `initiative/service.py:51–72` and `distr/core/llm_factory.py` both map provider names to litellm strings. They will diverge silently when new providers are added. [initiative/service.py:51–72]

- **`load_tools()` cold path re-instantiates ~75 objects per call**: When `user_message` is absent or `_tool_cache` is empty, `load_tools()` re-instantiates the full tool set (line 557+). This is the path used by `RequestToolTool` injection and any call before `warm_tool_cache` completes. [loader.py:557–639]

---

## Medium Priority Issues (P2)

- **`kanban_events.py` has no catch-up buffer**: WebSocket clients that reconnect cannot recover missed events; they must do a full board reload. [kanban_events.py:1–70]

- **`workflow_events.py` push payload carries no semantic content**: The push only includes a version counter, forcing full state refetches on every event. [workflow_events.py:44]

- **`KanbanBoard.default_project_id` is never injected into runs**: The DB column exists (kanban.py:36) but `kanban/agent.py` does not pass it as `run_metadata={"project_id": N}` when calling `start_workflow_run`. Send-to-CLI steps will always bypass for board-initiated runs unless the step has an explicit `linked_project_id`. [kanban/agent.py:~80, kanban.py:36]

- **`KanbanTicket.workflow_status` can be stranded at "running"**: If the process crashes between `start_workflow_run` (which sets `"running"`) and `_finalize_terminal_run` (which sets the final status), the ticket is permanently stuck. There is no recovery query at startup. [dispatcher.py:283, 158]

- **Telegram-originated tickets have no message traceability**: `KanbanTicket` has `whatsapp_message_id` but no `telegram_message_id`. [db/kanban.py:94–96]

- **`AgentSession` is 1,651 lines**: The class handles pipeline construction, all service hot-swaps, config loading, signal bridging, and command dispatch. It should be decomposed. [session.py:1–1651]

- **Project resolution logic is copy-pasted** between `_run_code_type` and `_run_send_to_project_cli`. [step_executor.py:73–87, 155–181]

- **`_build_agent_prompt` opens two DB sessions for the same workflow view** with a possible inconsistency window between them. [step_executor.py:435, 475]

- **`setattr(self.tts_service, '_FrameProcessor__started', True)`** accesses a name-mangled Pipecat internal. [session.py:1116]

- **`ALWAYS_ON_NAMES` tool set is not user-configurable** at runtime. [tool_retriever.py:36–42]

- **No context window trimming** is implemented for long-running agent sessions; message history grows without bound. [session.py, base_service.py]

---

## Architectural Recommendations

1. **Introduce a domain-event bus between core and GUI.** Replace the direct imports of `increment_workflow_updated` / `increment_kanban_updated` inside `distr/core/workflow/` with a lightweight callback registry (or use the existing `signal_manager`). Core modules register events; the GUI layer subscribes. This restores the proper dependency direction and makes core testable without a running web server.

2. **Consolidate workflow execution entry points.** `service.py` should contain only CRUD and query helpers. All execution functions (`start_workflow_run`, `execute_step`, `cancel_run`, `complete_run`, `continue_waiting_step`) should be importable directly from `dispatcher.py`. The re-export block in `service.py` should be removed; callers should update their imports. This eliminates the two-module-boundary indirection.

3. **Replace migrations.py with a version-table migration system.** Add a single `schema_version` INTEGER column to the settings table (or a separate `_schema_migrations` table). Run only new migrations. Consider Alembic for reversibility; at minimum, number migrations and gate each on `schema_version < N`. This replaces 1,927 lines of try/except ALTER with O(N) numbered functions.

4. **Single project-resolution helper.** Extract the `linked_project_id` lookup (step → run → run_data → project) into one function in a `distr/core/workflow/context.py` or `project_resolver.py` and call it from both `_run_code_type` and `_run_send_to_project_cli`.

5. **Bound `_agent_report_queue` and add a consumer at the web route level.** Set `maxsize=500` on the queue. Add a `/api/workflows/pending-reports` endpoint (or a websocket event) so the web UI can also consume workflow completion reports — not only the Qt `workflow_finished` signal path. This also enables users without a running voice agent to receive workflow summaries.

6. **Unify `_litellm_model` into `llm_factory.py`.** Delete `initiative/service.py:_litellm_model` and import from the factory. Add a test that asserts both callers produce the same output for the same inputs.

7. **Harden `KanbanTicket.workflow_status` recovery.** Add a startup check in `kanban/agent.py` (or `migrations.py`) that queries `AutoWorkflowRun` for all runs in `running`/`waiting` status whose process is no longer in `_active_runs`, and sets their `workflow_status` to `"failed"` with a recovery note.

8. **Upgrade `kanban_events.py` to match `workflow_events.py`** — add the ring-buffer catch-up pattern and include semantic event payload (event type + ticket ID) in every push so clients can perform partial updates instead of full reloads.

---

## Conclusion

DecisionsAI's architecture is coherent at the macro level: the separation of voice pipeline (Pipecat), web UI (FastAPI/aiohttp), workflow engine, and kanban agent reflects a deliberate design. The recent extraction of `StepDispatcher` + mixins, `IntegrationReconnectMixin`, `context_assembly.py`, and `variable_resolver.py` shows active quality improvement. The most pressing concerns are structural: the inverted core↔GUI dependency, the unbounded report queue, the stranded-run risk, and the migration system that has scaled beyond its original one-file pattern. None of these represent fundamental design failures — they are the natural accumulation of rapid product iteration. Addressing items P0 and P1 would substantially reduce operational risk and make the system easier to test in isolation.
