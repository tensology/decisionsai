"""Regression tests for the Jackcat (PROJ-178-001 / run 159-160) workflow failures."""

from __future__ import annotations

import subprocess

import pytest

from distr.core.project_cli_backends import model_policy, provider_preflight
from distr.core.project_cli_backends.timing import (
    normalize_pi_route_provider,
    resolve_pi_model_provider,
)


# --- Fix 1: Pi provider must not be derived from the model vendor prefix ---

@pytest.mark.parametrize(
    "model,configured,expected",
    [
        ("deepseek/deepseek-v4-pro", "", "openrouter"),
        ("deepseek/deepseek-v4-pro", "deepseek", "openrouter"),
        ("openai/gpt-oss-120b", "openai", "openrouter"),
        ("tencent/hy3-preview", "", "openrouter"),
        ("deepseek/deepseek-v4-pro", "openrouter", "openrouter"),
        ("nvidia/nemotron-3.5-lightning:free", "kilocode", "kilocode"),
        ("gpt-5.6-luna", "openai", "openai"),
        ("ornith:35b", "ollama", "ollama"),
    ],
)
def test_resolve_pi_model_provider(model, configured, expected):
    assert resolve_pi_model_provider(model, configured) == expected


def test_normalize_pi_route_provider_fixes_primary_and_fallback():
    route = {
        "backend": "pi",
        "model": "deepseek/deepseek-v4-pro",
        "model_provider": "deepseek",
        "fallback_model": "minimax/minimax-m3.1-flash",
        "fallback_backend": "pi",
        "fallback_model_provider": "minimax",
    }
    route = normalize_pi_route_provider(route)
    assert route["model_provider"] == "openrouter"
    assert route["fallback_model_provider"] == "openrouter"


def test_pi_exact_route_preflight_blocks_unrunnable_pair(monkeypatch):
    monkeypatch.setattr(
        provider_preflight,
        "pi_catalogue_pairs",
        lambda **_: {("openrouter", "deepseek/deepseek-v4-pro")},
    )
    blocked = provider_preflight._pi_exact_route_preflight("deepseek", "deepseek/deepseek-v4-pro")
    assert blocked is not None and blocked.ready is False
    assert "openrouter/deepseek/deepseek-v4-pro" in blocked.message
    assert provider_preflight._pi_exact_route_preflight("openrouter", "deepseek/deepseek-v4-pro") is None


def test_catalogue_checks_off_in_pytest_unless_opted_in(monkeypatch):
    monkeypatch.delenv("DECISIONS_PREFLIGHT_CATALOGUE_CHECKS", raising=False)
    assert provider_preflight.openrouter_catalogue_ids() is None


# --- Fix 2: free-first override and dead :free models ---

@pytest.mark.parametrize(
    "route,run_data,expected",
    [
        ({"route_mode": "manual"}, {}, True),
        ({"route_locked": True}, {}, True),
        ({}, {"model_route": {"route_mode": "manual"}}, True),
        ({}, {"board_provider": "tensology"}, True),
        ({}, {"routing_assessment": {"source": "tensology_approval"}}, True),
        ({"route_mode": "auto"}, {}, False),
    ],
)
def test_route_disallows_free_preference(route, run_data, expected):
    assert model_policy.route_disallows_free_preference(route, config={}, run_data=run_data) is expected


def test_drop_retired_openrouter_free_models(monkeypatch):
    monkeypatch.setattr(
        provider_preflight,
        "openrouter_catalogue_ids",
        lambda **_: {"openai/gpt-oss-120b", "thinkingmachines/inkling-small:free"},
    )
    models = [
        {"id": "openai/gpt-oss-120b:free", "provider": "openrouter"},
        {"id": "thinkingmachines/inkling-small:free", "provider": "openrouter"},
        {"id": "ornith:35b", "provider": "ollama"},
    ]
    kept = [m["id"] for m in model_policy._drop_retired_openrouter_free_models(models)]
    assert kept == ["thinkingmachines/inkling-small:free", "ornith:35b"]


def test_drop_retired_keeps_list_when_catalogue_unknown(monkeypatch):
    monkeypatch.setattr(provider_preflight, "openrouter_catalogue_ids", lambda **_: None)
    models = [{"id": "openai/gpt-oss-120b:free", "provider": "openrouter"}]
    assert model_policy._drop_retired_openrouter_free_models(models) == models


# --- Fix 4: review gate ---

def test_review_gate_rejects_snake_case_needs_input_and_gaps():
    from distr.core.workflow.router import _release_hold_findings

    output = (
        "ship_verdict: needs_input — Redis availability or code fix\n"
        "completion_gaps: CacheError try/except missing in get_major_list()\n"
    )
    findings = _release_hold_findings(output, {"step_role": "review"})
    assert any("NEEDS INPUT" in f for f in findings)
    assert any("completion gaps" in f for f in findings)


def test_review_gate_ignores_non_defect_completion_gaps_value():
    from distr.core.workflow.router import _release_hold_findings

    output = "Ship verdict: SHIP\ncompletion_gaps: all acceptance criteria covered\n"
    assert _release_hold_findings(output, {"step_role": "review"}) == []


def test_review_gate_passes_clean_review():
    from distr.core.workflow.router import _release_hold_findings

    output = "Ship verdict: SHIP\nCompletion gaps: none\nBlockers: none\n"
    assert _release_hold_findings(output, {"step_role": "review"}) == []


# --- Fix 5: chat relaunch guard ---

@pytest.mark.parametrize(
    "message,expected",
    [
        ("resume", True),
        ("Please restart the workflow", True),
        ("run it again", True),
        ("/resume", True),
        ("hello", False),
        ("what happened?", False),
    ],
)
def test_explicit_resume_detection(message, expected):
    from distr.core.workflow.relaunch_guard import is_explicit_workflow_resume

    assert is_explicit_workflow_resume(message) is expected


def test_relaunch_blocked_after_cancel_without_resume(monkeypatch):
    from distr.core.workflow import relaunch_guard

    monkeypatch.setattr(
        relaunch_guard,
        "latest_thread_run",
        lambda chat_id, workflow_id: {
            "id": 159,
            "status": "cancelled",
            "run_data": {"model_route": {"route_mode": "manual", "model": "deepseek/deepseek-v4-pro"}},
        },
    )
    blocked = relaunch_guard.relaunch_decision(1843, 374, "hello")
    assert blocked["allow"] is False and "#159" in blocked["notice"]
    resumed = relaunch_guard.relaunch_decision(1843, 374, "resume")
    assert resumed["allow"] is True
    assert resumed["inherit"]["model_route"]["route_mode"] == "manual"


# --- Fix 3: pause notification carries the question and how to reply ---

def test_waiting_message_includes_question_and_reply_path():
    from distr.core.kanban.ticket_workflow_engagement import build_operator_waiting_message

    text = build_operator_waiting_message(
        workflow_name="Development",
        ticket_title="Jackcat 500",
        step_name="Review",
        waiting_kind="worker_question",
        question="Is Redis expected to be running on the server?",
        chat_id=1843,
        run_id=159,
    )
    assert "Redis" in text
    assert "1843" in text and "159" in text


# --- Fix 5: ops-incident profile and default branch ---

def test_ops_incident_detection():
    from distr.core.tensology_client import _is_ops_incident

    assert _is_ops_incident({"title": "Site returns 500 Internal Server Error", "description": "redis connection refused"})
    assert not _is_ops_incident({"title": "Add a filter to the majors list", "description": ""})


def test_repo_default_branch_detects_master(tmp_path):
    from distr.core.tensology_client import _repo_default_branch

    subprocess.run(["git", "init", "-q", "-b", "master", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "init"],
        check=True,
    )
    assert _repo_default_branch(tmp_path) == "master"
    assert _repo_default_branch(tmp_path / "missing") == "main"


# --- Workflows audit: preset replace must drop smoke-test leftovers ---

def test_preset_replace_drops_e2e_smoke_keys():
    import inspect

    from distr.core.workflow import loop_presets

    source = inspect.getsource(loop_presets)
    assert '("slug", "e2e_smoke", "skip_human_checkpoints")' in source
