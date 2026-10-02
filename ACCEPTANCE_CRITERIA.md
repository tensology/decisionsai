# DecisionsAI — Acceptance Criteria Checklist

**Scope:** local DecisionsAI only. No prod / deploy / spend.  
**Repo:** `/Users/paul/development/TENSOLOGY/DECISIONS/DecisionsAI`  
**Updated:** 2026-10-02 ~18:15 SAST (Africa/Johannesburg)
**Status key:** **met** = code + tests (or live proof) satisfy the checks below; **in-flight** = partial / still being edited; **future** = not built yet.

> `docs/*` is gitignored. Canonical tracked copy: repo-root `ACCEPTANCE_CRITERIA.md`. Mirror: `docs/ACCEPTANCE_CRITERIA.md`.

---

## Summary

| # | Item | Status |
|---|------|--------|
| 1 | Dynamic Ollama harness picker | **met** |
| 2 | `human_checkpoints` default on (+ no strip; ad-hoc too) | **met** |
| 3 | WorkIntake lifecycle row for WA | **met** |
| 4 | WhatsApp customer send dry-run + live only on yes | **met** |
| 5 | Approval loop E2E | **in-flight** |
| 6 | MemPalace (dual-write, chromadb, markdown test, flag off, both surfaces) | **met** |
| 7 | Cost ledger | **in-flight** |

---

## 1. Dynamic Ollama harness picker — **met**

**Intent:** Development harness worker picker lists live installed Ollama chat tags (`ollama list` / `/api/tags`), not a hard-coded Qwen/Glimmer catalog. No pulls. Fail closed when Ollama unreachable. Legacy aliases remain labels only.

**Code:** `distr/core/workflow/development_harness.py` (`available_local_harness_workers`, `_ollama_reachable_installed_ids`, `LOCAL_OLLAMA_LABEL_HINTS` / `LEGACY_LOCAL_WORKER_ALIASES`).  
**Tests:** `tests/core/test_local_harness_workers.py`.

### Pass / fail

- [x] Picker source of truth is installed tags, not `LOCAL_HARNESS_PRESETS` as the catalog.
- [x] Worker ids use `local_ollama:<tag>`; legacy `qwen38_27b` / `glimmer` still resolve.
- [x] Embedding / `:cloud` tags are excluded from local workers.
- [x] Unreachable Ollama → empty local-worker list (fail closed), not a fake static list.
- [x] Selecting a local worker resolves to Pi + Ollama route fields (`resolve_local_harness_selection`).
- [x] No `ollama pull` in picker / resolve path; unload uses `keep_alive=0` only after run.
- [x] Unit tests cover dynamic list, fail-closed, legacy alias, and no hard-coded picker source in composer.

---

## 2. `human_checkpoints` default on (+ no strip; ad-hoc too) — **met**

**Intent:** Pre-work / step human gates are **ON by default** for Development and ad-hoc WorkIntake. Do not strip the setting from run settings. Explicit `human_checkpoints: false` or `skip_human_checkpoints` still disables.

**Code:**
- `DEFAULT_RUN_SETTINGS["human_checkpoints"] = True` — `distr/core/workflow/dispatcher.py`
- `human_checkpoint_enabled` defaults True when key missing — `distr/core/workflow/run_briefing.py`
- Ad-hoc lightweight WorkIntake prefers linked workflow (so Telegram pre-work approve fires) unless skip — `distr/core/work_intake/service.py`
- **Defaults flipped (2026-10-02):** `spawn_workflow_for_ticket(..., skip_human_checkpoints=False)` — `distr/core/workflow/spawn_workflow.py`; kanban API `SpawnWorkflowForTicketRequest.skip_human_checkpoints: bool = False` — `distr/gui/web/routes/kanban.py`; agent tool call `skip_human_checkpoints=False` — `distr/core/agent/tools/step_runner/workflow_tools.py`

**Tests:** `tests/core/test_workflow_run_briefing.py::test_human_checkpoints_default_on_unless_explicitly_disabled`; `tests/core/test_workflow_run_settings_human_checkpoints.py` (preserves default + spawn/kanban/tool skip defaults False).

### Pass / fail

- [x] Missing `run_settings.human_checkpoints` → checkpoints enabled.
- [x] Explicit `False` or `skip_human_checkpoints` → disabled.
- [x] `DEFAULT_RUN_SETTINGS` includes `human_checkpoints: True` (not stripped on normalize).
- [x] Ad-hoc / work-intent ticket with linked workflow uses workflow path for approval (not silent lightweight CLI) unless metadata skip.
- [x] `spawn_workflow_for_ticket` / kanban spawn payload default `skip_human_checkpoints=False` (callers must opt in to skip).
- [x] Agent workflow tool calls spawn with `skip_human_checkpoints=False`.

## 3. WorkIntake lifecycle row for WA — **met**

**Intent:** WhatsApp-sourced WorkIntake ticket creation persists a `whatsapp_work_lifecycles` row so reply-review / send gating can attach later.

**Code:** `OrchestratorIntakeService._record_whatsapp_lifecycle` → `record_ticket_created` in `distr/core/kanban/whatsapp_work_lifecycle.py` (called from `_create_ticket` when `intake.source == whatsapp`). Also wired from kanban / toolkit snapshot paths.

**Tests:** `tests/core/test_whatsapp_work_lifecycle.py` (durable row, review, send claim). WorkIntake create path covered via service change + existing lifecycle tests.

### Pass / fail

- [x] Table `whatsapp_work_lifecycles` created idempotently (`ensure_tables`).
- [x] WA WorkIntake ticket create inserts/upserts a lifecycle row keyed by `ticket_id`.
- [x] Row captures `source_jid` / phone / contact / `message_ids` from intake metadata when present.
- [x] Duplicate ticket create does not explode (unique `ticket_id`; lifecycle helpers re-read existing).
- [x] Lifecycle status advances through execution / reply-draft / reply-sent paths used by dispatcher + Telegram review.

---

## 4. WhatsApp customer send dry-run + live only on yes — **met**

**Intent:** Outbound customer WhatsApp never fires by accident. Dry-run available for local claim/review. Live relay POST only after explicit human yes (Telegram review **Send** or toolkit approval phrase + token).

**Code:**
- Dry-run: `whatsapp_send_dry_run_enabled()` — env `DECISIONSAI_WHATSAPP_DRY_RUN=1` preferred; settings `whatsapp_send_dry_run` (default **False**) — `distr/core/integrations/whatsapp/relay_client.py`
- Customer path: Telegram review markup `wa:<token>:send|revise|leave` → `send_message_via_relay` only on **send** — `whatsapp_work_lifecycle.handle_telegram_reply`
- Agent toolkit: draft → approval token; send requires `approved` + phrase in `{approved, send, send it, go ahead, yes send}` — `whatsapp_toolkit.py`

### Pass / fail

- [x] With dry-run ON, `send_message_via_relay` returns `{success, dry_run: true}` and does **not** POST to relay `/send`.
- [x] With dry-run OFF, relay POST is the only live send path used by lifecycle + toolkit.
- [x] Telegram QA prompt asks “Send to customer?”; **Send** claims review once; failed send returns review to pending.
- [x] **Revise** / **leave draft** do not call live send.
- [x] Toolkit cannot send from draft alone (token + explicit approval phrase required).
- [x] Default settings leave `whatsapp_send_dry_run: False` (operators opt into dry-run via env for local).

---

## 5. Approval loop E2E — **in-flight**

**Intent:** End-to-end: work waits on a plain-English approval card (run briefing / step review), human says yes/no/steer (chat or Telegram), run resumes or stops once; loop diagnostics kick in if the same gate repeats without progress.

**Code (present):** `distr/core/workflow/approval_decision.py` (cards + `approval_loop_diagnostics`), `run_briefing.py`, dispatcher waiting kinds, Telegram / chat classifiers, route-approval helpers. WA lifecycle + dry-run path simulated in unit tests.

**Tests (partial):** `test_workflow_run_briefing.py`, `test_requested_pre_execution_approval.py`, `test_run_route_approval.py`, `test_approval_loop_e2e_simulation.py` (A→E simulated: lifecycle → checkpoints ON → completion review → Telegram send/revise/leave with `DECISIONSAI_WHATSAPP_DRY_RUN=1` / dry-run send — **no live Paul Telegram press**).

### Pass / fail

- [x] Approval cards are plain-English (`ApprovalDecision` / `format_approval_decision_text`).
- [x] Checkpoint loop counter + diagnostics after repeated same gate (`approval_loop_diagnostics`, threshold 3).
- [x] Requested pre-execution / route-approval unit paths exist.
- [x] Simulated WA approval-loop handlers (dry-run send, revise, leave) covered without live channel presses.
- [ ] **Gap:** Full **live** E2E (WA WorkIntake → TG pre-work approve → harness step → TG completion “Send to customer?” → WA dry-run send) not proven end-to-end on a running Decisions app. Blocked on Paul pressing Telegram buttons while app is up; use `DECISIONSAI_WHATSAPP_DRY_RUN=1` so customer send stays dry-run.
- [ ] **Gap:** Voice/TTS soft-reply for work-intake approval confirmation still noted as caveat in `.decisions-audit-flags.md`.

## 6. MemPalace — **met** (flag OFF by design)

**Intent:** Optional MemPalace backend behind `mempalace_memory_backend` (default **OFF**). When ON: dual-write + prefer-read; chromadb installed locally; markdown memory live-tested; both chat + Development surfaces; no delete of legacy stores.

**Code:** `distr/core/mempalace/` (adapter, wiring, flags, paths, bootstrap). Hooks in `memory/files.py`, `core_mixin` RAG, orchestrator / learnings / steering / workspace.  
**Notes:** `.decisions-mempalace-flags.md`, `docs/mempalace-setup-map.md`, `distr/core/mempalace/SETUP.md`.  
**Tests:** `tests/memory/test_mempalace_flag.py`. Live markdown proof 2026-10-02 (~17:38 SAST) with isolated palace; flag left OFF after.

### Pass / fail

- [x] Flag default **OFF** (`DEFAULT_SETTINGS` + unset env → legacy only).
- [x] Env `DECISIONS_MEMPALACE_MEMORY_BACKEND=1` enables without flipping durable default.
- [x] Palace path Decisions-owned: `~/.decisions/mempalace/palace` (override `DECISIONS_MEMPALACE_PALACE_PATH`).
- [x] Dual-write first (legacy write always + MemPalace drawer); no cutover/delete of old stores.
- [x] chromadb available in local decisions venv (mempalace 3.3.3 + chromadb 1.5.9; free ONNX embed cache).
- [x] Markdown dual-write / prefer-read / search live-tested; wiring no-ops when flag OFF.
- [x] Both surfaces: chat primary semantic consumer; Development/harness dual-write + prefer-read (FS companions stay pickup SoT).
- [x] Soft-fail when mempalace/chromadb missing (adapter status, no crash).
- [ ] **Out of scope / future:** one-shot migrate, Settings UI toggle, markdown-only scoped flag, LlamaIndex replace vs keep.

---

## 7. Cost ledger — **in-flight**

**Intent:** Durable per-run / per-provider cost + token ledger with queryable totals, budget breakers already hinted in blueprint adherence, and an operator-visible surface — without requiring paid APIs for local-only runs.

**Today (2026-10-02):** Module `distr/core/cost_ledger/` ships schema + `record_usage` / `link_deliverable` / rollups; hooked from `StepExecutor._consume_power_budget_after_step`; Reports **Costs** tab + `GET /workflows/studio/reports/costs`. Setting `cost_ledger_enabled` defaults **True** (env `DECISIONS_COST_LEDGER_ENABLED`); display `cost_invoice_display` = `blended`|`explicit` (default blended). Local/ollama provider USD = 0 with resource estimate. Open calls in `distr/core/cost_ledger/NEEDS_PAUL.md`. Budget breaker still uses in-memory `blueprint_adherence` heuristic (ledger is separate durable estimate).

### Pass / fail

- [x] Schema (or file ledger) records: run_id, ticket_id, provider, model, input/output tokens, estimated USD, timestamp, source (chat / harness / workflow).
- [x] Dual-write or hook from existing step/result packets without breaking offline Ollama (cost may be `0` / `n/a` for local).
- [x] Query API or CLI: totals by day / project / run; export CSV or JSON. *(JSON API + rollups; CSV export still open)*
- [ ] Budget breaker can read ledger totals (not only in-memory session counters).
- [x] Feature flag or setting default **True** for local recording; recording no-op-safe when disabled. *(ORM column persistence still open — see NEEDS_PAUL)*
- [x] Unit tests for record + aggregate; no network spend in CI.
- [x] Operator doc: how to read the ledger and what “estimated” means per provider. *(`pricing.py` + NEEDS_PAUL + `.decisions-cost-ledger-status.md`)*
- [x] Reports UI Costs tab with blended (default) / explicit display toggle.

---

## Ambiguities / open calls

1. **`skip_human_checkpoints` defaults** — **Resolved (2026-10-02):** spawn signature, kanban `SpawnWorkflowForTicketRequest`, and agent workflow tool all default/call `False`. Explicit `True` remains the escape hatch.
2. **“No strip”** — Interpreted as: do not omit/clear `human_checkpoints` from `run_settings`, and do not auto-inject `skip_human_checkpoints` on ad-hoc paths. If “strip” meant something else (UI checkbox, metadata sanitizer), call it out.
3. **WhatsApp dry-run default** — Code default is **False** (live relay when connected); local safety relies on env `DECISIONSAI_WHATSAPP_DRY_RUN=1`. Confirm whether durable default should be True for non-prod profiles.
4. **Approval loop E2E owner** — §2 spawn/tool skip defaults flipped. §5 remains **in-flight**: unit simulation green; full live WA→TG→harness→WA dry-run still blocked on Paul pressing Telegram with Decisions running and `DECISIONSAI_WHATSAPP_DRY_RUN=1`.
5. **MemPalace “both surfaces”** — Recommended and partially wired; Development does not yet RAG-inject markdown/palace the way chat does. Confirm whether “both” means dual-write only on Dev, or also prefer-read inject on harness prompts.
6. **Cost ledger currency / pricing table** — **In-flight:** static table in `distr/core/cost_ledger/pricing.py`; local/ollama $0 provider + resource estimate. See NEEDS_PAUL for API vs table / Client ORM.
7. **Uncommitted local tree** — Several §1–§6 items live in a dirty working tree (`mempalace/`, harness workers, WorkIntake WA row, checkpoint default). Acceptance **met** here means “current local code satisfies checks,” not “merged to main.”

---

## How to re-verify (local, no spend)

```bash
cd /Users/paul/development/TENSOLOGY/DECISIONS/DecisionsAI
# Harness picker
pytest -q tests/core/test_local_harness_workers.py
# Checkpoints default + spawn/kanban skip defaults
pytest -q tests/core/test_workflow_run_briefing.py -k human_checkpoint tests/core/test_workflow_run_settings_human_checkpoints.py
# WA lifecycle
pytest -q tests/core/test_whatsapp_work_lifecycle.py
# MemPalace flag / wiring
pytest -q tests/memory/test_mempalace_flag.py
# Optional: WA dry-run (no relay POST)
# DECISIONSAI_WHATSAPP_DRY_RUN=1  → send_message_via_relay returns dry_run
```

Do **not** run `ollama pull`, paid provider calls, or MemPalace mine against production data under the no-spend rule.
