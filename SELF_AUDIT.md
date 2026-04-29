# DecisionsAI Self-Audit Report
*Date: 2026-04-29*

---

## Executive Summary

This audit covers the DecisionsAI agent runtime across six areas: TTS output quality, reasoning model coherence, context system, tool orchestration, self-reflection, and integrations. The system is architecturally mature — the Pipecat pipeline, semantic tool retrieval, and multi-provider TTS/LLM hot-swap are well-implemented. However, three systemic gaps stand out: (1) no SSML or prosody control layer means TTS rhythm is entirely controlled by the upstream model, not the voice pipeline; (2) there is zero self-reflection machinery in the runtime — no module tracks past tool executions, detects contradictions, or adjusts prompts based on prior failures; and (3) subagent context inheritance is implicit and one-directional, with the `WorkflowAgent` receiving no structured handoff from the parent voice session. Addressing these three gaps would meaningfully raise agent coherence and voice naturalness.

---

## Findings

### 1. TTS Output Quality

**Status: Moderate**

**Strengths:**

The text-cleaning pipeline (`distr/core/agent/services/llm/text_utils.py:11`) is thorough: it strips markdown formatting, emoji characters outside the Latin extended-A range, leaked tool-call JSON artifacts, chain-of-thought bleed (`<think>...</think>`, `Your tool output was:`), URL substitution to "a web link", and path substitution to "a file path". Provider dispatch is cleanly abstracted through `tts_registry` (`distr/core/audio/tts_handler.py:76`), and the deduplication guard in `_cmd_speak_text_directly` (`distr/core/agent/command_handler.py:700-708`) prevents double announcements within a 4-second window.

**Issues:**

**No SSML / prosody control layer.** The `generate_tts_audio` function (`distr/core/audio/tts_handler.py:39-79`) passes raw cleaned text directly to each provider's `generate_audio()` without any speech markup. For Kokoro (offline) this means all sentence pacing, emphasis, and emotional tone are left entirely to the model's default neural cadence. There is no ability to insert pauses between tool-result items, slow down for important conclusions, or add emphasis to key words. ElevenLabs supports stability/similarity/style tuning (`distr/core/agent/command_handler.py:357-368`) but these are static settings, not per-utterance.

**Phoneme correction map is context-agnostic.** The word correction table in `distr/core/paths.py:170-190` remaps commands like `pause` → `paws`, `zoom` → `boom`, `screen` → `scream`. These phoneme hacks only apply when Kokoro mispronounces the word — but the substitution is applied regardless of sentence context, so a sentence like "the screen will appear" becomes "the scream will appear" at TTS input. There is no context-aware disambiguation. The substitution dict is defined but it is not clear which code path actually applies it during live TTS (it is not imported in `tts_handler.py`).

**Robotic phrasing after tool execution.** When a tool completes, the workflow bridge generates a `[Instruction: Give a brief spoken response...]` suffix appended to the step summary (`distr/core/workflow_engine/agent_bridge.py:124-133`). This instruction text is passed to the LLM as part of the report — so the LLM sometimes reads that instruction literally rather than using it as guidance. The Instruction tag is not stripped before TTS.

**No sentence-boundary chunking for streaming.** The `clean_text_for_tts` function (`text_utils.py:11`) strips all content without checking whether the streaming chunk is mid-sentence, potentially creating abrupt cutoffs at chunk boundaries. There is no lookahead or sentence-reassembly stage.

---

### 2. Reasoning Model Coherence

**Status: Moderate**

**Strengths:**

The three-layer routing model in `tool_routing.py` is well-conceived: `FastActionDetector` (regex bypass) → `detect_request_type()` (heuristic intent classification) → semantic `ToolRetriever` (embedding similarity). The `fast_tool_matcher` in `base.py:152-274` correctly skips fast-matching when contextual references are detected (`that email`, `based on that`), forcing LLM orchestration for multi-step chains.

The `_CONTEXT_REFS` regex (`distr/core/agent/tools/base.py:174-180`) is a correct guard against over-eager fast-matching; this prevents the common failure mode of matching a trigger word inside a conversational sentence.

**Issues:**

**No contradiction detection across prompts.** The `_messages` list (`distr/core/agent/services/llm/core_mixin.py:920`) is a flat conversation array. There is no cross-turn analysis to detect when a new instruction contradicts an earlier committed action (e.g., "make the file red" followed two turns later by "I never said that"). The agent will silently comply with each instruction in sequence.

**Context decay is unmanaged.** There is no token-budget mechanism in the main agent's `_messages` list. The `on_chat_changed` method (`core_mixin.py:926`) loads the full chat history via `chat_manager.get_chat_history(chat_id)` without any sliding-window or summarisation strategy. As a long session grows, the effective context window is consumed by early messages the model attends to poorly. There is no graceful degradation path (e.g., summarise messages older than N turns).

**Model tier gate is binary.** The `classify_model_tier` method (`tool_retriever.py:150-188`) returns `"micro"`, `"small"`, or `"standard"`. Micro models receive only 6 always-on tools. But there is no mechanism to adjust the *system prompt complexity* alongside the tool budget — a micro model gets the same full-length system prompt as a standard model, which likely overwhelms its context handling.

**Workflow step reasoning is prompt-only, not verified.** The `_run_agent` path in `step_executor.py:312-404` sends an enriched prompt to the `WorkflowAgent` and trusts its output. If the agent produces a response that doesn't execute the required action (e.g., describes what it would do rather than doing it), validation only fires if the step has `validation_type != "none"` — an opt-in setting, not a default.

---

### 3. Context System

**Status: Moderate**

**Strengths:**

The `assemble_step_context` function (`distr/core/workflow_engine/context_assembly.py:81-146`) implements a clear context-matrix: `agent_instruction` steps get full context (workflow input + rules + prior results); `run_command` and `http_request` get variables only; `play_recording` gets nothing. This is a thoughtful data diet per step type.

The `variable_resolver.py` (`distr/core/workflow_engine/variable_resolver.py:19-51`) cleanly builds a `{{step_N}}` / `{{step_N.field}}` namespace from prior step outputs, and unresolvable placeholders are left as-is with a logged warning. This prevents silent data loss.

Per-thread workflow environment (`dispatcher.py:60-96`) using `_workflow_thread_env` supplements `os.environ` for concurrent workflow runs, preventing environment variable collisions between simultaneous runs.

**Issues:**

**No context expiry or memory decay for the voice agent.** The `ChatManagerCore` uses an LRU dict with `maxsize=50` for `chat_histories` (`chat_manager.py:72`). This is a UI cache, not a token-aware memory manager. The in-process `_messages` list for the active chat has no upper bound — sessions with hundreds of turns will silently accumulate context, potentially causing context-length errors at the LLM API level with no graceful fallback.

**Prompt contamination risk from workflow report format.** The `WorkflowAgentBridge._generate_report()` (`agent_bridge.py:91-133`) appends a literal `[Instruction: ...]` directive into the report string that is then queued to the voice agent's LLM via `process_chat_input`. The agent LLM is expected to interpret this as an instruction rather than output it verbatim. If the model is in a non-instruction-following mode (e.g., low temperature Ollama model, or a context overflow state), the `[Instruction: ...]` text will appear in the spoken response verbatim.

**Subagent context is isolated, not inherited.** The `WorkflowAgent` (`workflow_agent.py:31-63`) instantiates its own `_messages: []` fresh, loads tools from cache, and resolves its own provider/model from settings. It receives no reference to the parent voice agent's conversation history. This means a workflow step agent cannot reason about "what was the user discussing before this workflow started" — it only sees the step prompt and prior step results. This is by design for isolation, but it creates a gap when workflow steps need broader conversational context.

**Initiative service context assembly is unguarded.** The `InitiativeService` (`initiative/service.py`) assembles context (chat history, ticket boards, workflows) and asks the LLM to propose one action. There is no guard against the LLM proposing an action type that was already proposed and skipped in the previous cycle — the `duplicate_recent` flag in `policy.py:42` is checked but must be set externally by the caller; the service itself does not track its own proposal history across cycles.

---

### 4. Tool Orchestration

**Status: Good**

**Strengths:**

The semantic retrieval pipeline is well-architected: `build_index_async` (`tool_retriever.py:66-95`) builds the index once in a daemon thread; `retrieve()` (`tool_retriever.py:289-347`) handles the micro/small/standard tier split and merges always-on tools with the semantic result set. The `request_tool` meta-tool (`loader.py:187`) provides a graceful fallback when a needed tool is not in the active set.

The `_COMMAND_MAP` in `command_handler.py:1114-1146` is a clean dispatch table with no if/elif chains, and every handler is wrapped in try/except to prevent one bad command from crashing the pipeline.

Tool execution is audited to the workflow audit log via `record_tool_execution` (`tool_audit.py:14-50`), providing a persistent record in Settings > Workflows.

**Issues:**

**`load_tools` has a dual code path that diverges.** When `user_message` is provided, `load_tools` (`loader.py:520-555`) returns from the cache path and exits early, never loading accessibility/sidecar tools. When `user_message` is absent it falls through to the full loading path which *does* load accessibility and sidecar tools (`loader.py:604-636`). If the cache path is triggered, sidecar tools are silently unavailable with no warning. This is a behavioral divergence that could manifest as intermittent tool-not-found failures for sidecar-only tasks.

**Always-on tool set has no intent weighting.** The 6 `ALWAYS_ON_NAMES` (`tool_retriever.py:36-43`) are `smart_open`, `execute_code`, `oracle_control`, `mode_control`, `new_chat`, `system_info`. These are broadly useful but not tuned by conversation context. A user actively writing code would benefit from `file_operations` and `git_operations` always being present, but those are retrieval-only. There is no mechanism to adjust the always-on set based on an active project context.

**Fast matcher confidence threshold is static.** `fast_tool_matcher` (`base.py:271`) uses a fixed threshold of 0.85. There is no per-tool threshold tuning — a tool like `media_control` that matches on common words (`play`, `pause`) may trigger at 0.90 confidence when the user is asking a question about media, not issuing a command. The length ratio check (line 231-236) partially mitigates this, but short sentences like "play me something" would still score high and bypass the LLM.

**Redundant tool instantiation path not gated.** When `user_message` is not provided (e.g., during session initialization or certain fast-action paths), `load_tools` re-instantiates all tools from scratch (`loader.py:562-592`), ignoring the module-level `_tool_cache`. This wastes ~100-300ms and re-imports all modules. The warm cache path exists precisely to avoid this but the guard at line 540 (`if user_message and _tool_cache`) means any call without a message bypasses it.

---

### 5. Self-Reflection Layer

**Status: Weak — significant gap**

**No self-reflection module exists in the runtime.**

The grep for `self_reflect`, `reflection`, `corrective` across all Python files in `distr/` returned zero results. There is no module, class, or function in the runtime that:

- Summarizes past tool executions for the agent to reason over
- Detects when the same tool has been called N times with the same parameters without progress
- Identifies contradictions between the current instruction and recent history
- Adjusts prompts based on prior failure patterns
- Produces a "what I did last session" summary for the next session

**What exists as a partial substitute:**

- `tool_audit.py` appends every tool execution to a per-chat workflow audit log. This is readable from the UI (Settings > Workflows) but is never fed back into the agent's `_messages` context. The audit is write-only from the agent's perspective.
- The `WorkflowAgentBridge._generate_report()` (`agent_bridge.py:91-133`) produces a human-readable step summary that is queued to the voice agent. This is a *post-hoc narration*, not a corrective loop.
- The `InitiativeService` runs a periodic context-assembly and LLM-proposal cycle (`initiative/service.py`), which resembles proactive self-direction. But it does not examine *what went wrong* — it only assembles fresh context and proposes the next action.
- `WorkflowAgent._augment_agent_result_with_tool_evidence` (`step_executor.py:663-695`) appends tool evidence to the result text so the next step sees what happened. This is a step-chain memory mechanism, not a self-correction mechanism.

**No loop-break heuristic.** If the agent enters a failure loop (tool call fails → agent retries same tool → fails again), there is no circuit breaker. The only protection is `step.max_retries` at the workflow level (`dispatcher.py` line 629), which defaults to 0. For ad-hoc voice requests outside workflows, there is no retry or loop-break at all.

**No confidence scoring on agent responses.** The agent produces text and the system trusts it. There is no mechanism to evaluate whether the response was coherent, completed the task, or should trigger a self-check prompt.

---

### 6. Integrations

**Status: Moderate**

**CLI Link (pi / terminal):**

The `pi_rpc.py` integration and `PiAgentTool` delegate coding tasks to the pi CLI agent. `_run_send_to_project_cli` (`step_executor.py:139-206`) sends instructions to a terminal session keyed by `project_id`, using `get_or_create_session()` with `asyncio.run()` — which will raise a `RuntimeError` if called from within an already-running event loop (e.g., inside an async workflow). The fallback "Bypassed" path (`step_executor.py:183-188`) silently marks the step as passed with `skip_wait=True`, hiding the failure from the workflow history.

**Cursor Connection:**

`CreateCursorTicketTool` (`create_cursor_ticket.py`) writes ticket markdown to `<project>/.tickets/` or `~/.cursor/decisionsai/tickets/`. The active project detection uses a runtime API call + internal token retrieval from the web server meta tag (`project_tools.py:26-35`). This token lookup hits `http://127.0.0.1:8765/settings` and scrapes an HTML meta tag — a fragile coupling between the tool runtime and the web server that would silently fail if the web server is not running, returning an empty token and falling back to "Missing internal API token" diagnostics without notifying the user.

**Subagent Context Inheritance:**

The `WorkflowAgent` receives no reference to the parent voice session. Its system prompt and initial messages are constructed independently from global settings (`workflow_agent.py:40-63`). Subagent tool calls within a workflow step cannot reference what the user was discussing before the workflow started. The only cross-agent data channel is the `context_prefix` in `_RunContext` (`dispatcher.py:44`) which holds a raw string injected at `start_workflow_run(context=...)` — but the structure and content of this string are caller-defined, with no schema.

**Telegram Integration:**

The Telegram manager has a 10-second deduplication window (`manager.py:164`) and inbound dedup cache (`messages.py:1`). The `speak_text_directly` path correctly clears `telegram_request` thread flags before pushing frames so desktop TTS fires even for Telegram-originated messages (`command_handler.py:729-736`). This is well-handled.

**WhatsApp Integration:**

Referenced in git status (`distr/core/integrations/whatsapp/manager.py` modified) but not audited in key files — integration status unclear from available context.

---

## Prioritized Improvement Areas

| Priority | Area | Issue | Suggested Fix |
|----------|------|-------|---------------|
| P0 | Self-Reflection | Zero self-correction machinery — no loop detection, no failure-aware retry, no corrective reasoning | Add `SelfReflectionMixin` that checks recent tool audit entries before re-issuing same call; add loop-break after N identical tool+params |
| P0 | Context System | `_messages` list is unbounded — long sessions silently consume context window leading to LLM errors or degraded coherence | Add sliding-window truncation: keep system prompt + last N turns + summarized older turns |
| P1 | TTS Quality | No SSML/prosody control — all pacing left to model defaults; workflow report `[Instruction:]` tag sometimes spoken verbatim | Add sentence-boundary pause injection; strip `[Instruction:...]` tags before TTS input |
| P1 | Tool Orchestration | `load_tools` dual code path: sidecar tools silently absent in cache-path; redundant re-instantiation when no user_message | Unify to always load from cache; add sidecar tools to the warm cache explicitly |
| P2 | Reasoning | No contradiction detection across prompts; context decay unmanaged for very long sessions | Add turn-count gate that triggers context summarisation; add contradiction check before executing destructive tool calls |
| P2 | Integrations | `_run_send_to_project_cli` calls `asyncio.run()` inside async context; silently bypasses on failure | Replace with `asyncio.get_event_loop().run_until_complete()` or proper async path; surface bypass as a warning to the voice agent |
| P3 | TTS Quality | Phoneme correction map (`paths.py:170-190`) remaps common words out-of-context (e.g., `screen`→`scream`) | Move phoneme corrections to a per-voice config; apply only when the TTS model is confirmed to mispronounce the word |
| P3 | Integrations | Cursor token retrieval scrapes HTML meta tag from live web server — fragile in headless or web-off scenarios | Cache token at startup; expose via a proper internal API endpoint rather than HTML meta tag scrape |

---

## Pseudo-Tickets for Core Improvement

### Ticket 1: Add Self-Reflection Loop-Break and Tool Retry Guard

**Area:** Self-Reflection Layer

**Problem:** When the agent issues a tool call that fails or produces an empty result, it has no mechanism to recognize the failure, adjust its strategy, and try a different approach. The same tool with the same parameters can be called repeatedly in the same session with no circuit breaker.

**Suggested Implementation:**
- Create `distr/core/agent/services/llm/reflection.py` with a `SelfReflectionMixin`
- Track last N (default 5) tool call results per session in a ring buffer on the LLM service
- Before issuing a tool call, check if the same `(tool_name, args_hash)` was attempted in the last 3 turns; if so, inject a reflection prompt: "Your previous attempt with {tool} returned: {result}. Try a different approach."
- Add a `max_identical_tool_calls: int = 3` guard that raises a soft-fail after N identical consecutive calls

**Files affected:**
- `distr/core/agent/services/llm/core_mixin.py` (add mixin, hook into tool-call dispatch)
- `distr/core/agent/services/llm/reflection.py` (new file)
- `distr/core/agent/services/llm/openai_compat.py` (hook into tool result handling)

---

### Ticket 2: Context Window Sliding-Window + Summarisation

**Area:** Context System / Reasoning Model Coherence

**Problem:** The `_messages` list in `core_mixin.py` grows unbounded. Long sessions silently degrade coherence as the LLM attends poorly to early messages, and may hit API token limits without graceful recovery.

**Suggested Implementation:**
- Add `_MAX_CONTEXT_TURNS: int = 40` setting (configurable from settings DB)
- When `len(self._messages) > _MAX_CONTEXT_TURNS + 1` (plus system), invoke a `_summarise_old_turns()` method that calls the LLM with `[Summarise these N exchanges in 2-3 sentences: ...]` and replaces the old turns with a single `{"role": "system", "content": "Earlier in this session: {summary}"}` message
- Expose `tool_context_window_turns` in settings DB (default 40)

**Files affected:**
- `distr/core/agent/services/llm/core_mixin.py`
- `distr/core/agent/services/llm/background_chain.py` (reuse for async summarisation)
- `distr/core/settings.py` (add default key)

---

### Ticket 3: SSML/Prosody Layer for Natural TTS Rhythm

**Area:** TTS Output Quality

**Problem:** All TTS providers receive raw text with no prosody hints. Tool completion announcements, list items, and step transitions are spoken at the same pace and emphasis level as conversational replies. Robotic delivery reduces perceived intelligence.

**Suggested Implementation:**
- Add `inject_prosody_hints(text: str, context: str) -> str` in `text_utils.py` that inserts provider-neutral pause markers (e.g., `...` or `\n\n`) at:
  - End of list items (when `\n-` pattern detected)
  - After tool completion phrases ("Done.", "I've finished...")
  - Before and after step numbers in workflow narration
- For ElevenLabs specifically: set `stability=0.65` for workflow summaries (measured, authoritative) vs `stability=0.45` for conversational replies (expressive). Currently a single static value is used.
- Strip `[Instruction: ...]` tags from workflow reports before they reach the LLM's TTS output path

**Files affected:**
- `distr/core/agent/services/llm/text_utils.py`
- `distr/core/workflow_engine/agent_bridge.py` (strip `[Instruction:...]` from TTS-bound text)
- `distr/core/audio/tts_handler.py` (accept optional `context_type` param)

---

### Ticket 4: Unify `load_tools` Cache Paths — Eliminate Silent Sidecar Gap

**Area:** Tool Orchestration

**Problem:** `load_tools` in `loader.py:540` exits early on the cache path without loading sidecar/accessibility tools. Any session with `user_message` set (the normal runtime path) silently lacks sidecar tools unless they were added to `warm_tool_cache`. If they were added to `warm_tool_cache`, they will be present; currently `warm_tool_cache` does add them (`loader.py:237-270`), but the `load_tools` non-cache path adds them *again* via separate loops. The two paths are diverged and can yield different tool sets.

**Suggested Implementation:**
- Audit `warm_tool_cache` to confirm sidecar tools are in the cache
- Change `load_tools` cache path to always return `list(_tool_cache.values())` — no early exit that might omit tools
- Add a startup assertion that logs which tools are in cache vs expected registry

**Files affected:**
- `distr/core/agent/tools/loader.py` (lines 539-555, 604-636)

---

### Ticket 5: Subagent Context Inheritance Protocol

**Area:** Integrations / Context System

**Problem:** `WorkflowAgent` starts with a blank `_messages` and has no access to the parent voice session's conversation context. Steps that need to act on "what the user just told me" receive only the structured step instruction, losing conversational intent. The `context_prefix` in `_RunContext` is an unstructured string with no schema.

**Suggested Implementation:**
- Define a `WorkflowRunContext` dataclass with:
  ```
  last_N_turns: List[Dict]   # Last 5 messages from parent session
  active_project_id: Optional[int]
  user_intent_summary: str   # 1-sentence LLM-generated summary of the triggering request
  ```
- Pass this as a typed argument to `start_workflow_run()` replacing the free-form `context: Optional[str]`
- In `_build_agent_prompt` (`step_executor.py:408`), inject `last_N_turns` as a prefixed context block

**Files affected:**
- `distr/core/workflow/dispatcher.py`
- `distr/core/workflow/step_executor.py`
- `distr/core/workflow_agent.py`

---

### Ticket 6: `_run_send_to_project_cli` Async Safety Fix

**Area:** Integrations

**Problem:** `_run_send_to_project_cli` calls `asyncio.run()` (`step_executor.py:193`) to get or create a terminal session. `asyncio.run()` raises `RuntimeError: This event loop is already running` when called from within an async context (e.g., a workflow step dispatched from the FastAPI/uvicorn server). The silent bypass path (`step_executor.py:183-188`) marks the step as passed with `skip_wait=True`, hiding the failure entirely.

**Suggested Implementation:**
- Replace `asyncio.run(...)` with `nest_asyncio` or schedule via `asyncio.get_event_loop().run_until_complete()` with a guard
- If the bypass path fires (no project context available), emit a warning to the voice agent: `"Send to project CLI step skipped: no linked project."` rather than silently passing
- Add a unit test that calls `_run_send_to_project_cli` from within an active event loop

**Files affected:**
- `distr/core/workflow/step_executor.py` (lines 139-206)

---

### Ticket 7: Phoneme Correction Map — Context-Aware Gating

**Area:** TTS Output Quality

**Problem:** The word correction map in `distr/core/paths.py:170-190` maps words like `screen` → `scream`, `zoom` → `boom`, `pause` → `paws`. These substitutions are intended for Kokoro mispronunciation but are applied globally and out of context. The word `pause` in "the workflow will pause here" becomes "the workflow will paws here" in spoken output. Additionally, the file defines the map but there is no clear runtime call site that applies it during live TTS — the map may be unused or applied only in certain code paths.

**Suggested Implementation:**
- Confirm which code path imports and applies this map; add a comment referencing the call site
- Move to a per-voice, opt-in config rather than a global map
- For context-aware filtering: only apply the substitution when the token appears as a standalone command (i.e., is preceded by a verb trigger or is the entire transcription), not when it appears in a multi-word sentence

**Files affected:**
- `distr/core/paths.py` (lines 160-190)
- Whichever module applies the map at TTS time (requires confirming the call site)

---

### Ticket 8: Initiative Service Proposal History Deduplication

**Area:** Reasoning Model Coherence / Initiative

**Problem:** The `InitiativeService` (`initiative/service.py`) runs a 5-minute idle timer and a 60-second schedule timer. On each cycle it proposes an action. The `policy.py` gate checks `duplicate_recent` but this flag must be set by the caller — the service itself does not maintain a history of what it proposed in previous cycles. If the LLM consistently proposes the same action (e.g., "remind the user to check their Jira board"), it will be proposed every 60 seconds indefinitely.

**Suggested Implementation:**
- Add `_recent_proposals: deque[tuple[str, float]]` (action_type + hash, timestamp) to `InitiativeService`
- Before dispatching a proposed action, check if a proposal with the same `(action_type, payload_hash)` was made within the last `cooldown_seconds` (default 300)
- Set `policy_context["duplicate_recent"] = True` automatically before calling `evaluate()` when a duplicate is detected
- Persist proposal log to DB so it survives app restarts

**Files affected:**
- `distr/core/initiative/service.py`
- `distr/core/initiative/policy.py`

---

## Meta-Learning Strategy Recommendation

The current audit log (`tool_audit.py`) is a valuable foundation but is entirely write-only from the agent's perspective. A meta-learning loop would require three components that are currently absent:

**1. Feedback ingestion.** The `append_audit_step` function (`workflow/audit.py:49`) records tool name, instruction, result, and status per chat. This data should be periodically summarised (per-chat, per-tool-class) and stored as a `meta_context` field on the chat or session. This summary — "In this session, `file_operations` failed twice with 'permission denied'" — should be prepended to the system prompt on session reload.

**2. Tool-level outcome tracking.** Extend `tool_telemetry.py` (`tool_telemetry.py:17-45`) to log outcome quality, not just invocation metadata. Add a `outcome: "success" | "partial" | "failure" | "no_action"` field to `log_request_tool_event`. Over time, this creates a per-model-per-tool success rate database that can inform future retrieval K-values (e.g., always include `web_search` for this user's query patterns).

**3. Corrective prompt injection.** When a session starts (or after a failed workflow step), query the last 10 audit entries for the current chat and inject a 2-line summary into the system prompt: "Recent context: you last ran {tool_name} which {succeeded/failed}. Be aware that {condition}." This transforms the static system prompt into a session-aware instruction.

A minimal implementation could be delivered in two phases: Phase 1 adds `outcome` to `tool_telemetry` and builds the per-chat `meta_context` summary (no model changes, pure logging). Phase 2 adds the corrective prompt injection hook in `_build_system_message` (`core_mixin.py`) gated by a settings flag `enable_meta_learning: bool = False`.

---

## Conclusion

DecisionsAI's agent runtime is well-structured: the Pipecat pipeline, hot-swap services, semantic tool retrieval, and initiative policy gate represent genuine architectural maturity. The most critical gap is the complete absence of any self-reflection or corrective reasoning loop — the agent has no ability to learn from within-session failures, detect repetition, or modulate its approach based on what just failed. This is the single highest-leverage improvement available.

The TTS layer is functional but lacks prosody control and has a context-agnostic phoneme correction table that can actively degrade output quality in normal sentences. The context system is solid for workflow execution but unbounded for the voice agent, creating a long-session coherence risk. Subagent context inheritance is implicit and structurally incomplete, limiting the depth of reasoning available to workflow step agents.

The eight tickets above address the top improvements in priority order. Tickets 1 (self-reflection loop), 2 (context window management), and 3 (TTS prosody) would deliver the most noticeable improvement in perceived agent intelligence and voice quality.
