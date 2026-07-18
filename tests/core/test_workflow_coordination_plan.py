from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

from distr.core.workflow.coordination_plan import (
    build_run_coordination_plan,
    coordination_plan_routes,
    render_coordination_map,
    revise_plan_after_step,
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
    assert revised["assignments"]["12"]["primary_route"]["backend"] == "codex"
    assert revised["assignments"]["12"]["primary_route"]["source"] == "coordination_replan"
    assert revised["revisions"][0]["reason"].startswith("The local worker failed")
