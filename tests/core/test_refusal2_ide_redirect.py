"""Regression 2026-10-22 18:31 SAST, chat 1789: ide_thread blocked -> model said 'thread controls
aren't available'; amend with development_thread_id=1789 (Oracle chat id); project 7 vs 9."""
import json

import distr.core.workflow.oracle_routing_rule as rr
from distr.core.agent.services.llm import tool_execution_policy as tep
from distr.core.workflow.development_threads import orchestrator_surface

PROJECTS = [(7, "Spoortrack (Old)"), (9, "DecisionsAI")]
USER = "Can you see, can you please add a thread to the Decisions AI project and ask what the purpose of this project is?"


def test_decisions_ai_resolves_to_project_9_not_7():
    assert rr.resolve_named_project(USER, PROJECTS) == 9


def test_add_a_thread_to_project_is_explicit():
    assert rr.explicit_new_thread_request(USER, PROJECTS)
    assert orchestrator_surface(USER) == "development"
    assert orchestrator_surface("what is the purpose of the Decisions AI project?") == "chat"


def _redirect(monkeypatch, thread):
    monkeypatch.setattr(rr, "_project_names", lambda: PROJECTS)
    monkeypatch.setattr(rr, "existing_project_thread_id", lambda pid: thread if pid == 9 else None)
    reason = tep.ide_thread_misroute_reason(
        "ide_thread", {"action": "prompt", "surface": "codex", "project": "DecisionsAI",
                       "instruction": "Explain the purpose"}, "please ask the Decisions AI project its purpose")
    assert reason.startswith("Blocked:") and "not an outage" in reason
    return json.loads(reason.split("development_control now with ")[1].split(" and do not")[0])


def test_redirect_names_send_instruction_for_existing_thread(monkeypatch):
    assert _redirect(monkeypatch, 1746) == {"action": "send_instruction", "thread_id": 1746,
                                            "instruction": "Explain the purpose"}


def test_redirect_names_create_thread_with_project(monkeypatch):
    assert _redirect(monkeypatch, None) == {"action": "create_thread", "instruction": "Explain the purpose",
                                            "project_id": 9}


def test_ide_thread_rejects_non_development_thread_id(monkeypatch):
    import distr.core.agent.tools.system.ide_thread as it

    monkeypatch.setattr(it, "_is_development_thread", lambda cid: False)
    tool = next(v for v in vars(it).values() if isinstance(v, type) and v.__module__ == it.__name__
                and hasattr(v, "_run") and v.__name__ != "BaseModel")
    out = json.loads(tool()._run(action="amend", amendment="x", development_thread_id=1789))
    assert out["success"] is False and "1789 is not a Development thread" in out["error"]


def test_existing_project_thread_id_query_runs():
    # Runs the real query against the test DB (DECISIONS_DB_DIR copy); must not raise.
    assert rr.existing_project_thread_id(None) is None
    result = rr.existing_project_thread_id(9)
    assert result is None or isinstance(result, int)
