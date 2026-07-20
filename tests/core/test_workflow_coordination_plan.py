from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

from distr.core.workflow.coordination_plan import (
    build_run_coordination_plan,
    consume_step_replan,
    coordination_plan_routes,
    render_coordination_map,
    revise_plan_after_step,
    apply_steering_to_plan,
)


def _workflow(*, adaptive: bool | None = True, name: str = "Development"):
    steps = [
        SimpleNamespace(
            id=11,
            position=0,
            name="Plan the implementation",
            description="Scope the ticket and acceptance criteria.",
            config=json.dumps({"step_role": "planning", "skills": ["planning"]}),
            validation_type="rule_based",
        ),
        SimpleNamespace(
            id=12,
            position=1,
            name="Implement the plan",
            description="Make the requested code changes.",
            config=json.dumps({"step_role": "implementation", "tools": ["files", "shell"]}),
            validation_type="rule_based",
        ),
        SimpleNamespace(
            id=13,
            position=2,
            name="Independent review",
            description="Review the implementation against the ticket.",
            config=json.dumps({"step_role": "review"}),
            validation_type="llm_judgment",
        ),
    ]
    run_settings = {
        "auto_route_models": True,
        "max_parallel_evaluators": 2,
    }
    if adaptive is not None:
        run_settings["adaptive_multi_model_enabled"] = adaptive
    return SimpleNamespace(
        id=7,
        name=name,
        steps=steps,
        run_settings=json.dumps(run_settings),
    )


def _fake_role_policy(route, *, step_role="execution", **_kwargs):
    workers = {
        "planning": ("codex", "auto"),
        "implementation": ("pi", "ornith:35b"),
        "review": ("pi", "tencent/hy3-preview"),
    }
    backend, model = workers.get(step_role, ("pi", "auto"))
    return {
        **route,
        "backend": backend,
        "model": model,
        "model_provider": "openrouter" if "hy3" in model else "ollama",
        "source": "auto_step_role",
    }


def _fake_free_validators(_settings):
    return [
        {
            "backend": "pi",
            "model_provider": "ollama",
            "model": "ornith:9b",
            "source": "coordination_independent_evaluator",
        },
        {
            "backend": "pi",
            "model_provider": "openrouter",
            "model": "deepseek/deepseek-r1:free",
            "source": "coordination_independent_evaluator",
        },
    ]


def test_coordination_plan_allocates_the_whole_workflow_without_mutating_it(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.model_policy.apply_auto_step_role_policy",
        _fake_role_policy,
    )
    monkeypatch.setattr(
        "distr.core.workflow.coordination_plan._free_validator_candidates",
        _fake_free_validators,
    )
    workflow = _workflow()
    original = deepcopy([step.__dict__ for step in workflow.steps])

    plan = build_run_coordination_plan(
        workflow,
        {
            "execution_route": {"complexity": "high"},
            "risk_profile": {"level": "high"},
        },
        settings={},
    )

    assert list(plan["assignments"]) == ["11", "12", "13"]
    assert plan["immutable_workflow"] is True
    assert plan["strategy"] == "adaptive_dual_review"
    assert plan["assignments"]["12"]["depends_on"] == [11]
    assert plan["assignments"]["12"]["primary_route"]["model"] == "ornith:35b"
    assert len(plan["assignments"]["13"]["evaluation_routes"]) == 2
    assert [step.__dict__ for step in workflow.steps] == original

    step_routes, role_routes = coordination_plan_routes(plan)
    assert step_routes["11"]["backend"] == "codex"
    assert role_routes["implementation"]["model"] == "ornith:35b"


def test_coordination_plan_keeps_legacy_workflows_deterministic(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.model_policy.apply_auto_step_role_policy",
        _fake_role_policy,
    )
    monkeypatch.setattr(
        "distr.core.workflow.coordination_plan._free_validator_candidates",
        _fake_free_validators,
    )
    plan = build_run_coordination_plan(_workflow(adaptive=False), {}, settings={})

    assert plan["strategy"] == "single"
    assert all(item["review_mode"] == "deterministic" for item in plan["assignments"].values())
    assert all(not item["evaluation_routes"] for item in plan["assignments"].values())


def test_existing_canonical_development_workflow_gets_adaptive_default(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.model_policy.apply_auto_step_role_policy",
        _fake_role_policy,
    )
    monkeypatch.setattr(
        "distr.core.workflow.coordination_plan._free_validator_candidates",
        _fake_free_validators,
    )

    development = build_run_coordination_plan(_workflow(adaptive=None), {}, settings={})
    custom = build_run_coordination_plan(
        _workflow(adaptive=None, name="Custom workflow"), {}, settings={}
    )

    assert development["adaptive_multi_model_enabled"] is True
    assert development["strategy"].startswith("adaptive_")
    assert custom["adaptive_multi_model_enabled"] is False
    assert custom["strategy"] == "single"


def test_coordination_map_marks_current_step_and_shows_every_assignment(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.model_policy.apply_auto_step_role_policy",
        _fake_role_policy,
    )
    monkeypatch.setattr(
        "distr.core.workflow.coordination_plan._free_validator_candidates",
        _fake_free_validators,
    )
    plan = build_run_coordination_plan(_workflow(), {}, settings={})

    rendered = render_coordination_map(plan, current_step_id=12)

    assert "· 1. Plan the implementation" in rendered
    assert "→ 2. Implement the plan" in rendered
    assert "· 3. Independent review" in rendered
    assert "ornith:35b" in rendered


def test_visual_evidence_ticket_routes_review_to_configured_vision_model(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.model_policy.apply_auto_step_role_policy",
        _fake_role_policy,
    )
    monkeypatch.setattr(
        "distr.core.workflow.coordination_plan._free_validator_candidates",
        _fake_free_validators,
    )

    plan = build_run_coordination_plan(
        _workflow(),
        {
            "ticket_title": "Research the supplied artist sources",
            "ticket_workflow_brief": (
                "Browser evidence required: screenshots of the supplied Spotify and YouTube pages."
            ),
        },
        settings={
            "vision_llm_provider": "openai",
            "vision_llm_model": "gpt-4o",
        },
    )

    review = plan["assignments"]["13"]
    assert review["primary_route"]["model_provider"] == "openai"
    assert review["primary_route"]["model"] == "gpt-4o"
    assert review["primary_route"]["source"] == "run_coordination_visual_evidence"
    assert review["primary_route"]["evidence_capabilities"] == ["vision"]
    assert review["required_evidence_capabilities"] == ["vision"]


def test_failed_step_replans_only_the_next_run_assignment():
    plan = {
        "assignments": {
            "11": {"status": "planned", "primary_route": {"backend": "pi", "model": "ornith:35b"}},
            "12": {"status": "planned", "revision": 0, "primary_route": {"backend": "pi", "model": "ornith:35b"}},
        },
        "revisions": [],
    }

    revised, revision = revise_plan_after_step(
        plan,
        completed_step_id=11,
        next_step_id=12,
        passed=False,
        reason="The local worker failed the completion contract.",
        settings={},
    )

    assert revision is not None
    assert plan["assignments"]["12"]["primary_route"]["backend"] == "pi"
    assert revised["assignments"]["11"]["status"] == "failed"
    assert revised["assignments"]["12"]["primary_route"]["backend"] == "pi"
    assert revised["assignments"]["12"]["replan_evidence_count"] == 1
    assert revision["route_changed"] is False
    assert revised["revisions"][0]["reason"].startswith("The local worker failed")

    escalated, escalation = revise_plan_after_step(
        revised,
        completed_step_id=11,
        next_step_id=12,
        passed=False,
        reason="The corrected attempt still failed the completion contract.",
        settings={},
    )
    assert escalation is not None
    assert escalation["route_changed"] is True
    assert escalated["assignments"]["12"]["primary_route"]["backend"] == "codex"


def test_actual_fallback_route_is_preserved_and_same_model_review_is_replanned(monkeypatch):
    monkeypatch.setattr(
        "distr.core.workflow.coordination_plan._free_validator_candidates",
        _fake_free_validators,
    )
    planned_implementation = {
        "backend": "pi",
        "model_provider": "ollama",
        "model": "ornith:35b",
    }
    actual_implementation = {
        "backend": "pi",
        "model_provider": "openrouter",
        "model": "tencent/hy3-preview",
        "source": "approved_route_override",
    }
    plan = {
        "assignments": {
            "12": {
                "step_id": 12,
                "role": "implementation",
                "status": "planned",
                "primary_route": planned_implementation,
            },
            "13": {
                "step_id": 13,
                "role": "review",
                "status": "planned",
                "revision": 0,
                "primary_route": dict(actual_implementation),
                "requested_review_mode": "dual",
                "review_mode": "dual",
                "evaluation_routes": [
                    {
                        "backend": "pi",
                        "model_provider": "ollama",
                        "model": "ornith:9b",
                    },
                    {
                        "backend": "kilocode",
                        "model_provider": "openrouter",
                        "model": "openrouter/free",
                    },
                ],
            },
        },
        "revisions": [],
    }

    revised, revision = revise_plan_after_step(
        plan,
        completed_step_id=12,
        next_step_id=13,
        passed=True,
        reason="Implementation passed.",
        settings={},
        actual_route=actual_implementation,
    )

    completed = revised["assignments"]["12"]
    review = revised["assignments"]["13"]
    assert completed["planned_route"] == planned_implementation
    assert completed["executed_route"] == actual_implementation
    assert completed["primary_route"] == actual_implementation
    assert review["planned_route"]["model"] == "tencent/hy3-preview"
    assert review["primary_route"]["model"] == "ornith:9b"
    assert review["primary_route"]["source"] == "coordination_actual_route_replan"
    assert review["independent_from_route"] == actual_implementation
    assert all(
        route["model"] != "tencent/hy3-preview"
        for route in review["evaluation_routes"]
    )
    assert revision is not None
    assert revision["independence_reconciled"] is True

    step_routes, role_routes = coordination_plan_routes(revised)
    assert step_routes["12"]["model"] == "tencent/hy3-preview"
    assert step_routes["13"]["model"] == "ornith:9b"
    assert role_routes["implementation"]["model"] == "tencent/hy3-preview"
    assert role_routes["review"]["model"] == "ornith:9b"


def test_consume_step_replan_resolves_free_preference_and_clears_flag(monkeypatch):
    def _fake_free(settings, *, complexity="medium", prefer_local=False):
        return {
            "backend": "pi",
            "model_provider": "ollama" if prefer_local else "openrouter",
            "model": "ornith:9b" if prefer_local else "deepseek/deepseek-r1:free",
            "source": "workflow_policy_free_local" if prefer_local else "workflow_policy_free_eligible",
        }

    monkeypatch.setattr(
        "distr.core.project_cli_backends.model_policy._free_eligible_model",
        _fake_free,
    )
    plan = {
        "assignments": {
            "12": {
                "step_id": 12,
                "position": 1,
                "role": "implementation",
                "primary_route": {"backend": "pi", "model": "auto", "complexity": "medium"},
                "needs_replan": True,
                "route_preference": "local",
                "revision": 1,
            }
        },
        "revisions": [],
    }
    revised, revision = consume_step_replan(plan, step_id=12, settings={}, run_data={})
    assert revision is not None
    assert revision["type"] == "dispatch_replan"
    assert revised["assignments"]["12"]["needs_replan"] is False
    assert revised["assignments"]["12"]["primary_route"]["model"] == "ornith:9b"
    assert revised["assignments"]["12"]["primary_route"]["source"] == "coordination_replan_dispatch"


def test_plan_steer_marks_future_steps_and_consume_reallocates(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.model_policy.apply_auto_step_role_policy",
        _fake_role_policy,
    )
    plan = {
        "complexity": "medium",
        "assignments": {
            "1": {"step_id": 1, "position": 0, "role": "planning", "primary_route": {"backend": "pi", "model": "auto"}},
            "2": {"step_id": 2, "position": 1, "role": "implementation", "primary_route": {"backend": "pi", "model": "auto"}},
            "3": {"step_id": 3, "position": 2, "role": "review", "primary_route": {"backend": "pi", "model": "auto"}},
        },
        "revisions": [],
    }
    steered, revision = apply_steering_to_plan(
        plan,
        current_step_id=1,
        message="Drop admin work and focus on checkout only",
        impact="plan",
    )
    assert revision is not None
    assert steered["assignments"]["2"]["needs_replan"] is True
    assert steered["assignments"]["3"]["needs_replan"] is True
    assert "checkout" in steered["assignments"]["2"]["steering_constraints"][0].lower()

    revised, consumed = consume_step_replan(
        steered,
        step_id=2,
        settings={},
        workflow=_workflow(adaptive=False),
    )
    assert consumed is not None
    assert revised["assignments"]["2"]["needs_replan"] is False
    assert revised["assignments"]["2"]["primary_route"]["backend"] == "pi"
