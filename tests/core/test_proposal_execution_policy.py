import copy

import pytest

from distr.core.workflow.execution_mode import (
    choose_development_execution_mode, choose_proposal_execution_mode,
    proposal_execution_scope,
)


SIMPLE = {
    "title": "Check the working directory",
    "description": "Run pwd and print the acceptance marker. Do not deploy, migrate databases, change authentication, or create a workflow.",
    "tasks": ["Run pwd", "Print the marker", "Report the observed output"],
}


@pytest.mark.parametrize("surface", ["web", "native", "telegram"])
def test_same_scope_ignores_surface_wrappers_and_unrelated_assessments(surface):
    decision = choose_development_execution_mode(
        "Workflow orchestration: implementation and testing, production security, independent review. " * 30,
        assessment={"source": surface, "complexity": "high", "operational_state": "risk",
                    "proposal_scope": copy.deepcopy(SIMPLE)},
    )
    assert decision == choose_proposal_execution_mode(SIMPLE)
    assert decision["mode"] == "direct"
    assert decision["signals"] == []
    assert decision["policy"] == "proposal-scope-v1"


@pytest.mark.parametrize("scope,expected", [
    ({"title": "Migrate production authentication schema"}, "critical"),
    ({"title": "Refactor integration across the project"}, "heavy"),
    ({"title": "Inspect command output with independent review"}, "heavy"),
    ({"title": "Coordinate the change", "tasks": ["first", "second", "third", "fourth"]}, "heavy"),
])
def test_fixed_complex_and_risky_rules_repeat_exactly(scope, expected):
    decisions = [choose_proposal_execution_mode(scope) for _ in range(30)]
    assert all(decision == decisions[0] for decision in decisions)
    assert decisions[0]["mode"] == "workflow"
    assert decisions[0]["workflow_class"] == expected
    assert decisions[0]["signals"] and decisions[0]["reason"]


def test_positive_risk_survives_a_following_negative_constraint():
    decision = choose_proposal_execution_mode({"description": "Fix authentication without deploying the application."})
    assert decision["mode"] == "workflow"
    assert "risk:auth" in decision["signals"]


def test_no_describing_a_failure_does_not_hide_production_risk():
    assert choose_proposal_execution_mode({"title": "No jobs load in production"})["mode"] == "workflow"


def test_transport_metadata_is_not_part_of_the_canonical_scope():
    other = {**SIMPLE, "source": "telegram", "project_name": "Production Security", "origin": "tensology",
             "attachments": [{"filename": "workflow-migration.txt"}], "updated_at": "tomorrow"}
    assert proposal_execution_scope(other) == proposal_execution_scope(SIMPLE)
    assert choose_proposal_execution_mode(other) == choose_proposal_execution_mode(SIMPLE)


def test_tensology_auto_approval_enters_shared_dispatcher_without_preassigned_workflow(monkeypatch):
    from distr.core import tensology_client as tc
    captured = []
    mirror = []
    monkeypatch.setattr(tc, "_ensure_tensology_kanban_ticket", lambda ticket, project, workflow_id: mirror.append(workflow_id) or 296)
    monkeypatch.setattr(tc, "_execution_skill_ids", lambda ticket: ([], False))
    monkeypatch.setattr(tc, "_repo_default_branch", lambda path: "main")
    monkeypatch.setattr(tc, "_local_api_post", lambda path, body: captured.append(body) or {"task": {"id": 349}})
    ticket = {**SIMPLE, "id": 1162, "reference": "ACCEPTANCE-1", "project_id": 175,
              "execution_complexity": "high", "source_metadata": {"approval_source": "telegram"}}
    project = {"id": 2, "name": "Merrypak", "folder_location": "/tmp", "coding_backend": "pi",
               "coding_backend_model": "deepseek/deepseek-v4.1-flash"}
    cid, route = tc._create_tensology_thread(ticket, project)
    assert cid == 349 and mirror == [None]
    assert captured[0]["workflow_id"] is None
    assert captured[0]["routing_assessment"]["proposal_scope"] == proposal_execution_scope(SIMPLE)
    decision = choose_development_execution_mode(captured[0]["prompt"], assessment=captured[0]["routing_assessment"])
    assert decision["mode"] == "direct"
    assert route["model"] == "deepseek/deepseek-v4.1-flash"


def test_native_create_uses_the_same_application_api_and_fails_closed(monkeypatch):
    from distr.gui.development_hot_corner import start_desktop_proposal
    from distr.core import tensology_client as tc
    calls = []
    monkeypatch.setattr(tc, "_local_api_post", lambda path, body: calls.append((path, body)) or {"chat_id": 349})
    assert start_desktop_proposal(SIMPLE) == 349
    assert calls == [("workflows/studio/incoming/proposals/thread", {"proposal": SIMPLE})]
    monkeypatch.setattr(tc, "_local_api_post", lambda *_: {})
    with pytest.raises(ValueError, match="could not be started"):
        start_desktop_proposal(SIMPLE)
