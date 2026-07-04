from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace

import distr.core.db.orchestrator  # noqa: F401
import distr.core.db.kanban  # noqa: F401
import distr.core.db.projects  # noqa: F401
from distr.core.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _factory(tmp_path):
    db_path = tmp_path / "orchestrator_proactive.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextlib.contextmanager
def _session_ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _seed_work_signal(factory, tmp_path):
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.projects import Project

    with _session_ctx(factory) as session:
        project = Project(
            name="Merrypak",
            folder_location=str(tmp_path / "www.merrypak.co.za"),
            coding_backend="codex",
            in_use=True,
        )
        session.add(project)
        session.flush()

        board = KanbanBoard(
            name="Merrypak Board",
            default_project_id=project.id,
            in_use=True,
            orchestrator_policy=json.dumps(
                {
                    "harness_preferences": {
                        "frontend": {"backend": "cursor", "skills": ["react-frontend-expert"]}
                    }
                }
            ),
        )
        session.add(board)
        session.flush()

        lane = KanbanLane(board_id=board.id, name="Inbox", position=0)
        session.add(lane)
        session.flush()

        ticket = KanbanTicket(
            lane_id=lane.id,
            title="Client says checkout page is broken",
            description="Urgent: Merrypak client says the React checkout page fails on submit. Please fix today.",
            priority="critical",
            complexity="high",
            linked_project_id=project.id,
            source_provider="gmail",
            source_contact="client@example.com",
            source_thread_id="gmail-thread-1",
            source_label="Client email",
            position=0,
        )
        session.add(ticket)
        session.flush()
        return SimpleNamespace(project_id=project.id, board_id=board.id, ticket_id=ticket.id)


def test_proactive_scan_prioritizes_work_matches_project_and_emits_activity(tmp_path, monkeypatch):
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.orchestrator_proactive import run_proactive_check

    factory = _factory(tmp_path)
    ids = _seed_work_signal(factory, tmp_path)
    monkeypatch.setattr("distr.core.orchestrator.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.orchestrator_proactive.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr(
        "distr.core.external_agent_context.build_external_agent_context",
        lambda limit=8: {
            "codex_threads": [
                {
                    "cwd": str(tmp_path / "www.merrypak.co.za"),
                    "title": "Fix Merrypak checkout validation",
                }
            ],
            "cursor_workspaces": [{"folder": str(tmp_path / "www.merrypak.co.za")}],
        },
    )

    result = run_proactive_check(limit=5)

    assert result["success"] is True
    assert result["summary"]["total_candidates"] == 1
    candidate = result["candidates"][0]
    assert candidate["priority"] == "critical"
    assert candidate["priority_score"] >= 90
    assert candidate["project_id"] == ids.project_id
    assert candidate["project_name"] == "Merrypak"
    assert candidate["board_id"] == ids.board_id
    assert candidate["source"] == "gmail"
    assert candidate["recommended_action"] == "ask_approval_to_dispatch"
    assert "Merrypak" in candidate["approval_question"]
    assert "Codex" in candidate["developer_context"]["recent_surfaces"]
    assert "Cursor" in candidate["developer_context"]["recent_surfaces"]
    assert "Hermes" not in result["spoken_summary"]

    with _session_ctx(factory) as session:
        events = session.query(OrchestratorEvent).filter(OrchestratorEvent.event_type == "proactive_work_candidate").all()
        assert len(events) == 1
        payload = json.loads(events[0].payload)
        assert payload["candidate"]["ticket_id"] == ids.ticket_id


def test_dispatch_proactive_candidate_runs_project_backend_after_approval(tmp_path, monkeypatch):
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.orchestrator_proactive import dispatch_proactive_candidate, run_proactive_check
    from distr.core.project_cli_backends.base import BackendTaskResult

    factory = _factory(tmp_path)
    ids = _seed_work_signal(factory, tmp_path)
    monkeypatch.setattr("distr.core.orchestrator.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.orchestrator_proactive.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.external_agent_context.build_external_agent_context", lambda limit=8: {})
    monkeypatch.setattr(
        "distr.core.project_cli_backends.get_backend",
        lambda backend_id: SimpleNamespace(setup_status=lambda: SimpleNamespace(ready=True)),
    )
    monkeypatch.setattr(
        "distr.core.orchestrator.inspect_visual_baseline_readiness",
        lambda **kwargs: {"ready": True, "missing_screen_count": 0},
    )

    calls = []

    async def fake_run_project_task(project, instruction, **kwargs):
        calls.append({"project": project.name, "instruction": instruction, "kwargs": kwargs})
        return BackendTaskResult(
            True,
            kwargs.get("backend_id_override") or "codex",
            "codex",
            output="Status: completed\nSummary: queued",
            execution_session_id=123,
        )

    monkeypatch.setattr("distr.core.orchestrator_proactive.run_project_task", fake_run_project_task)

    scan = run_proactive_check(limit=5)
    candidate_id = scan["candidates"][0]["candidate_id"]
    result = dispatch_proactive_candidate(candidate_id, approved_by="telegram")

    assert result["success"] is True
    assert result["backend_id"] == "cursor"
    assert result["execution_session_id"] == 123
    assert calls[0]["project"] == "Merrypak"
    assert "checkout page is broken" in calls[0]["instruction"]
    assert calls[0]["kwargs"]["ticket_id"] == ids.ticket_id
    assert calls[0]["kwargs"]["origin"] == "proactive_orchestrator"
    assert calls[0]["kwargs"]["backend_id_override"] == "cursor"

    with _session_ctx(factory) as session:
        event_types = [row.event_type for row in session.query(OrchestratorEvent).order_by(OrchestratorEvent.id).all()]
        assert "proactive_work_dispatched" in event_types


def test_proactive_orchestrator_tool_is_voice_first(monkeypatch):
    from distr.core.agent.tools.system.proactive_orchestrator import ProactiveOrchestratorTool

    monkeypatch.setattr(
        "distr.core.orchestrator_proactive.run_proactive_check",
        lambda **kwargs: {
            "success": True,
            "spoken_summary": "I found one important work item for Merrypak and I would ask before dispatching it.",
            "summary": {"total_candidates": 1},
            "candidates": [{"candidate_id": 7, "title": "Checkout broken"}],
        },
    )

    result = ProactiveOrchestratorTool()._run(action="scan")

    assert result.startswith("I found one important work item for Merrypak")
    assert "REFERENCE:" in result
    assert "candidate_id" in result
    assert "Hermes" not in result.split("REFERENCE:")[0]


def test_proactive_orchestrator_tool_builds_daily_plan_from_context(monkeypatch):
    from distr.core.agent.tools.system.proactive_orchestrator import ProactiveOrchestratorTool

    captured = {}

    def fake_build(self, settings):
        return SimpleNamespace(
            chat_history=[{"role": "user", "content": "What matters today?"}],
            kanban_summary=[{"board_name": "DecisionsAI", "total_tickets": 4}],
            scheduled_sessions=[{"name": "Morning scan"}],
            unfinished_workflows=[{"instruction": "Finish workflow"}],
            skills=[{"id": "ecc"}],
            developer_context={"active_project": {"name": "DecisionsAI"}},
            work_scan={"whatsapp": {"messages": 2}, "gmail": {"messages": 1}},
            memory_user="likes direct, high-signal updates",
            memory_long_term="prefers voice notes",
        )

    def fake_generate(scope, settings, bundle, instruction):
        captured["scope"] = scope
        captured["instruction"] = instruction
        captured["bundle"] = bundle
        return "## Today\n- Handle the DecisionsAI board.\n- Check WhatsApp and Gmail.", {"scope": scope}

    monkeypatch.setattr("distr.core.initiative.context.ContextAssembler.build", fake_build)
    monkeypatch.setattr("distr.core.initiative.planners.generate_planner_markdown", fake_generate)
    monkeypatch.setattr(
        "distr.core.initiative.planners.tts_excerpt_from_markdown",
        lambda markdown, max_len=650: "Handle the DecisionsAI board, then check WhatsApp and Gmail.",
    )

    result = ProactiveOrchestratorTool()._run(action="daily_plan", from_automation_run=True)

    assert result.startswith("Handle the DecisionsAI board")
    assert "REFERENCE:" in result
    assert '"action": "daily_plan"' in result
    assert '"work_scan_sources"' in result
    assert captured["scope"] == "day"


def test_proactive_orchestrator_daily_plan_executes_implied_actions(monkeypatch):
    from distr.core.agent.tools.system.proactive_orchestrator import ProactiveOrchestratorTool

    executed = []

    def fake_build(self, settings):
        return SimpleNamespace(
            chat_history=[],
            kanban_summary=[],
            scheduled_sessions=[],
            unfinished_workflows=[],
            skills=[],
            developer_context={},
            work_scan={
                "boards": [
                    {
                        "id": 22,
                        "name": "RelightSA",
                        "lanes": [
                            {"name": "Current", "ticket_count": 0, "tickets": []},
                            {
                                "name": "Backlog",
                                "ticket_count": 1,
                                "tickets": [{"id": 501, "title": "Send quote", "priority": "critical"}],
                            },
                        ],
                    }
                ]
            },
            memory_user="",
            memory_long_term="",
        )

    def fake_execute(**kwargs):
        executed.append(kwargs)
        return {"success": True, "message": "Moved 1 ticket to Current", "ticket_ids": [501]}

    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {"initiative_allow_ticket_lane_moves": True})
    monkeypatch.setattr("distr.core.initiative.context.ContextAssembler.build", fake_build)
    monkeypatch.setattr(
        "distr.core.initiative.planners.generate_planner_markdown",
        lambda scope, settings, bundle, instruction: ("## Outcome for Today\n- RelightSA: finish Send quote today.", {"period": "day"}),
    )
    monkeypatch.setattr(
        "distr.core.initiative.planners.tts_excerpt_from_markdown",
        lambda markdown, max_len=650: "RelightSA: finish Send quote today.",
    )
    monkeypatch.setattr("distr.core.initiative.action_handlers.execute_initiative_action", fake_execute)

    result_text = ProactiveOrchestratorTool()._run(action="daily_plan", format="json", from_automation_run=True)
    result = json.loads(result_text)

    assert result["orchestration_results"][0]["message"] == "Moved 1 ticket to Current"
    assert executed[0]["action_type"] == "ticket_lane_move"
    assert executed[0]["payload"]["ticket_ids"] == [501]


def test_proactive_orchestrator_daily_plan_respects_lane_move_permission(monkeypatch):
    from distr.core.agent.tools.system.proactive_orchestrator import ProactiveOrchestratorTool

    def fake_build(self, settings):
        return SimpleNamespace(
            chat_history=[],
            kanban_summary=[],
            scheduled_sessions=[],
            unfinished_workflows=[],
            skills=[],
            developer_context={},
            work_scan={
                "boards": [
                    {
                        "id": 22,
                        "name": "RelightSA",
                        "lanes": [
                            {"name": "Current", "ticket_count": 0, "tickets": []},
                            {
                                "name": "Backlog",
                                "ticket_count": 1,
                                "tickets": [{"id": 501, "title": "Send quote", "priority": "critical"}],
                            },
                        ],
                    }
                ]
            },
            memory_user="",
            memory_long_term="",
        )

    def fail_execute(**kwargs):
        raise AssertionError("planner should not execute lane moves without permission")

    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {"initiative_allow_ticket_lane_moves": False})
    monkeypatch.setattr("distr.core.initiative.context.ContextAssembler.build", fake_build)
    monkeypatch.setattr(
        "distr.core.initiative.planners.generate_planner_markdown",
        lambda scope, settings, bundle, instruction: ("## Outcome for Today\n- RelightSA: finish Send quote today.", {"period": "day"}),
    )
    monkeypatch.setattr(
        "distr.core.initiative.planners.tts_excerpt_from_markdown",
        lambda markdown, max_len=650: "RelightSA: finish Send quote today.",
    )
    monkeypatch.setattr("distr.core.initiative.action_handlers.execute_initiative_action", fail_execute)

    result_text = ProactiveOrchestratorTool()._run(action="daily_plan", format="json", from_automation_run=True)
    result = json.loads(result_text)

    assert result["orchestration_actions"][0]["action_type"] == "ticket_lane_move"
    assert result["orchestration_results"][0]["success"] is False
    assert "need approval" in result["orchestration_results"][0]["message"]


def test_project_activity_includes_project_events_validations_and_rules(tmp_path, monkeypatch):
    from distr.core.orchestrator import (
        emit_event,
        list_project_activity,
        record_learning_signal,
        record_validation,
    )

    factory = _factory(tmp_path)
    ids = _seed_work_signal(factory, tmp_path)
    monkeypatch.setattr("distr.core.orchestrator.get_session", lambda: _session_ctx(factory))

    emit_event(
        source="proactive_orchestrator",
        event_type="proactive_check_run",
        status="completed",
        project_id=ids.project_id,
        board_id=ids.board_id,
        summary="Checked project work sources.",
    )
    record_validation(
        project_id=ids.project_id,
        board_id=ids.board_id,
        validation_snapshot={"verdict": "pass", "validation_type": "unit", "observed": "ok"},
    )
    record_learning_signal(
        scope="project",
        scope_id=ids.project_id,
        rule_type="routing_hint",
        summary="Frontend tickets for Merrypak are best reviewed in Cursor.",
    )

    activity = list_project_activity(ids.project_id)

    assert activity["project_id"] == ids.project_id
    assert any(event["event_type"] == "proactive_check_run" for event in activity["events"])
    assert any(validation["validation_type"] == "unit" for validation in activity["validations"])
    assert any(rule["rule_type"] == "routing_hint" for rule in activity["learned_rules"])
