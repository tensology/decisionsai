"""Phase 3 production-qualification checks for ticket-type workflow behavior.

These are deterministic contract/coordination checks that fail if the weekend
workflow hardening regresses. Live Kayla supervision remains a human/runtime
exercise; this file pins the systemic invariants.
"""

from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

from distr.core.workflow.coordination_plan import (
    build_run_coordination_plan,
    consume_step_replan,
    apply_steering_to_plan,
)
from distr.core.workflow.control_policy import decide_interruption
from distr.core.workflow.ticket_contract import (
    classify_ticket_execution,
    existing_work_satisfies_contract,
)


def _workflow():
    steps = [
        SimpleNamespace(
            id=1,
            position=0,
            name="Ingest and verify",
            description="Understand the ticket.",
            config=json.dumps({"step_role": "planning"}),
            validation_type="rule_based",
        ),
        SimpleNamespace(
            id=2,
            position=1,
            name="Implement",
            description="Make code changes.",
            config=json.dumps({"step_role": "implementation"}),
            validation_type="rule_based",
        ),
        SimpleNamespace(
            id=3,
            position=2,
            name="Independent review",
            description="Review the work.",
            config=json.dumps({"step_role": "review"}),
            validation_type="llm_judgment",
        ),
        SimpleNamespace(
            id=4,
            position=3,
            name="Compact report",
            description="Report and memory handoff.",
            config=json.dumps({"step_role": "reporting"}),
            validation_type="none",
        ),
    ]
    return SimpleNamespace(
        id=9,
        name="Development",
        steps=steps,
        run_settings=json.dumps({
            "auto_route_models": True,
            "adaptive_multi_model_enabled": True,
        }),
    )


def test_research_ticket_short_circuits_without_implementation_gates():
    ticket = (
        "Research the supplied sources and produce a cited markdown brief. "
        "No code changes. Existing notes already cover the acceptance criteria."
    )
    profile = classify_ticket_execution(ticket)
    assert profile.get("research_only") or profile.get("explicit_no_code")
    assert existing_work_satisfies_contract(
        ticket,
        (
            "Status: completed\n"
            "Blockers: none\n"
            "Summary: Research brief written with citations.\n"
            "Evidence: docs/research-brief.md\n"
            "Acceptance criteria verified against source inventory."
        ),
    )


def test_normal_implementation_gets_adaptive_review_allocation(monkeypatch):
    def _fake_role_policy(route, *, step_role="execution", **_kwargs):
        workers = {
            "planning": ("codex", "auto", "openai"),
            "implementation": ("pi", "ornith:35b", "ollama"),
            "review": ("pi", "tencent/hy3-preview", "openrouter"),
            "reporting": ("pi", "ornith:9b", "ollama"),
        }
        backend, model, provider = workers.get(step_role, ("pi", "auto", "ollama"))
        return {
            **route,
            "backend": backend,
            "model": model,
            "model_provider": provider,
            "source": "auto_step_role",
        }

    monkeypatch.setattr(
        "distr.core.project_cli_backends.model_policy.apply_auto_step_role_policy",
        _fake_role_policy,
    )
    monkeypatch.setattr(
        "distr.core.workflow.coordination_plan._free_validator_candidates",
        lambda _settings: [{
            "backend": "pi",
            "model_provider": "ollama",
            "model": "ornith:9b",
            "source": "coordination_independent_evaluator",
        }],
    )
    plan = build_run_coordination_plan(
        _workflow(),
        {
            "execution_route": {"backend": "pi", "model": "auto", "complexity": "medium"},
            "risk_profile": {"level": "medium"},
        },
        settings={},
    )
    review = plan["assignments"]["3"]
    assert review["role"] == "review"
    assert review["review_mode"] in {"independent", "dual"}
    assert review["evaluation_routes"]


def test_high_risk_ticket_prefers_stronger_review_and_interrupt_policy(monkeypatch):
    def _fake_role_policy(route, *, step_role="execution", **_kwargs):
        return {
            **route,
            "backend": "codex" if step_role in {"planning", "review"} else "pi",
            "model": "auto",
            "model_provider": "openai" if step_role in {"planning", "review"} else "ollama",
            "source": "auto_step_role",
        }

    monkeypatch.setattr(
        "distr.core.project_cli_backends.model_policy.apply_auto_step_role_policy",
        _fake_role_policy,
    )
    monkeypatch.setattr(
        "distr.core.workflow.coordination_plan._free_validator_candidates",
        lambda _settings: [{
            "backend": "pi",
            "model_provider": "openrouter",
            "model": "deepseek/deepseek-r1:free",
        }],
    )
    plan = build_run_coordination_plan(
        _workflow(),
        {
            "execution_route": {"backend": "pi", "model": "auto", "complexity": "high"},
            "risk_profile": {"level": "high"},
            "ticket_title": "Add OAuth login and payment capture",
        },
        settings={},
    )
    assert plan["risk_level"] == "high"
    assert plan["assignments"]["3"]["review_mode"] in {"independent", "dual"}
    assert decide_interruption(paid_escalation=True).should_interrupt
    assert decide_interruption(irreversible=True).should_interrupt


def test_steer_then_dispatch_replan_leaves_reusable_workflow_untouched(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.model_policy.apply_auto_step_role_policy",
        lambda route, **kwargs: {**route, "backend": "cursor", "model": "auto", "source": "auto"},
    )
    workflow = _workflow()
    original = deepcopy([step.__dict__ for step in workflow.steps])
    plan = {
        "assignments": {
            "2": {
                "step_id": 2,
                "position": 1,
                "role": "implementation",
                "primary_route": {"backend": "pi", "model": "auto"},
                "needs_replan": False,
            },
            "3": {
                "step_id": 3,
                "position": 2,
                "role": "review",
                "primary_route": {"backend": "pi", "model": "auto"},
                "needs_replan": False,
            },
        },
        "revisions": [],
        "immutable_workflow": True,
    }
    steered, _ = apply_steering_to_plan(
        plan,
        current_step_id=2,
        message="Use Cursor for the remaining work",
        impact="route",
        route_preference="cursor",
    )
    revised, revision = consume_step_replan(steered, step_id=3, workflow=workflow, settings={})
    assert revision is not None
    assert revised["assignments"]["3"]["needs_replan"] is False
    assert [step.__dict__ for step in workflow.steps] == original
