# Local Harness Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one local command that verifies DecisionsAI harness health, setup projection, and focused harness tests.

**Architecture:** Keep existing doctor/setup/smoke scripts as the source of truth. Add a thin orchestration script that runs them with clear exit codes and optional smoke-fixture lifecycle checks. Add tests around command composition and failure behavior.

**Tech Stack:** Python standard library, existing `scripts/*.py`, pytest.

---

### Task 1: Add Local Preflight Script

**Files:**
- Create: `scripts/preflight_local.py`
- Test: `tests/core/test_local_harness_preflight.py`

- [ ] **Step 1: Add script with command runner abstraction**

Create `scripts/preflight_local.py` with `PreflightStep`, `run_preflight`, and `main`. The default run executes harness doctor, setup verification, and focused harness tests. `--smoke-fixture` adds setup and cleanup steps. `--strict-doctor` makes doctor findings fail the whole preflight.

- [ ] **Step 2: Add tests for default and smoke command composition**

Test that the default plan includes doctor, setup verification, and pytest, and that `--smoke-fixture` adds setup/cleanup with cleanup always attempted after setup.

- [ ] **Step 3: Add tests for failure exit behavior**

Test that a required step failure returns non-zero and records the failed step, while non-strict doctor findings do not fail the command.

### Task 2: Document the Command

**Files:**
- Create: `docs/local-harness-preflight.md`

- [ ] **Step 1: Document daily and deeper checks**

Document `rtk python3 scripts/preflight_local.py`, `--strict-doctor`, `--smoke-fixture`, and how to read failures.

### Task 3: Verify

**Files:**
- Modify only if tests expose issues.

- [ ] **Step 1: Run targeted tests**

Run: `rtk python3 -m pytest -q tests/core/test_local_harness_preflight.py --tb=short`

- [ ] **Step 2: Run the preflight command**

Run: `rtk python3 scripts/preflight_local.py`

- [ ] **Step 3: Commit**

Commit the script, docs, and tests once verification passes or report the exact failing harness requirement.
