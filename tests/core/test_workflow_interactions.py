import pytest
from sqlalchemy import text

from distr.core.db import engine
from distr.core.workflow.interactions import (
    classify_reply,
    create_workflow_interaction,
    handle_telegram_workflow_reply,
    pending_interactions,
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


def test_create_reuses_same_pending_checkpoint():
    first = _create()
    second = _create()
    assert second["token"] == first["token"]
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
    assert duplicate["status_code"] == 409
    assert duplicate["idempotent"] is True


def test_chat_binding_blocks_other_private_sender():
    interaction = _create(chat_id=123)
    result = resolve_interaction(token=interaction["token"], action="approve", chat_id=999)
    assert result["status_code"] == 403


def test_freeform_direction_is_feedback_not_approval():
    action, response = classify_reply("Use Cursor and keep the database unchanged", ["approve", "stop", "feedback"])
    assert action == "feedback"
    assert response.startswith("Use Cursor")
