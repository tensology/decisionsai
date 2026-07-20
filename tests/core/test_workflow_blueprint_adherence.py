"""Runnable checks for blueprint adherence (budgets, tool bay, evals, version pin)."""

from __future__ import annotations

from distr.core.workflow.blueprint_adherence import (
    BLUEPRINT_CHECKLIST,
    build_run_blueprint_snapshot,
    build_run_version_pin,
    checklist_snapshot,
    compact_worker_context,
    consume_run_power_budget,
    default_run_power_budget,
    ensure_run_blueprint_defaults,
    format_tool_failure,
    record_memory_compaction_note,
    render_tool_bay_docs,
    update_drift_metrics,
)
from distr.core.workflow.blueprint_eval_pack import BLUEPRINT_EVAL_PACK, run_blueprint_eval_pack


def test_checklist_has_no_unexplained_fails():
    snap = checklist_snapshot()
    assert snap["counts"]["fail"] == 1
    fails = [item for item in BLUEPRINT_CHECKLIST if item["status"] == "fail"]
    assert len(fails) == 1
    assert fails[0]["id"] == "P2"
    assert fails[0].get("ceiling")


def test_power_budget_exhaustion_stop_and_ask():
    run_data = ensure_run_blueprint_defaults({}, complexity="low")
    budget = default_run_power_budget(complexity="low")
    run_data["power_budget"] = {**budget, "turns_used": budget["max_turns"] - 1}
    run_data, interrupt = consume_run_power_budget(run_data, turns=1, tokens=1200, model_provider="openai")
    assert interrupt is not None
    assert interrupt["should_interrupt"] is True
    assert "budget" in interrupt["question"].lower() or "turns" in interrupt["question"].lower()
    assert run_data["power_budget"]["exhausted"] is True
    assert run_data["power_budget"]["estimated_cost_usd"] > 0


def test_tool_bay_docs_and_failure_recovery():
    docs = render_tool_bay_docs(tool_ids=["cli", "shell"])
    assert "Tool bay v" in docs
    assert "cli" in docs
    assert "shell" in docs
    assert "playwright" not in docs
    text = format_tool_failure(tool_id="cli", error="pytest failed", suggestion="Re-run the named test once.")
    assert "Tool 'cli' failed" in text
    assert "Try next:" in text
    assert "Re-run the named test once" in text


def test_memory_pump_compacts_and_records_note():
    prior = "x" * 20_000
    pumped = compact_worker_context(
        role="implementation",
        objective="Ship the login fix",
        prior_text=prior,
        references=["artifacts/login.png"],
        max_chars=4_000,
    )
    assert pumped["compacted"] is True
    assert len(pumped["restart_context"]) <= 4_000
    assert "Pointers:" in pumped["restart_context"]
    run_data = record_memory_compaction_note({}, pumped["note"])
    assert len(run_data["memory_compaction_notes"]) == 1


def test_version_pin_and_blueprint_snapshot():
    pin = build_run_version_pin(
        workflow_id=7,
        workflow_name="Development",
        workflow_revision="rev-1",
        coordination_plan={
            "strategy": "orchestrator_workers",
            "assignments": {
                "1": {
                    "step_id": 1,
                    "role": "implementation",
                    "review_mode": "independent",
                    "primary_route": {"backend": "codex", "model": "gpt-5", "model_provider": "openai"},
                }
            },
        },
        tool_ids=["cli"],
        prompt_fingerprint="abc123",
    )
    assert pin["manifest_hash"]
    assert pin["tool_bay_version"]
    assert pin["routes"][0]["backend"] == "codex"
    run_data = ensure_run_blueprint_defaults({"version_pin": pin, "coordination_plan": {"strategy": "single"}})
    run_data = update_drift_metrics(run_data, human_takeover=True, completed=True)
    snap = build_run_blueprint_snapshot(run_data)
    assert snap["version_pin"]["manifest_hash"] == pin["manifest_hash"]
    assert snap["drift"]["human_takeovers"] == 1
    assert snap["drift"]["task_success"] is True


def test_eval_pack_scores_twenty_outcome_cases():
    assert len(BLUEPRINT_EVAL_PACK) == 20
    report = run_blueprint_eval_pack()
    assert report["total"] == 20
    assert report["failed"] == 0
    assert report["ok"] == 20
    assert report["success_rate"] == 1.0
