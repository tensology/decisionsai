"""Tests for project operations instruction classification."""

from distr.core.workflow.project_ops import (
    build_execution_plan,
    classify_project_instruction,
    human_event_summary,
    human_status_label,
    suggest_skills_for_route,
)


def test_classify_automation_instruction():
    result = classify_project_instruction("Open Chrome every morning")
    assert result["route"] == "automation"


def test_classify_implement_instruction():
    result = classify_project_instruction("Fix the mobile nav on Player One Sport")
    assert result["route"] == "implement_ticket"


def test_classify_cursor_handoff():
    result = classify_project_instruction("Send this ticket to Cursor")
    assert result["route"] == "cursor_implement"


def test_classify_codex_review():
    result = classify_project_instruction("Ask Codex to inspect this before implementation")
    assert result["route"] == "codex_review"


def test_classify_queue_review():
    result = classify_project_instruction("Review the current ticket queue")
    assert result["route"] == "queue_review"


def test_build_execution_plan_requires_approval_for_implementation():
    plan = build_execution_plan("Fix the login issue", context={"project_name": "Demo"})
    assert plan["route"] == "implement_ticket"
    assert plan["requires_approval"] is True
    assert "Create a ticket" in plan["summary"]


def test_build_execution_plan_redirects_automation():
    plan = build_execution_plan("Open Chrome every morning")
    assert plan["route"] == "automation"
    assert plan["redirect_to"] == "/automations/"


def test_human_status_label_mapping():
    assert human_status_label("waiting_for_approval") == "Waiting for approval"
    assert human_status_label("running") == "Implementing"


def test_human_event_summary_defaults():
    assert "Cursor" in human_event_summary("cursor_handoff", "")


def test_suggest_skills_for_route():
    hints = suggest_skills_for_route("qa_validation", "Run QA on the latest change")
    assert "UI QA" in hints
