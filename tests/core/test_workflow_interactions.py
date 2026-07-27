import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from distr.core.db import engine
from distr.core.workflow.interactions import (
    classify_reply,
    create_workflow_interaction,
    handle_telegram_workflow_reply,
    pending_interactions,
    record_telegram_delivery,
    record_telegram_delivery_error,
    reissue_workflow_interaction,
    resolve_interaction,
    telegram_reply_markup,
)


@pytest.fixture(autouse=True)
def _clear_interactions():
    # Force table creation before cleaning it.
    pending_interactions()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM workflow_interactions"))
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM workflow_interactions"))


def _create(kind="approval", chat_id=123):
    return create_workflow_interaction(
        workflow_id=10,
        run_id=20,
        step_id=30,
        kind=kind,
        telegram_chat_id=chat_id,
    )


def test_reply_markup_uses_opaque_token_and_kind_specific_actions():
    interaction = _create(kind="route_approval")
    markup = telegram_reply_markup(interaction)
    callbacks = [button["callback_data"] for button in markup["inline_keyboard"][0]]
    assert callbacks == [
        f"wf:{interaction['token']}:approve",
        f"wf:{interaction['token']}:reject",
    ]
    assert "20" not in callbacks[0]


def test_voice_qualification_keyboard_does_not_offer_callback_shortcut():
    interaction = _create(kind="qualification_telegram_voice")

    markup = telegram_reply_markup(interaction)
    labels = [button["text"] for row in markup["inline_keyboard"] for button in row]

    assert labels == ["Stop"]


def test_voice_qualification_rejects_non_voice_continue():
    interaction = _create(kind="qualification_telegram_voice")

    result = resolve_interaction(
        token=interaction["token"],
        action="continue",
        response_text="continue",
        source="telegram_callback",
        chat_id=123,
    )

    assert result["status_code"] == 400
    assert "voice note" in result["error"]
    assert pending_interactions(chat_id=123)[0]["status"] == "pending"


def test_create_reuses_same_pending_checkpoint():
    first = _create()
    second = _create()
    assert second["token"] == first["token"]
    assert len(pending_interactions(chat_id=123)) == 1


def test_telegram_delivery_acknowledgment_updates_exact_pending_interaction():
    interaction = _create()

    assert record_telegram_delivery(
        token=interaction["token"],
        telegram_chat_id=123,
        telegram_message_id=987,
        reply_markup_sent=True,
    ) is True

    row = pending_interactions(chat_id=123)[0]
    assert row["telegram_message_id"] == "987"
    assert row["error"] is None


def test_telegram_delivery_failure_is_visible_on_pending_interaction():
    interaction = _create()

    assert record_telegram_delivery_error(
        token=interaction["token"],
        error="Telegram returned 429",
    ) is True

    assert pending_interactions(chat_id=123)[0]["error"] == "Telegram returned 429"


def test_reissue_preserves_pending_checkpoint_and_resends_exact_question(monkeypatch):
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun

    with Session(engine) as db:
        workflow = AutoWorkflow(name="interaction reissue test")
        db.add(workflow)
        db.flush()
        run = AutoWorkflowRun(
            workflow_id=workflow.id,
            status="waiting",
            run_data=json.dumps({
                "waiting_kind": "approval",
                "waiting_prompt": "Approve the proposed route or stop this run.",
            }),
        )
        db.add(run)
        db.commit()
        run_id = int(run.id)
        workflow_id = int(workflow.id)

    interaction = create_workflow_interaction(
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=None,
        kind="approval",
        telegram_chat_id=123,
    )
    captured = {}

    def fake_notify(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        "distr.core.kanban.ticket_workflow_engagement.notify_ticket_workflow_progress",
        fake_notify,
    )
    result = reissue_workflow_interaction(run_id, workflow_id=workflow_id)

    assert result["success"] is True
    assert result["interaction_id"] == pending_interactions(chat_id=123)[0]["id"]
    assert pending_interactions(chat_id=123)[0]["token"] == interaction["token"]
    assert captured["body"] == "Approve the proposed route or stop this run."
    assert captured["requires_response"] is True
    assert captured["state_fingerprint"].startswith(
        f"workflow-interaction:{run_id}:approval:reissue:"
    )

    with Session(engine) as db:
        db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).delete()
        db.commit()


def test_pending_interaction_expires_when_run_is_no_longer_waiting():
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun

    with Session(engine) as db:
        workflow = AutoWorkflow(name="interaction lifecycle test")
        db.add(workflow)
        db.flush()
        run = AutoWorkflowRun(workflow_id=workflow.id, status="waiting")
        db.add(run)
        db.commit()
        run_id = int(run.id)
        workflow_id = int(workflow.id)

    interaction = create_workflow_interaction(
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=None,
        kind="approval",
        telegram_chat_id=123,
    )
    assert any(row["token"] == interaction["token"] for row in pending_interactions(chat_id=123))

    with Session(engine) as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).one()
        run.status = "completed"
        db.commit()

    assert all(row["token"] != interaction["token"] for row in pending_interactions(chat_id=123))

    with Session(engine) as db:
        db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).delete()
        db.commit()


def test_create_retries_transient_sqlite_lock_without_duplicate(monkeypatch):
    from distr.core.workflow import interactions

    original = interactions._ensure_table
    calls = {"count": 0}

    def flaky_ensure_table():
        calls["count"] += 1
        if calls["count"] == 1:
            raise OperationalError("database is locked", {}, Exception("locked"))
        return original()

    monkeypatch.setattr(interactions, "_ensure_table", flaky_ensure_table)
    monkeypatch.setattr(interactions, "SQLITE_LOCK_RETRY_DELAYS_S", (0,))

    interaction = _create()

    assert interaction["token"]
    assert calls["count"] == 2
    assert len(pending_interactions(chat_id=123)) == 1


def test_multiple_pending_short_reply_is_ambiguous():
    _create()
    create_workflow_interaction(
        workflow_id=11, run_id=21, step_id=31, kind="approval", telegram_chat_id=123
    )
    result = handle_telegram_workflow_reply("yes", chat_id=123)
    assert result["error"] == "ambiguous"
    assert len(result["pending"]) == 2


def test_multiple_pending_can_target_explicit_run(monkeypatch):
    _create()
    second = create_workflow_interaction(
        workflow_id=11, run_id=21, step_id=31, kind="approval", telegram_chat_id=123
    )
    captured = []
    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.continue_waiting_step",
        lambda run_id, feedback: captured.append((run_id, feedback)) or {"success": True},
    )
    result = handle_telegram_workflow_reply("approve run #21", chat_id=123)
    assert result["success"] is True
    assert result["run_id"] == 21
    assert captured == [(21, "approve")]


def test_telegram_resolution_queues_slow_work_off_ui_thread(monkeypatch):
    interaction = _create()
    captured = []
    workers = []

    class DeferredThread:
        def __init__(self, *, target, kwargs, name, daemon):
            self.target = target
            self.kwargs = kwargs
            self.name = name
            self.daemon = daemon

        def start(self):
            workers.append(self)

    monkeypatch.setattr(
        "distr.core.workflow.interactions.threading.Thread",
        DeferredThread,
    )
    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.continue_waiting_step",
        lambda run_id, feedback: captured.append((run_id, feedback)) or {"success": True},
    )

    result = handle_telegram_workflow_reply(
        f"wf:{interaction['token']}:approve",
        chat_id=123,
        background=True,
    )

    assert result == {
        "success": True,
        "run_id": 20,
        "action": "approve",
        "queued": True,
    }
    assert captured == []
    assert len(workers) == 1

    workers[0].target(**workers[0].kwargs)
    assert captured == [(20, "approve")]
    assert pending_interactions(chat_id=123) == []


def test_route_reject_is_deterministic(monkeypatch):
    interaction = _create(kind="route_approval")
    captured = []
    monkeypatch.setattr(
        "distr.core.workflow.service.apply_run_route_approval",
        lambda run_id, approved: captured.append((run_id, approved)) or {"success": True},
    )
    result = resolve_interaction(
        token=interaction["token"], action="reject", response_text="no", chat_id=123
    )
    assert result["success"] is True
    assert captured == [(20, False)]


def test_failed_resolution_remains_retryable(monkeypatch):
    interaction = _create()
    calls = {"count": 0}

    def _continue(_run_id, _feedback):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"error": "temporary failure"}
        return {"success": True}

    monkeypatch.setattr("distr.core.workflow.dispatcher.continue_waiting_step", _continue)
    failed = resolve_interaction(token=interaction["token"], action="approve", chat_id=123)
    assert failed["error"] == "temporary failure"
    assert pending_interactions(chat_id=123)[0]["status"] == "pending"
    retried = resolve_interaction(token=interaction["token"], action="approve", chat_id=123)
    assert retried["success"] is True


def test_resolved_interaction_is_idempotent(monkeypatch):
    interaction = _create()
    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.continue_waiting_step",
        lambda *_args: {"success": True},
    )
    assert resolve_interaction(token=interaction["token"], action="approve", chat_id=123)["success"]
    duplicate = resolve_interaction(token=interaction["token"], action="approve", chat_id=123)
    assert duplicate["success"] is True
    assert duplicate["idempotent"] is True


def test_resolving_same_action_is_idempotent_but_conflict_is_rejected():
    interaction = _create()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE workflow_interactions
            SET status='resolving', resolved_action='approve'
            WHERE token=:token
        """), {"token": interaction["token"]})

    duplicate = resolve_interaction(
        token=interaction["token"], action="approve", chat_id=123
    )
    assert duplicate["success"] is True
    assert duplicate["idempotent"] is True

    conflict = resolve_interaction(
        token=interaction["token"], action="stop", chat_id=123
    )
    assert conflict["status_code"] == 409
    assert "already resolving" in conflict["error"]


def test_chat_binding_blocks_other_private_sender():
    interaction = _create(chat_id=123)
    result = resolve_interaction(token=interaction["token"], action="approve", chat_id=999)
    assert result["status_code"] == 403


def test_freeform_direction_is_feedback_not_approval():
    action, response = classify_reply("Use Cursor and keep the database unchanged", ["approve", "stop", "feedback"])
    assert action == "feedback"
    assert response.startswith("Use Cursor")


def test_telegram_qualification_probe_advances_one_durable_run_through_all_phases(monkeypatch):
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
    from distr.core.workflow.interactions import start_telegram_qualification_probe

    with Session(engine) as db:
        workflow = AutoWorkflow(name="Telegram qualification phase test")
        db.add(workflow)
        db.flush()
        run = AutoWorkflowRun(
            workflow_id=workflow.id,
            status="running",
            run_data=json.dumps({
                "qualification_scenario_id": "telegram_control_round_trip",
                "qualification_remote_control_probe": True,
            }),
        )
        db.add(run)
        db.commit()
        run_id = int(run.id)
        workflow_id = int(workflow.id)

    def fake_notify(*, run_id, step_id, **_kwargs):
        with Session(engine) as db:
            current = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).one()
            data = json.loads(current.run_data or "{}")
        create_workflow_interaction(
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            kind=data["waiting_kind"],
            telegram_chat_id=123,
        )

    monkeypatch.setattr(
        "distr.core.kanban.ticket_workflow_engagement.notify_ticket_workflow_progress",
        fake_notify,
    )

    started = start_telegram_qualification_probe(run_id, None)
    assert started["waiting_kind"] == "qualification_telegram_approval"

    phases = [
        ("approve", "telegram_callback", "qualification_telegram_voice"),
        ("continue", "telegram_voice", "qualification_telegram_steer"),
        ("feedback", "telegram_text", "qualification_telegram_stop"),
    ]
    for action, source, expected_next in phases:
        pending = pending_interactions(chat_id=123)
        assert len(pending) == 1
        resolved = resolve_interaction(
            token=pending[0]["token"],
            action=action,
            response_text=("Keep the report concise" if action == "feedback" else action),
            source=source,
            chat_id=123,
        )
        assert resolved["success"] is True
        assert pending_interactions(chat_id=123)[0]["kind"] == expected_next

    stop = pending_interactions(chat_id=123)[0]
    assert resolve_interaction(
        token=stop["token"],
        action="stop",
        response_text="stop",
        source="telegram_text",
        chat_id=123,
    )["success"]

    with Session(engine) as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).one()
        assert run.status == "cancelled"
        db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).delete()
        db.commit()
