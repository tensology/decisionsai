"""parallel (2026-10-23): per-step model tier hint for light workflow steps."""
from __future__ import annotations

import json
from types import SimpleNamespace

from distr.core.project_cli_backends.model_policy import (
    apply_step_model_tier,
    enforce_paid_route,
    route_selection_reason,
    step_model_tier,
)

SETTINGS = {
    "project_cli_low_backend": "pi",
    "project_cli_low_model": "minimax/minimax-m3.1-flash",
    "project_cli_low_model_provider": "openrouter",
    "project_cli_medium_backend": "pi",
    "project_cli_medium_model": "deepseek/deepseek-v4.1-flash",
    "project_cli_medium_model_provider": "openrouter",
    "project_cli_high_backend": "codex",
    "project_cli_high_model": "gpt-5.6-sol",
    "project_cli_high_fallback_backend": "pi",
    "project_cli_high_fallback_model": "deepseek/deepseek-v4-pro",
    "project_cli_high_fallback_model_provider": "openrouter",
}
THREAD = {
    "backend": "pi",
    "model": "deepseek/deepseek-v4-pro",
    "model_provider": "openrouter",
    "route_mode": "manual",
    "source": "thread_model_route",
}


def test_tier_parsing_and_inherit():
    assert step_model_tier({"model_tier": "medium"}) == "medium"
    assert step_model_tier({"model_tier": "fast"}) == "medium"
    assert step_model_tier({"model_tier": "inherit"}) == ""
    assert step_model_tier({}) == ""
    assert apply_step_model_tier(THREAD, config={}, settings=SETTINGS) == THREAD


def test_light_step_uses_medium_tier_in_thread_family():
    route = apply_step_model_tier(THREAD, config={"model_tier": "medium"}, settings=SETTINGS)
    assert route["backend"] == "pi"
    assert route["model_provider"] == "openrouter"
    assert route["model"] == "deepseek/deepseek-v4.1-flash"
    assert route["fallback_model"] == "deepseek/deepseek-v4-pro"
    assert route["model_tier_applied"] is True
    assert "medium tier" in route_selection_reason(route)
    # idempotent on re-application (stored plan route -> executor)
    again = apply_step_model_tier(route, config={"model_tier": "medium"}, settings=SETTINGS)
    assert again["model"] == "deepseek/deepseek-v4.1-flash"
    assert again["fallback_model"] == "deepseek/deepseek-v4-pro"


def test_never_upgrades_a_cheaper_or_equal_thread_model():
    for model in ("minimax/minimax-m3.1-flash", "deepseek/deepseek-v4.1-flash", "acme/some-mini-model"):
        thread = {**THREAD, "model": model}
        route = apply_step_model_tier(thread, config={"model_tier": "medium"}, settings=SETTINGS)
        assert route["model"] == model
        assert route.get("model_tier_skipped")


def test_other_route_family_is_left_alone():
    codex = {**THREAD, "backend": "codex", "model": "gpt-5.6-sol", "model_provider": ""}
    assert apply_step_model_tier(codex, config={"model_tier": "medium"}, settings=SETTINGS)["model"] == "gpt-5.6-sol"
    other = {**THREAD, "model_provider": "nvidia"}
    assert apply_step_model_tier(other, config={"model_tier": "medium"}, settings=SETTINGS)["model"] == THREAD["model"]


def test_free_tier_model_is_never_selected_and_paid_guard_holds():
    settings = {**SETTINGS, "project_cli_medium_model": "nvidia/nemotron-3.5-lightning:free"}
    route = apply_step_model_tier(THREAD, config={"model_tier": "medium"}, settings=settings)
    assert route["model"] == THREAD["model"]
    assert route["model_tier_skipped"] == "tier_model_is_free"
    tiered = apply_step_model_tier(THREAD, config={"model_tier": "medium"}, settings=SETTINGS)
    assert enforce_paid_route(tiered, run_data={"thread_route": THREAD})["model"] == "deepseek/deepseek-v4.1-flash"


def test_route_locked_step_keeps_its_route():
    assert apply_step_model_tier(THREAD, config={"model_tier": "medium", "route_locked": True}, settings=SETTINGS) == THREAD


def test_coordination_plan_assigns_tier_only_to_light_steps():
    from distr.core.workflow.coordination_plan import build_run_coordination_plan

    def step(i, name, role, tier=None):
        cfg = {"step_role": role, "model_policy": {"mode": "auto", "auto_route_models": True}}
        if tier:
            cfg["model_tier"] = tier
        return SimpleNamespace(id=i, position=i, name=name, config=json.dumps(cfg),
                               validation_type="llm_judgment", step_type="send_to_project_cli",
                               action_type="send_to_project_cli")

    wf = SimpleNamespace(id=374, name="Development", run_settings=json.dumps({"auto_route_models": True}), steps=[
        step(1, "Understand", "planning", "medium"),
        step(2, "Contract", "planning", "medium"),
        step(3, "Implement", "implementation"),
        step(4, "Review", "review"),
        step(5, "Fix", "implementation"),
        step(6, "Ship audit", "final_polish"),
        step(7, "Report", "reporting", "medium"),
    ])
    plan = build_run_coordination_plan(wf, {"thread_route": THREAD, "execution_route": dict(THREAD)}, settings=SETTINGS)
    models = {int(k): v["primary_route"]["model"] for k, v in plan["assignments"].items()}
    assert models == {
        1: "deepseek/deepseek-v4.1-flash",
        2: "deepseek/deepseek-v4.1-flash",
        3: "deepseek/deepseek-v4-pro",
        4: "deepseek/deepseek-v4-pro",
        5: "deepseek/deepseek-v4-pro",
        6: "deepseek/deepseek-v4-pro",
        7: "deepseek/deepseek-v4.1-flash",
    }
