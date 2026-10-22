"""Regression 2026-10-22 18:20 SAST: 'create a new chat in the Decisions AI project and ask what
the point of this project is' was refused by development_control's conversational guard."""
import json

from distr.core.workflow.oracle_routing_rule import explicit_new_thread_request
from distr.core.workflow.development_threads import orchestrator_surface

PROJECTS = [(7, "DecisionsAI"), (3, "SpoorTrack")]
USER = "Can you create a new chat in the Decisions AI project and ask what the point of this project is?"


def test_explicit_request_in_named_project_detected():
    assert explicit_new_thread_request(USER, PROJECTS)
    assert explicit_new_thread_request("start a new thread in SpoorTrack and ask how auth works", PROJECTS)


def test_guard_kept_without_explicit_request_or_project():
    assert orchestrator_surface("What is the point of this project?") == "chat"
    assert not explicit_new_thread_request("what is the point of the DecisionsAI project?", PROJECTS)
    assert not explicit_new_thread_request("create a new chat and ask what time it is", PROJECTS)


def test_create_thread_not_refused_for_explicit_request(monkeypatch):
    import distr.core.workflow.development_threads as dt
    import distr.core.workflow.oracle_routing_rule as rr
    import distr.core.workflow.oracle_thread_handoff as handoff
    from distr.core.agent.tools.integrations.development_control import DevelopmentControlTool

    monkeypatch.setattr(rr, "_project_names", lambda: PROJECTS)
    calls = {}
    monkeypatch.setattr(dt, "ensure_development_thread", lambda **kw: calls.setdefault("kw", kw) and 4242)
    monkeypatch.setattr(handoff, "send_instruction_to_thread", lambda tid, text: {"sent": True})
    out = json.loads(DevelopmentControlTool()._run(
        action="create_thread", project_id=7, title="Project purpose",
        instruction="What is the point of this project?", last_user_message=USER))
    assert out.get("created") is True and out["thread_id"] == 4242 and out["project_id"] == 7


def test_create_thread_still_refused_for_model_initiated_question(monkeypatch):
    from distr.core.agent.tools.integrations.development_control import DevelopmentControlTool

    out = json.loads(DevelopmentControlTool()._run(
        action="create_thread", instruction="What is the point of this project?",
        last_user_message="what is the point of this project?"))
    assert out["created"] is False and "do not refuse" in out["reason"]
