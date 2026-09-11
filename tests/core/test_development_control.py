from __future__ import annotations

import contextlib
import inspect
import json
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat
from distr.core.db.kanban import ProjectExecutionSession
from distr.core.db.projects import Project
from distr.core.db.time import utc_now_naive
from distr.core.db.workflow import AutoWorkflow, DevelopmentWorkItem
from distr.core.workflow import development_control as control


@pytest.fixture()
def development_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'development-control.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def get_session():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(control, "get_session", get_session)
    with get_session() as db:
        project_root = tmp_path / "project"
        project_root.mkdir()
        project = Project(name="Decisions", folder_location=str(project_root))
        db.add(project)
        db.flush()
        chat = Chat(
            title="Build control plane",
            project_id=project.id,
            params=json.dumps({"development": {"ticket_id": None}}),
            route_mode="auto",
            execution_profile="code",
            autonomy_level="full",
        )
        db.add(chat)
        db.flush()
        db.add(DevelopmentWorkItem(
            chat_id=chat.id,
            identity_key=f"thread:{chat.id}",
            workflow_id=None,
            project_id=project.id,
        ))
        db.commit()
        return {"session": get_session, "chat_id": chat.id, "project_id": project.id, "project_root": project_root}


def test_command_queue_is_ordered_editable_and_cancellable(development_db):
    first = control.enqueue_command(development_db["chat_id"], "Keep the existing navigation", source="web")
    second = control.enqueue_command(development_db["chat_id"], "Add browser evidence", source="telegram", source_ref="tg-42")

    assert [item["id"] for item in control.list_commands(development_db["chat_id"])] == [first["id"], second["id"]]
    assert second["source"] == "telegram"
    assert second["source_ref"] == "tg-42"

    edited = control.update_command(first["id"], content="Keep the existing navigation and spacing")
    cancelled = control.update_command(second["id"], cancel=True)
    assert edited["content"].endswith("and spacing")
    assert cancelled["status"] == "cancelled"
    assert [item["id"] for item in control.list_commands(development_db["chat_id"], include_terminal=False)] == [first["id"]]


def test_command_dispatch_steers_the_active_native_turn_without_a_workflow(development_db, monkeypatch):
    command = control.enqueue_command(development_db["chat_id"], "Re-run the mobile test")
    monkeypatch.setattr(
        "distr.core.turn_runtime.steer_active_turn",
        lambda chat_id, message: {
            "accepted": True,
            "method": "native_turn_steering",
            "runtime_id": "native",
            "event_id": "cte-steer",
        },
    )
    result = control.dispatch_command(command["id"])

    assert result["dispatched"] is True
    assert result["command"]["workflow_id"] is None
    assert result["command"]["run_id"] is None
    assert result["command"]["status"] == "delivered"
    assert result["command"]["metadata"]["delivery"]["method"] == "native_turn_steering"
    assert "apply_run_harness_steer" not in inspect.getsource(control.dispatch_command)


def test_command_stays_queued_when_no_native_turn_is_active(development_db, monkeypatch):
    command = control.enqueue_command(development_db["chat_id"], "Only steer this thread")
    monkeypatch.setattr(
        "distr.core.turn_runtime.steer_active_turn",
        lambda chat_id, message: {"accepted": False, "reason": "no_active_native_turn"},
    )
    result = control.dispatch_command(command["id"])

    assert result["dispatched"] is False
    assert result["reason"] == "no_active_native_turn"
    assert control.list_commands(development_db["chat_id"], include_terminal=False)[0]["id"] == command["id"]


def test_thread_controls_archive_export_and_redacted_snapshot(development_db):
    with development_db["session"]() as db:
        workflow = AutoWorkflow(name="Implementation audit", workflow_type="manual")
        db.add(workflow)
        db.commit()
        workflow_id = workflow.id
    controls = control.update_thread_controls(
        development_db["chat_id"],
        pinned=True,
        permission_profile={"mode": "careful"},
        remote_continuation=True,
        workflow_id=workflow_id,
    )
    assert controls["pinned"] is True
    assert controls["permission_profile"] == {"mode": "careful"}
    assert controls["workflow_id"] == workflow_id
    with development_db["session"]() as db:
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=development_db["chat_id"]).one()
        assert item.workflow_id == workflow_id

    control.enqueue_command(development_db["chat_id"], "Use token=sk-thismustberedacted and inspect /Users/paul/private/file")
    exported = control.thread_export(development_db["chat_id"])
    exported_text = json.dumps(exported)
    assert "sk-thismustberedacted" not in exported_text
    assert "/Users/paul/private/file" not in exported_text

    shared = control.create_shared_snapshot(development_db["chat_id"])
    assert control.get_shared_snapshot(shared["token"])["thread"]["id"] == development_db["chat_id"]
    assert control.get_shared_snapshot("wrong-token") is None

    assert control.archive_thread(development_db["chat_id"], archived=True)["archived"] is True
    assert control.archive_thread(development_db["chat_id"], archived=False)["archived"] is False


def test_completed_direct_turn_can_be_captured_as_project_local_skill(development_db):
    with development_db["session"]() as db:
        execution = ProjectExecutionSession(
            project_id=development_db["project_id"],
            route_type="native_turn",
            route_backend="kilocode",
            selected_model="kilo-auto/free",
            complexity="medium",
            status="completed",
            input_packet=json.dumps({"chat_id": development_db["chat_id"], "prompt": "Repair the menu"}),
            output_packet=json.dumps({
                "runtime_id": "native",
                "output": "Implemented the verified menu repair.",
                "evidence": {"verification": "passed", "token": "sk-secretvalue123"},
            }),
        )
        db.add(execution)
        db.commit()
        execution_session_id = execution.id

    result = control.capture_execution_skill(
        development_db["chat_id"],
        execution_session_id,
        name="Verified UI repair",
    )
    skill_path = development_db["project_root"] / ".decisions" / "skills" / "verified-ui-repair" / "SKILL.md"
    assert result["status"] == "created"
    assert skill_path.is_file()
    assert "Implemented the verified menu repair" in skill_path.read_text(encoding="utf-8")
    assert "sk-secretvalue123" not in skill_path.read_text(encoding="utf-8")


def test_non_completed_direct_turn_cannot_become_a_skill(development_db):
    with development_db["session"]() as db:
        execution = ProjectExecutionSession(
            project_id=development_db["project_id"],
            route_type="native_turn",
            route_backend="kilocode",
            status="failed",
            input_packet=json.dumps({"chat_id": development_db["chat_id"]}),
            output_packet="{}",
        )
        db.add(execution)
        db.commit()
        execution_session_id = execution.id

    with pytest.raises(ValueError, match="Only completed"):
        control.capture_execution_skill(development_db["chat_id"], execution_session_id)


def test_thread_owned_time_can_play_pause_reset_and_auto_pause(development_db):
    started = control.resume_thread_time(development_db["chat_id"])
    assert started["paused"] is False

    reset = control.reset_thread_time(development_db["chat_id"], seconds=95)
    assert reset["seconds"] >= 95
    paused = control.pause_thread_time(development_db["chat_id"])
    assert paused["paused"] is True
    assert paused["seconds"] >= 95

    control.resume_thread_time(development_db["chat_id"])
    with development_db["session"]() as db:
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=development_db["chat_id"]).one()
        item.time_started_at = utc_now_naive() - timedelta(minutes=5)
        item.time_last_activity_at = utc_now_naive() - timedelta(minutes=5)
        response = Chat(
            parent_id=development_db["chat_id"],
            input="Please verify it",
            response="Verified.",
            modified_date=utc_now_naive() - timedelta(minutes=4),
        )
        db.add(response)
        db.commit()
    auto_paused = control.thread_time_state(development_db["chat_id"])
    assert auto_paused["paused"] is True
    assert 330 <= auto_paused["seconds"] <= 345


def test_clear_context_preserves_transcript(development_db):
    with development_db["session"]() as db:
        row = Chat(
            parent_id=development_db["chat_id"],
            input="Implement the board model",
            response="The board model is implemented.",
        )
        db.add(row)
        db.commit()
        transcript_id = row.id

    cleared = control.clear_thread_context(development_db["chat_id"])
    assert cleared["cleared"] is True
    with development_db["session"]() as db:
        root = db.get(Chat, development_db["chat_id"])
        transcript = db.get(Chat, transcript_id)
        checkpoint = json.loads(root.additional_context)["compact_checkpoint"]
        assert checkpoint["active"] is True
        assert transcript.input == "Implement the board model"
        assert transcript.response == "The board model is implemented."


def test_clear_context_rejects_an_active_direct_agent(development_db):
    with development_db["session"]() as db:
        root = db.get(Chat, development_db["chat_id"])
        params = json.loads(root.params)
        params["development"]["execution"] = {
            "job_id": "direct-1",
            "status": "running",
        }
        root.params = json.dumps(params)
        db.commit()

    with pytest.raises(ValueError, match="Stop the active agent"):
        control.clear_thread_context(development_db["chat_id"])


def test_development_fork_has_independent_ownership_and_does_not_copy_ticket_identity(development_db):
    source_id = development_db["chat_id"]

    result = control.fork_thread(source_id, title="Independent fork")

    assert result["source_chat_id"] == source_id
    assert result["id"] != source_id
    with development_db["session"]() as db:
        fork = db.get(Chat, result["id"])
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=result["id"]).one()
        metadata = json.loads(fork.params)["development"]
        assert fork.title == "Independent fork"
        assert fork.project_id == development_db["project_id"]
        assert item.identity_key == f"chat:{result['id']}"
        assert item.local_ticket_id is None
        assert metadata["forked_from_chat_id"] == source_id
        assert "ticket_id" not in metadata


def test_development_delete_removes_the_owned_aggregate(development_db, monkeypatch):
    chat_id = development_db["chat_id"]
    monkeypatch.setattr(
        "distr.core.chat.remove_chat_transcript_audit_events",
        lambda _chat_id: 0,
    )

    result = control.delete_thread(chat_id)

    assert result["deleted"] is True
    with development_db["session"]() as db:
        assert db.get(Chat, chat_id) is None
        assert db.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).first() is None


def test_delivered_command_does_not_starve_the_next_queued_instruction(development_db, monkeypatch):
    received = []
    def steer(chat_id, message):
        received.append(message)
        return {"accepted": True}
    monkeypatch.setattr("distr.core.turn_runtime.steer_active_turn", steer)
    chat_id = development_db["chat_id"]
    control.enqueue_command(chat_id, "First instruction")
    control.enqueue_command(chat_id, "Second instruction")
    assert control.dispatch_pending(chat_id)["status"] == "delivered"
    assert control.dispatch_pending(chat_id)["status"] == "delivered"
    assert control.dispatch_pending(chat_id)["status"] == "idle"
    assert received == ["First instruction", "Second instruction"]


def test_idle_thread_without_response_stops_accumulating_time(development_db, monkeypatch):
    now = utc_now_naive()
    monkeypatch.setattr(control, "utc_now_naive", lambda: now)
    monkeypatch.setattr("distr.core.workflow.development_harness.development_execution_active", lambda _: False)
    with development_db["session"]() as db:
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=development_db["chat_id"]).one()
        item.time_paused = False
        item.time_started_at = now - timedelta(days=3)
        item.time_last_activity_at = item.time_started_at
        db.commit()
    result = control.thread_time_state(development_db["chat_id"])
    assert result["running"] is False
    assert result["seconds"] == 180
    assert control.thread_time_state(development_db["chat_id"])["seconds"] == 180
