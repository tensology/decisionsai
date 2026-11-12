import json
import contextlib
import sys

import pytest
from sqlalchemy import create_engine,event,text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from distr.core.db import Base,engine
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
def _clear_interactions(tmp_path,monkeypatch):
    from distr.core.workflow import interactions,dispatcher,service
    from distr.core import db as db_module
    from distr.core.integrations.telegram import receipt_writes

    monkeypatch.setattr(receipt_writes, "_journal_path", lambda: tmp_path / "journal")
    monkeypatch.setattr(receipt_writes, "_pending", receipt_writes.deque())
    test_engine=create_engine(f"sqlite:///{tmp_path/'interactions.db'}")
    Base.metadata.create_all(test_engine)
    @contextlib.contextmanager
    def session():
        with Session(test_engine) as db: yield db
    monkeypatch.setattr(sys.modules[__name__],'engine',test_engine)
    monkeypatch.setattr(interactions,'engine',test_engine)
    for module in (db_module,dispatcher,service):monkeypatch.setattr(module,'get_session',session)
    # Force table creation before cleaning it.
    pending_interactions()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM workflow_interactions"))
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM workflow_interactions"))
    test_engine.dispose()


def _seed(workflow_id=10,run_id=20,step_id=30,kind='approval'):
    from distr.core.db.workflow import AutoWorkflow,AutoWorkflowRun,AutoWorkflowStep
    with Session(engine) as db:
        if db.get(AutoWorkflow,workflow_id) is None:db.add(AutoWorkflow(id=workflow_id,name='Explicit checkpoint fixture'))
        if db.get(AutoWorkflowStep,step_id) is None:db.add(AutoWorkflowStep(id=step_id,workflow_id=workflow_id,name='Checkpoint',status='waiting'))
        run=db.get(AutoWorkflowRun,run_id)
        if run is None:
            run=AutoWorkflowRun(id=run_id,workflow_id=workflow_id);db.add(run)
        run.status='waiting';run.current_step_id=step_id
        run.run_data=json.dumps({'waiting_kind':kind})
        db.commit()


def _create(kind="approval", chat_id=123):
    _seed(kind=kind)
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


def test_workflow_checkpoint_transport_cannot_switch_to_a_new_telegram_account():
    from tests.telegram.test_telegram_delivery_stability import DummyTelegramSender

    original = _create(chat_id=123)
    reissued = _create(chat_id=456)
    assert reissued["token"] == original["token"]
    sender = DummyTelegramSender()
    sender.telegram_user_id = 456

    def enqueue():
        assert sender.send_to_telegram(
            text="Approve the staged release?",
            reply_markup=telegram_reply_markup(reissued),
            interaction_token=reissued["token"],
        )
        sender._process_message_queue()

    enqueue()
    assert sender.websocket_attempts == []
    row = pending_interactions(chat_id=123)[0]
    assert row["token"] == original["token"]
    assert "original Telegram account" in row["error"]
    assert pending_interactions(chat_id=456) == []

    sender.telegram_user_id = 123
    enqueue()
    assert [payload["chat_id"] for payload in sender.websocket_attempts] == [123]
    assert sender.websocket_attempts[0]["reply_markup"] == telegram_reply_markup(original)


def test_workflow_checkpoint_requeue_after_account_switch_is_not_deduplicated():
    from tests.telegram.test_telegram_delivery_stability import DummyTelegramSender

    interaction = _create(chat_id=123)
    sender = DummyTelegramSender()
    sender.telegram_user_id = 123
    payload = {
        "text": "Approve the staged release?",
        "reply_markup": telegram_reply_markup(interaction),
        "interaction_token": interaction["token"],
    }
    assert sender.send_to_telegram(**payload)
    sender.telegram_user_id = 456
    sender._process_message_queue()
    assert sender.websocket_attempts == []

    sender.telegram_user_id = 123
    assert sender.send_to_telegram(**payload)
    sender._process_message_queue()
    assert [item["chat_id"] for item in sender.websocket_attempts] == [123]
    assert not any(key.startswith("_") for key in sender.websocket_attempts[0])


def test_workflow_checkpoint_binds_before_the_first_telegram_write(monkeypatch):
    from tests.telegram.test_telegram_delivery_stability import DummyTelegramSender

    interaction = _create(chat_id=None)
    sender = DummyTelegramSender()
    sender.telegram_user_id = 123
    attempted = []

    def send(payload):
        row = pending_interactions(chat_id=123)[0]
        assert row["telegram_chat_id"] == "123"
        assert row["telegram_message_id"] is None
        attempted.append(payload)
        return True

    monkeypatch.setattr(sender, "_send_websocket_message", send)
    assert sender.send_to_telegram(
        text="Approve the staged release?",
        reply_markup=telegram_reply_markup(interaction),
        interaction_token=interaction["token"],
    )
    sender._process_message_queue()
    assert len(attempted) == 1


def test_workflow_checkpoint_rechecks_scope_after_queueing():
    from tests.telegram.test_telegram_delivery_stability import DummyTelegramSender

    interaction = _create(chat_id=123)
    sender = DummyTelegramSender()
    sender.telegram_user_id = 123
    assert sender.send_to_telegram(
        text="Approve the staged release?",
        reply_markup=telegram_reply_markup(interaction),
        interaction_token=interaction["token"],
    )
    _seed(kind="feedback")
    sender._process_message_queue()
    assert sender.websocket_attempts == []
    assert pending_interactions(chat_id=123) == []


def test_workflow_checkpoint_pauses_transport_during_database_outage(monkeypatch):
    from distr.core.workflow import interactions
    from tests.telegram.test_telegram_delivery_stability import DummyTelegramSender

    interaction = _create(chat_id=None)
    sender = DummyTelegramSender()
    sender.telegram_user_id = 123
    assert sender.send_to_telegram(
        text="Approve the staged release?",
        reply_markup=telegram_reply_markup(interaction),
        interaction_token=interaction["token"],
    )

    def locked(**_kwargs):
        raise OperationalError("select", {}, RuntimeError("database is locked"))

    with monkeypatch.context() as scoped:
        scoped.setattr(interactions, "authorize_telegram_checkpoint_delivery", locked)
        sender._process_message_queue()
        assert sender.websocket_attempts == []
        assert sender._outbound_retry_message["client_message_id"] == interaction["token"]

    sender._process_message_queue()
    assert [item["chat_id"] for item in sender.websocket_attempts] == [123]
    assert pending_interactions(chat_id=123)[0]["telegram_chat_id"] == "123"


@pytest.mark.parametrize("write_result", ["zero", "short", "raised", "full"])
def test_workflow_qt_write_records_uncertainty_and_accepts_a_late_confirmation(monkeypatch, write_result):
    from types import MethodType, SimpleNamespace
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager
    from tests.telegram.test_telegram_delivery_stability import DummyTelegramSender

    interaction = _create()
    sender = DummyTelegramSender()
    sender.telegram_user_id = 123
    frames = []

    def send_text(payload):
        frames.append(json.loads(payload))
        count = len(payload.encode("utf-8"))
        if write_result == "raised":
            raise OSError("Simulated write failure")
        return {"zero": 0, "short": count - 1, "full": count}[write_result]

    sender.socket = SimpleNamespace(sendTextMessage=send_text)
    monkeypatch.setattr(sender, "_send_websocket_message",
                        MethodType(TelegramWebSocketManager._send_websocket_message, sender))
    assert sender.send_to_telegram("Approve the staged release?", reply_markup=telegram_reply_markup(interaction),
                                   interaction_token=interaction["token"])
    sender._process_message_queue()
    row = pending_interactions(chat_id=123)[0]
    assert row["telegram_message_id"] is None
    if write_result == "full":
        assert row["error"] is None
    else:
        assert "could not be confirmed" in row["error"]

    sender._process_message_queue()
    assert len(frames) == 1
    receiver = SimpleNamespace(_mark_socket_heartbeat_seen=lambda: None)
    TelegramWebSocketManager._on_message(receiver, json.dumps({
        "type": "message_sent", "client_message_id": frames[0]["client_message_id"], "chat_id": 123,
        "telegram_message_id": 987, "reply_markup_sent": True,
    }))
    row = pending_interactions(chat_id=123)[0]
    assert row["telegram_message_id"] == "987"
    assert row["error"] is None

    write_result = "short"
    assert sender.send_to_telegram("Review the approval again?", reply_markup=telegram_reply_markup(interaction),
                                   interaction_token=interaction["token"])
    sender._process_message_queue()
    assert len(frames) == 2
    row = pending_interactions(chat_id=123)[0]
    assert row["telegram_message_id"] == "987"
    assert row["error"] is None


@pytest.mark.parametrize("write_result", ["raised", "indeterminate"])
def test_workflow_checkpoint_local_uncertainty_survives_restart(tmp_path, monkeypatch, write_result):
    from types import SimpleNamespace

    from distr.core.incoming import telegram_updates
    from distr.core.integrations.telegram import receipt_writes
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager
    from tests.telegram.test_telegram_delivery_stability import DummyTelegramSender

    interaction = _create()
    token = interaction["token"]
    sender = DummyTelegramSender()
    sender.telegram_user_id = 123
    attempted = []

    def reject_receipt_write(_conn, _cursor, statement, parameters, *_):
        if "SET ERROR=" in statement.upper() and "UPDATE WORKFLOW_INTERACTIONS" in statement.upper():
            raise OperationalError(statement, parameters, RuntimeError("database is locked"))

    def send(payload):
        attempted.append(payload)
        if write_result == "raised":
            raise OSError("Connection interrupted after socket accepted the frame")
        return None

    monkeypatch.setattr(sender, "_send_websocket_message", send)
    monkeypatch.setattr(telegram_updates, "_manager", lambda: None)
    assert sender.send_to_telegram("Approve the staged release?", reply_markup=telegram_reply_markup(interaction),
                                   interaction_token=token)
    event.listen(engine, "before_cursor_execute", reject_receipt_write)
    try:
        sender._process_message_queue()
        assert pending_interactions(chat_id=123)[0]["error"] is None
    finally:
        event.remove(engine, "before_cursor_execute", reject_receipt_write)

    sender._process_message_queue()
    payload, = attempted
    entry, = (tmp_path / "journal").glob("*.json")
    assert json.loads(entry.read_text()) == ["checkpoint-error", token, 1, False]
    receipt_writes._pending.clear()
    telegram_updates.flush_execution_updates()
    row = pending_interactions(chat_id=123)[0]
    assert row["telegram_message_id"] is None
    assert row["error"] == "Telegram delivery could not be confirmed."

    receiver = SimpleNamespace(_mark_socket_heartbeat_seen=lambda: None)
    TelegramWebSocketManager._on_message(receiver, json.dumps({
        "type": "message_sent", "client_message_id": payload["client_message_id"], "chat_id": 123,
        "telegram_message_id": 987, "reply_markup_sent": True,
    }))
    row = pending_interactions(chat_id=123)[0]
    assert row["telegram_message_id"] == "987"
    assert row["error"] is None
    assert len(attempted) == 1
    assert not list((tmp_path / "journal").glob("*.json"))


def test_workflow_checkpoint_retries_when_the_socket_closes_before_any_write(monkeypatch):
    from types import MethodType, SimpleNamespace
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager
    from tests.telegram.test_telegram_delivery_stability import DummyTelegramSender

    interaction = _create()
    sender = DummyTelegramSender()
    sender.telegram_user_id = 123
    frames = []
    connection_checks = iter([True, False, True, True])

    def send_text(payload):
        frames.append(json.loads(payload))
        return len(payload.encode("utf-8"))

    sender.socket = SimpleNamespace(sendTextMessage=send_text)
    monkeypatch.setattr(sender, "is_connected", lambda: next(connection_checks))
    monkeypatch.setattr(sender, "_send_websocket_message",
                        MethodType(TelegramWebSocketManager._send_websocket_message, sender))
    assert sender.send_to_telegram("Approve the staged release?", reply_markup=telegram_reply_markup(interaction),
                                   interaction_token=interaction["token"])
    sender._process_message_queue()
    assert frames == []
    assert sender._outbound_retry_message["client_message_id"] == interaction["token"]
    assert pending_interactions(chat_id=123)[0]["error"] is None

    sender._process_message_queue()
    assert len(frames) == 1
    assert frames[0]["client_message_id"].endswith(":2")
    assert sender._outbound_retry_message is None


def test_workflow_checkpoint_waits_for_main_thread_socket_write():
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from unittest.mock import Mock
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager

    socket_write, signal = Mock(), Mock()
    sender = SimpleNamespace(socket=SimpleNamespace(sendTextMessage=socket_write), is_connected=lambda: True,
                             _send_ws_text_signal=SimpleNamespace(emit=signal))
    with ThreadPoolExecutor(max_workers=1) as worker:
        result = worker.submit(TelegramWebSocketManager._send_websocket_message, sender, {
            "type": "send_message", "client_message_id": "workflow-checkpoint:abcdefghijklmnop:1",
        })
        assert result.result() is False
    socket_write.assert_not_called()
    signal.assert_not_called()


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


@pytest.fixture
def crowded_development_checkpoints(monkeypatch):
    from types import SimpleNamespace

    from distr.core.db import Chat
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflowRun
    from tests.core.test_workflow_studio_task_api import _client

    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {})
    monkeypatch.setattr("distr.core.workflow.development_control.list_commands", lambda *_args, **_kwargs: [])
    with Session(engine) as db:
        own_project = Project(name="Own project")
        other_project = Project(name="Other project")
        db.add_all([own_project, other_project])
        db.flush()
        own = Chat(id=17, title="Own thread", project_id=own_project.id)
        other = Chat(id=18, title="Other thread", project_id=other_project.id)
        db.add_all([own, other])
        db.flush()
        child = Chat(title="Own execution turn", parent_id=own.id, project_id=own_project.id)
        empty = Chat(title="Direct thread", project_id=own_project.id)
        db.add_all([child, empty])
        db.flush()
        own_id, other_id, child_id, empty_id = own.id, other.id, child.id, empty.id
        db.commit()

    def checkpoint(workflow_id, run_id, step_id, thread_id, recipient):
        _seed(workflow_id=workflow_id, run_id=run_id, step_id=step_id)
        with Session(engine) as db:
            db.get(AutoWorkflowRun, run_id).chat_id = thread_id
            db.commit()
        return create_workflow_interaction(
            workflow_id=workflow_id, run_id=run_id, step_id=step_id, kind="approval",
            telegram_chat_id=recipient,
        )["token"]

    own_run = 99
    own_token = checkpoint(44, own_run, 30, child_id, 123)
    other_tokens = {
        checkpoint(11, run_id, 31, other_id, 456) for run_id in range(40, 61)
    }
    return SimpleNamespace(
        own_id=own_id, other_id=other_id, empty_id=empty_id, own_run=own_run,
        own_token=own_token, other_tokens=other_tokens, client=_client(),
    )


def test_development_control_state_scopes_checkpoints_before_the_global_limit(crowded_development_checkpoints):
    scope = crowded_development_checkpoints
    assert scope.own_token not in {row["token"] for row in pending_interactions()}
    assert [row["token"] for row in pending_interactions(chat_id=123)] == [scope.own_token]
    path = "/api/workflows/studio/control-state"

    def tokens(**params):
        response = scope.client.get(path, params=params)
        assert response.status_code == 200, response.text
        return {row["token"] for row in response.json()["interactions"]}

    assert tokens(chat_id=scope.own_id, run_id=scope.own_run) == {scope.own_token}
    assert tokens(run_id=scope.own_run) == {scope.own_token}
    assert tokens(chat_id=scope.own_id) == {scope.own_token}
    assert tokens(chat_id=scope.other_id, run_id=scope.own_run) == set()
    other_visible = tokens(chat_id=scope.other_id)
    assert len(other_visible) == 20 and other_visible <= scope.other_tokens
    assert tokens(chat_id=scope.empty_id) == set()
    assert tokens() == set()


@pytest.fixture
def paired_checkpoint(monkeypatch):
    from types import SimpleNamespace
    from sqlalchemy.orm import sessionmaker
    from distr.core.db import Chat
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.workflow import development_control, development_harness, interactions
    from distr.gui.web.security import INTERNAL_AUTH_HEADER
    from tests.core.test_workflow_studio_task_api import _client

    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(development_harness, "get_session", factory)
    monkeypatch.setattr(development_control, "get_session", factory)
    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {})
    monkeypatch.setattr("distr.gui.web.security.get_internal_api_token", lambda: "fixture-only")
    workers = []

    class HeldWorker:
        def __init__(self, *, kwargs, **_):
            self.kwargs = kwargs

        def start(self):
            workers.append(self.kwargs)

    monkeypatch.setattr(interactions, "threading", SimpleNamespace(Thread=HeldWorker))
    _seed()
    _seed(workflow_id=11, run_id=21, step_id=31)
    prompt = "Review the signed delivery receipt before releasing the result."
    with factory.begin() as db:
        db.add_all([Project(id=9, name="Delivery"), Project(id=10, name="Other project")])
        db.flush()
        params = json.dumps({"development": {"source_type": "workflow"}})
        db.add_all([
            Chat(id=17, title="Delivery proposal", project_id=9, params=params),
            Chat(id=18, title="Other thread", project_id=10, params=params),
        ])
        for run_id, chat_id, project_id in [(20, 17, 9), (21, 18, 10)]:
            run = db.get(AutoWorkflowRun, run_id)
            run.chat_id = chat_id
            run.run_data = json.dumps({"project_id": project_id, "waiting_kind": "approval", "waiting_prompt": prompt})
    token = create_workflow_interaction(workflow_id=10, run_id=20, step_id=30, kind="approval")["token"]
    other = create_workflow_interaction(workflow_id=11, run_id=21, step_id=31, kind="approval")["token"]
    return SimpleNamespace(client=_client(), headers={INTERNAL_AUTH_HEADER: "fixture-only"}, token=token,
                           other=other, prompt=prompt, workers=workers, factory=factory)


def test_paired_checkpoint_claim_is_scoped_and_idempotent(paired_checkpoint):
    scope = paired_checkpoint
    path = "/api/workflows/studio/tasks/17/execution"
    state = scope.client.get(path).json()
    assert state["waiting_prompt"] == scope.prompt
    assert state["checkpoint"]["token"] == scope.token
    assert scope.other not in json.dumps(state)
    body = {"run_id": 20, "token": scope.token, "action": "approve"}
    decide = path + "/checkpoint"
    assert scope.client.post(decide, json=body).status_code == 401
    assert scope.client.post(decide, json={**body, "run_id": 21}, headers=scope.headers).status_code == 409
    assert scope.client.post(decide, json={**body, "token": scope.other}, headers=scope.headers).status_code == 409
    assert scope.client.post(decide, json={**body, "action": "feedback"}, headers=scope.headers).status_code == 422
    assert not scope.workers
    response = scope.client.post(decide, json=body, headers=scope.headers)
    assert response.json()["queued"] and response.headers["cache-control"] == "no-store"
    replay = scope.client.post(decide, json=body, headers=scope.headers)
    assert replay.json()["idempotent"]
    assert len(scope.workers) == 1 and scope.workers[0]["token"] == scope.token
    assert scope.client.post(decide, json={**body, "action": "stop"}, headers=scope.headers).status_code == 409
    applying = scope.client.get(path).json()
    assert "checkpoint" not in applying and applying["checkpoint_decision_pending"] is True
    assert scope.client.get(path.replace("/17/", "/18/")).json()["checkpoint"]["token"] == scope.other


def test_paired_checkpoint_rejects_a_changed_question(paired_checkpoint):
    from distr.core.db.workflow import AutoWorkflowRun

    scope = paired_checkpoint
    path = "/api/workflows/studio/tasks/17/execution"
    with scope.factory.begin() as db:
        run = db.get(AutoWorkflowRun, 20)
        run_data = json.loads(run.run_data)
        run_data["waiting_prompt"] = "Review the revised receipt before continuing."
        run.run_data = json.dumps(run_data)
    decision = {"run_id": 20, "token": scope.token, "action": "approve"}
    assert scope.client.post(path + "/checkpoint", json=decision, headers=scope.headers).status_code == 409
    assert not scope.workers
    current = create_workflow_interaction(workflow_id=10, run_id=20, step_id=30, kind="approval")
    state = scope.client.get(path).json()
    assert state["checkpoint"]["token"] == current["token"] != scope.token
    assert state["waiting_prompt"] == run_data["waiting_prompt"]


def test_checkpoint_revision_replay_preserves_the_first_guidance_across_surfaces(paired_checkpoint):
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.workflow.interactions import authorize_telegram_checkpoint_delivery

    scope = paired_checkpoint
    path = "/api/workflows/studio/tasks/17/execution/checkpoint"
    for token in (scope.token, scope.other):
        assert authorize_telegram_checkpoint_delivery(token=token, telegram_chat_id=123)
    body = {"run_id": 20, "token": scope.token, "action": "feedback", "response_text": "Keep the receipt."}
    assert scope.client.post(path, json=body, headers=scope.headers).json()["queued"]
    other_text = "Remove the receipt."
    competing = scope.client.post(path, json={**body, "response_text": other_text}, headers=scope.headers)
    assert competing.status_code == 409
    assert "different revision" in competing.json()["detail"].lower()
    assert body["response_text"] not in competing.text
    rejected = handle_telegram_workflow_reply(f"Run #20: {other_text}", chat_id=123, background=True)
    assert rejected["status_code"] == 409 and not rejected.get("idempotent")
    exact = handle_telegram_workflow_reply("Run #20: Keep the receipt.", chat_id=123, background=True)
    assert exact["idempotent"] and exact["run_id"] == 20
    with scope.factory.begin() as db:
        db.get(AutoWorkflowRun, 20).status = "running"
    assert scope.client.post(path, json=body, headers=scope.headers).json()["idempotent"]
    late = scope.client.post(path, json={**body, "response_text": other_text}, headers=scope.headers)
    assert late.status_code == 409 and late.json()["code"] == "checkpoint_revision_conflict"
    assert len(scope.workers) == 1 and scope.workers[0]["response_text"] == body["response_text"]
    assert scope.client.get(path.removesuffix("/checkpoint").replace("/17/", "/18/")).json()["checkpoint"]["token"] == scope.other


def test_explicit_telegram_run_cannot_resolve_the_only_other_pending_checkpoint(monkeypatch):
    item = _create()
    applied = []
    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.continue_waiting_step",
        lambda run_id, feedback, **_: applied.append((run_id, feedback)) or {"success": True},
    )
    result = handle_telegram_workflow_reply("Run #21: Keep the receipt.", chat_id=123)
    assert result["status_code"] == 409
    assert not applied
    assert [row["token"] for row in pending_interactions(chat_id=123)] == [item["token"]]
    assert handle_telegram_workflow_reply("What is waiting for run #21?", chat_id=123) is None
    assert handle_telegram_workflow_reply("Run 30 tests before continuing.", chat_id=123)["success"]
    assert applied == [(20, "Run 30 tests before continuing.")]


def test_explicit_telegram_run_is_found_outside_the_global_window(crowded_development_checkpoints, monkeypatch):
    scope = crowded_development_checkpoints
    with engine.begin() as conn:
        conn.execute(text("UPDATE workflow_interactions SET telegram_chat_id='123'"))
    assert scope.own_token not in {row["token"] for row in pending_interactions(chat_id=123)}
    applied = []
    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.continue_waiting_step",
        lambda run_id, feedback, **_: applied.append((run_id, feedback)) or {"success": True},
    )
    result = handle_telegram_workflow_reply(f"approve run #{scope.own_run}", chat_id=123)
    assert result["success"] and result["run_id"] == scope.own_run
    assert applied == [(scope.own_run, "approve")]


@pytest.mark.parametrize("interruption", ["queued", "started", "legacy", "changed", "expired"])
def test_checkpoint_startup_recovers_only_a_decision_that_never_started(
    paired_checkpoint, monkeypatch, interruption,
):
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.workflow import interactions

    scope = paired_checkpoint
    path = "/api/workflows/studio/tasks/17/execution"
    decision = {"run_id": 20, "token": scope.token, "action": "feedback", "response_text": "Keep the receipt."}
    assert scope.client.post(path + "/checkpoint", json=decision, headers=scope.headers).json()["queued"]
    first, = scope.workers
    applied = []

    def apply(**kwargs):
        applied.append(kwargs)
        if interruption == "started":
            raise SystemExit("Desktop stopped after crossing the apply boundary")
        return {"success": True}

    monkeypatch.setattr(interactions, "_apply_standard_workflow_interaction", apply)
    if interruption == "started":
        with pytest.raises(SystemExit, match="Desktop stopped"):
            interactions._apply_claimed_interaction(**first)
        interactions.recover_interrupted_checkpoint_decisions()
        assert scope.client.get(path).json()["checkpoint_decision_pending"]
    elif interruption == "legacy":
        with engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE workflow_interactions DROP COLUMN apply_worker_id")
            conn.exec_driver_sql("ALTER TABLE workflow_interactions DROP COLUMN apply_claim_id")
    elif interruption == "changed":
        with scope.factory.begin() as db:
            run = db.get(AutoWorkflowRun, 20)
            data = json.loads(run.run_data)
            data["waiting_prompt"] = "A different question."
            run.run_data = json.dumps(data)
    elif interruption == "expired":
        with engine.begin() as conn:
            conn.execute(text("UPDATE workflow_interactions SET expires_at=0 WHERE token=:token"), {"token": scope.token})

    scope.workers.clear()
    monkeypatch.setattr(interactions, "_APPLY_WORKER_ID", "restarted-process")
    blocked = interactions.recover_interrupted_checkpoint_decisions()
    assert blocked == ({(20, 30)} if interruption in {"started", "legacy"} else set())
    if interruption == "queued":
        interactions.recover_interrupted_checkpoint_decisions()
        first_recovery, repeated_recovery = scope.workers
        assert interactions._apply_claimed_interaction(**first_recovery)["success"]
        assert interactions._apply_claimed_interaction(**repeated_recovery)["status_code"] == 409
        assert len(applied) == 1 and applied[0]["response_text"] == "Keep the receipt."
        assert applied[0]["resolver_id"] == "paired-app-view"
        expected = "resolved"
    elif interruption in {"started", "legacy"}:
        assert not scope.workers and len(applied) == (interruption == "started")
        state = scope.client.get(path).json()
        assert state["checkpoint_decision_unconfirmed"] and "checkpoint" not in state
        controls = scope.client.get("/api/workflows/studio/control-state?chat_id=17&run_id=20").json()["interactions"]
        assert controls[0]["decision_status"] == "unconfirmed" and controls[0]["allowed_actions"] == []
        assert scope.client.post(path + "/checkpoint", json=decision, headers=scope.headers).status_code == 409
        assert create_workflow_interaction(workflow_id=10, run_id=20, step_id=30, kind="approval")["token"] == scope.token
        expected = "unconfirmed"
    else:
        assert not scope.workers and not applied
        expected = "expired"
    with engine.connect() as conn:
        actual = conn.execute(text("SELECT status FROM workflow_interactions WHERE token=:token"), {"token": scope.token}).scalar()
    assert actual == expected
    assert scope.client.get(path.replace("/17/", "/18/")).json()["checkpoint"]["token"] == scope.other


def test_delayed_worker_cannot_apply_the_next_claim_for_the_same_checkpoint(paired_checkpoint, monkeypatch):
    from distr.core.workflow import interactions

    scope = paired_checkpoint
    path = "/api/workflows/studio/tasks/17/execution/checkpoint"
    body = {"run_id": 20, "token": scope.token, "action": "feedback", "response_text": "Earlier revision."}
    applied = []

    def apply(**kwargs):
        applied.append(kwargs["response_text"])
        if len(applied) == 1:
            return {"error": "Fixture failed before applying any changes."}
        return {"success": True}

    monkeypatch.setattr(interactions, "_apply_standard_workflow_interaction", apply)
    assert scope.client.post(path, json=body, headers=scope.headers).json()["queued"]
    original, = scope.workers
    assert interactions._apply_claimed_interaction(**original)["error"]
    assert scope.client.post(path, json={**body, "response_text": "Current revision."}, headers=scope.headers).json()["queued"]
    assert interactions._apply_claimed_interaction(**original)["status_code"] == 409
    assert interactions._apply_claimed_interaction(**scope.workers[-1])["success"]
    assert applied == ["Earlier revision.", "Current revision."]


@pytest.mark.parametrize("bad_chat, bad_message", [(None, 988), (123, None), (123, True), (0, 988)])
def test_incomplete_telegram_receipt_cannot_replace_confirmed_controls(bad_chat, bad_message):
    token = _create()["token"]
    assert record_telegram_delivery(token=token, telegram_chat_id=123, telegram_message_id=987, reply_markup_sent=True)
    assert not record_telegram_delivery(token=token, telegram_chat_id=bad_chat, telegram_message_id=bad_message,
                                        reply_markup_sent=True)
    row = pending_interactions(chat_id=123)[0]
    assert row["telegram_message_id"] == "987"
    assert row["error"] is None


def test_checkpoint_delivery_errors_cannot_downgrade_confirmed_controls():
    token = _create()["token"]
    assert record_telegram_delivery(token=token, telegram_chat_id="123", telegram_message_id="987", reply_markup_sent=False)
    assert pending_interactions(chat_id=123)[0]["error"] == "Telegram acknowledged the message without its controls"
    assert record_telegram_delivery(token=token, telegram_chat_id=123, telegram_message_id=988, reply_markup_sent=True)
    assert not record_telegram_delivery_error(token=token, error="Late relay failure")
    assert not record_telegram_delivery(token=token, telegram_chat_id=123, telegram_message_id=987, reply_markup_sent=False)
    row = pending_interactions(chat_id=123)[0]
    assert row["telegram_message_id"] == "988"
    assert row["error"] is None


@pytest.mark.parametrize(("confirmed_without_controls", "rate_limited"), [(False, False), (False, True), (True, True)])
def test_checkpoint_failure_survives_restart_without_overwriting_a_receipt(
    tmp_path, monkeypatch, confirmed_without_controls, rate_limited,
):
    from types import SimpleNamespace

    from distr.core.incoming import telegram_updates
    from distr.core.integrations.telegram import receipt_writes
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager
    from tests.telegram.test_telegram_delivery_stability import DummyTelegramSender

    interaction = _create()
    token = interaction["token"]
    sender = DummyTelegramSender()
    sender.telegram_user_id = 123
    assert sender.send_to_telegram("Approve the release?", reply_markup=telegram_reply_markup(interaction),
                                   interaction_token=token)
    sender._process_message_queue()
    correlation = sender.websocket_attempts[0]["client_message_id"]
    receiver = SimpleNamespace(_mark_socket_heartbeat_seen=lambda: None)
    monkeypatch.setattr(telegram_updates, "_manager", lambda: None)
    if confirmed_without_controls:
        TelegramWebSocketManager._on_message(receiver, json.dumps({
            "type": "message_sent", "client_message_id": correlation, "chat_id": 123,
            "telegram_message_id": 987, "reply_markup_sent": False,
        }))

    def reject_write(_conn, _cursor, statement, parameters, *_):
        if statement.lstrip().upper().startswith("UPDATE WORKFLOW_INTERACTIONS SET"):
            raise OperationalError(statement, parameters, RuntimeError("database is locked"))

    event.listen(engine, "before_cursor_execute", reject_write)
    try:
        TelegramWebSocketManager._on_message(receiver, json.dumps({
            "type": "error", "client_message_id": correlation, "reason": "relay_rate_limited" if rate_limited else None,
            "message": "Provider detail that should never be journaled",
        }))
    finally:
        event.remove(engine, "before_cursor_execute", reject_write)

    entry, = (tmp_path / "journal").glob("*.json")
    assert json.loads(entry.read_text()) == ["checkpoint-error", token, 1, rate_limited]
    receipt_writes._pending.clear()
    telegram_updates.flush_execution_updates()
    row = pending_interactions(chat_id=123)[0]
    assert row["telegram_message_id"] == ("987" if confirmed_without_controls else None)
    expected = "Telegram relay rate limit blocked this delivery attempt." if rate_limited else "Telegram delivery could not be confirmed."
    assert row["error"] == ("Telegram acknowledged the message without its controls" if confirmed_without_controls else expected)
    assert len(sender.websocket_attempts) == 1
    assert not list((tmp_path / "journal").glob("*.json"))


@pytest.mark.parametrize("kind", ["confirmation", "confirmation_then_error", "error"])
def test_workflow_telegram_receipt_is_saved_after_a_database_lock(monkeypatch, kind):
    from types import SimpleNamespace

    from distr.core.incoming import telegram_updates
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager

    token = _create()["token"]
    _seed(workflow_id=11, run_id=21, step_id=31)
    neighbor = create_workflow_interaction(workflow_id=11, run_id=21, step_id=31, kind="approval",
                                           telegram_chat_id=123)["token"]
    receiver = SimpleNamespace(_mark_socket_heartbeat_seen=lambda: None)
    monkeypatch.setattr(telegram_updates, "_manager", lambda: None)
    packet = {"type": "message_sent", "chat_id": 123, "telegram_message_id": 987, "reply_markup_sent": True}
    if kind == "error":
        packet = {"type": "error", "message": "Telegram request was rejected"}

    def reject_receipt_write(_connection, _cursor, statement, parameters, *_):
        if statement.lstrip().upper().startswith("UPDATE WORKFLOW_INTERACTIONS SET"):
            raise OperationalError(statement, parameters, RuntimeError("database is locked"))

    event.listen(engine, "before_cursor_execute", reject_receipt_write)
    try:
        TelegramWebSocketManager._on_message(receiver, json.dumps({**packet, "client_message_id": token}))
        telegram_updates.flush_execution_updates()
        with engine.connect() as conn:
            row = conn.execute(text("SELECT telegram_message_id, error FROM workflow_interactions WHERE token=:token"),
                               {"token": token}).one()
        assert tuple(row) == (None, None)
    finally:
        event.remove(engine, "before_cursor_execute", reject_receipt_write)

    if kind == "confirmation_then_error":
        TelegramWebSocketManager._on_message(receiver, json.dumps({
            "type": "error", "client_message_id": token, "message": "Late relay error",
        }))
    telegram_updates.flush_execution_updates()
    rows = {row["token"]: row for row in pending_interactions(chat_id=123)}
    if kind != "error":
        assert rows[token]["telegram_message_id"] == "987" and rows[token]["error"] is None
    else:
        assert rows[token]["telegram_message_id"] is None
        assert rows[token]["error"] == "Telegram delivery could not be confirmed."
    assert rows[neighbor]["telegram_message_id"] is None and rows[neighbor]["error"] is None


def test_relay_checkpoint_receipt_requires_a_boolean_controls_confirmation():
    from types import SimpleNamespace
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager

    token = _create()["token"]
    receiver = SimpleNamespace(_mark_socket_heartbeat_seen=lambda: None)
    packet = {"type": "message_sent", "client_message_id": token, "chat_id": 123,
              "telegram_message_id": 987, "reply_markup_sent": "false"}
    TelegramWebSocketManager._on_message(receiver, json.dumps(packet))
    assert pending_interactions(chat_id=123)[0]["error"] == "Telegram acknowledged the message without its controls"

    packet.update(telegram_message_id=988, reply_markup_sent=True)
    TelegramWebSocketManager._on_message(receiver, json.dumps(packet))
    TelegramWebSocketManager._on_message(receiver, json.dumps({
        "type": "error", "client_message_id": token, "message": "Late relay error",
    }))
    row = pending_interactions(chat_id=123)[0]
    assert row["telegram_message_id"] == "988"
    assert row["error"] is None


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("controls_sent", [False, True])
def test_workflow_confirmation_survives_restart_with_the_desktop_offline(tmp_path, monkeypatch, legacy, controls_sent):
    from types import SimpleNamespace
    from distr.core.incoming import telegram_updates
    from distr.core.integrations.telegram import receipt_writes
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager
    from tests.telegram.test_telegram_delivery_stability import DummyTelegramSender

    interaction = _create()
    token = interaction["token"]
    correlation = token
    if not legacy:
        sender = DummyTelegramSender()
        sender.telegram_user_id = 123
        assert sender.send_to_telegram("Approve the release?", reply_markup=telegram_reply_markup(interaction),
                                       interaction_token=token)
        sender._process_message_queue()
        correlation = sender.websocket_attempts[0]["client_message_id"]
    monkeypatch.setattr(telegram_updates, "_manager", lambda: None)
    receiver = SimpleNamespace(_mark_socket_heartbeat_seen=lambda: None)

    def reject_write(_conn, _cursor, statement, parameters, *_):
        if statement.lstrip().upper().startswith("UPDATE WORKFLOW_INTERACTIONS SET"):
            raise OperationalError(statement, parameters, RuntimeError("database is locked"))

    event.listen(engine, "before_cursor_execute", reject_write)
    try:
        TelegramWebSocketManager._on_message(receiver, json.dumps({
            "type": "message_sent", "client_message_id": correlation, "chat_id": 123,
            "telegram_message_id": 987, "reply_markup_sent": controls_sent,
        }))
    finally:
        event.remove(engine, "before_cursor_execute", reject_write)

    entry, = (tmp_path / "journal").glob("*.json")
    expected = [token, 987, 123, None if legacy else 1]
    if not controls_sent:
        expected.append(False)
    assert json.loads(entry.read_text()) == expected
    receipt_writes._pending.clear()
    assert pending_interactions(chat_id=123)[0]["telegram_message_id"] is None
    telegram_updates.flush_execution_updates()
    row = pending_interactions(chat_id=123)[0]
    assert row["telegram_message_id"] == "987"
    assert row["telegram_receipt_attempt"] == (None if legacy else 1)
    assert row["error"] == (None if controls_sent else "Telegram acknowledged the message without its controls")
    if not legacy:
        assert len(sender.websocket_attempts) == 1
    assert not list((tmp_path / "journal").glob("*.json"))


def test_recovered_workflow_receipt_cannot_replace_a_later_attempt(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from distr.core.integrations.telegram import receipt_writes
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager
    from tests.telegram.test_telegram_delivery_stability import DummyTelegramSender

    interaction = _create()
    sender = DummyTelegramSender()
    sender.telegram_user_id = 123
    receiver = SimpleNamespace(_mark_socket_heartbeat_seen=lambda: None)

    for question in ("Approve this release?", "Please review the release approval"):
        assert sender.send_to_telegram(question, reply_markup=telegram_reply_markup(interaction),
                                       interaction_token=interaction["token"])
        sender._process_message_queue()
    older, newer = [payload["client_message_id"] for payload in sender.websocket_attempts]
    assert older != newer
    assert all(payload["reply_markup"] == telegram_reply_markup(interaction) for payload in sender.websocket_attempts)

    def acknowledge(correlation, message_id):
        TelegramWebSocketManager._on_message(receiver, json.dumps({
            "type": "message_sent", "client_message_id": correlation, "chat_id": 123,
            "telegram_message_id": message_id, "reply_markup_sent": True,
        }))

    with monkeypatch.context() as interrupted:
        interrupted.setattr(receipt_writes, "_discard_journal_entry", lambda _path: None)
        acknowledge(newer, 800)
        acknowledge(older, 900)
    assert pending_interactions(chat_id=123)[0]["telegram_message_id"] == "800"
    assert len(list((tmp_path / "journal").glob("*.json"))) == 2
    receipt_writes._pending.clear()
    receipt_writes.flush_pending_receipt_writes()
    assert pending_interactions(chat_id=123)[0]["telegram_message_id"] == "800"
    assert not list((tmp_path / "journal").glob("*.json"))


def test_workflow_receipt_migrates_an_existing_interaction_without_losing_its_confirmation():
    from distr.core.workflow.interactions import authorize_telegram_checkpoint_delivery

    token = _create()["token"]
    assert record_telegram_delivery(token=token, telegram_chat_id=123, telegram_message_id=987, reply_markup_sent=True)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE workflow_interactions DROP COLUMN telegram_send_attempt")
        conn.exec_driver_sql("ALTER TABLE workflow_interactions DROP COLUMN telegram_receipt_attempt")

    assert authorize_telegram_checkpoint_delivery(token=token, telegram_chat_id=123) == 1
    assert pending_interactions(chat_id=123)[0]["telegram_message_id"] == "987"
    assert not record_telegram_delivery(token=token, telegram_chat_id=123, telegram_message_id=990,
                                        reply_markup_sent=True, attempt=2)
    assert not record_telegram_delivery(token=token, telegram_chat_id=456, telegram_message_id=990,
                                        reply_markup_sent=True, attempt=1)
    assert record_telegram_delivery(token=token, telegram_chat_id=123, telegram_message_id=989,
                                    reply_markup_sent=True, attempt=1)
    assert not record_telegram_delivery(token=token, telegram_chat_id=123, telegram_message_id=987,
                                        reply_markup_sent=True)
    assert pending_interactions(chat_id=123)[0]["telegram_message_id"] == "989"


def test_reissue_preserves_pending_checkpoint_and_resends_exact_question(monkeypatch):
    from types import SimpleNamespace

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
    manager = SimpleNamespace(telegram_user_id=456)
    monkeypatch.setattr(
        "distr.core.kanban.ticket_workflow_engagement._telegram_manager_from_app",
        lambda: manager,
    )
    wrong_recipient = reissue_workflow_interaction(run_id, workflow_id=workflow_id)
    assert wrong_recipient["status_code"] == 409
    assert "original Telegram account" in wrong_recipient["error"]
    assert captured == {}

    manager.telegram_user_id = 123
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
        run = AutoWorkflowRun(workflow_id=workflow.id, status="waiting",run_data=json.dumps({'waiting_kind':'approval'}))
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
    _seed(11,21,31)
    create_workflow_interaction(
        workflow_id=11, run_id=21, step_id=31, kind="approval", telegram_chat_id=123
    )
    result = handle_telegram_workflow_reply("yes", chat_id=123)
    assert result["error"] == "ambiguous"
    assert len(result["pending"]) == 2


@pytest.mark.parametrize("reply", ["approve run #21", "approve run 21", "approve for run #21"])
def test_multiple_pending_can_target_explicit_run(monkeypatch, reply):
    _create()
    _seed(11,21,31)
    second = create_workflow_interaction(
        workflow_id=11, run_id=21, step_id=31, kind="approval", telegram_chat_id=123
    )
    captured = []
    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.continue_waiting_step",
        lambda run_id, feedback, **kwargs: captured.append((run_id, feedback)) or {"success": True},
    )
    result = handle_telegram_workflow_reply(reply, chat_id=123)
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
        lambda run_id, feedback, **kwargs: captured.append((run_id, feedback)) or {"success": True},
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
        lambda run_id, approved, **kwargs: captured.append((run_id, approved)) or {"success": True},
    )
    result = resolve_interaction(
        token=interaction["token"], action="reject", response_text="no", chat_id=123
    )
    assert result["success"] is True
    assert captured == [(20, False)]


def test_failed_resolution_remains_retryable(monkeypatch):
    interaction = _create()
    calls = {"count": 0}

    def _continue(_run_id, _feedback, **kwargs):
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
        lambda *_args, **kwargs: {"success": True},
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


@pytest.mark.parametrize(("reply", "expected", "response"), [
    ("Do not remove the receipt.", "feedback", "Do not remove the receipt."),
    ("Don't stop the approval step.\n  Add a receipt check.", "feedback", "Don't stop the approval step.\n  Add a receipt check."),
    ("Keep option 2 in the report.", "feedback", "Keep option 2 in the report."),
    ("Can you stop the run?", None, "Can you stop the run?"),
    ("Could we approve the draft later?", None, "Could we approve the draft later?"),
    ("Revision: Can you verify the receipt?", "feedback", "Can you verify the receipt?"),
    ("Please stop this workflow.", "stop", "Please stop this workflow."),
    ("No, stop.", "stop", "No, stop."),
    ("Do not proceed.", "stop", "Do not proceed."),
    ("Yes, go ahead.", "approve", "Yes, go ahead."),
    ("Yes, please go ahead.", "approve", "Yes, please go ahead."),
    ("Try option 2.", "model_1", "Try option 2."),
])
def test_checkpoint_reply_distinguishes_decisions_questions_and_guidance(reply, expected, response):
    assert classify_reply(reply, ["approve", "stop", "feedback", "model_1"]) == (expected, response)


def test_named_telegram_revision_preserves_references_to_other_runs(paired_checkpoint):
    from distr.core.workflow.interactions import authorize_telegram_checkpoint_delivery

    scope = paired_checkpoint
    for token in (scope.token, scope.other):
        assert authorize_telegram_checkpoint_delivery(token=token, telegram_chat_id=123)
    guidance = "Do not stop run #21.\nCompare option 2 in its report."
    ambiguous = handle_telegram_workflow_reply(f"Revision: {guidance}", chat_id=123, background=True)
    assert ambiguous["error"] == "ambiguous" and not scope.workers
    sent = handle_telegram_workflow_reply(f"Run #20: Revision: {guidance}", chat_id=123, background=True)
    assert sent["queued"] and sent["action"] == "feedback" and sent["run_id"] == 20
    assert [worker["response_text"] for worker in scope.workers] == [guidance]
    assert scope.client.get("/api/workflows/studio/tasks/18/execution").json()["checkpoint"]["token"] == scope.other


def test_telegram_voice_probe_keeps_its_documented_continue_phrase(monkeypatch):
    from distr.core.workflow import interactions

    _create(kind="qualification_telegram_voice")
    applied = []
    monkeypatch.setattr(
        interactions, "_advance_telegram_qualification_probe",
        lambda **decision: applied.append(decision["action"]) or {"success": True},
    )
    phrase = "Continue the remote control check."
    assert handle_telegram_workflow_reply(phrase, chat_id=123, source="telegram_text")["status_code"] == 400
    assert not applied
    assert handle_telegram_workflow_reply(phrase, chat_id=123, source="telegram_voice")["success"]
    assert applied == ["continue"]


@pytest.mark.parametrize(("kind", "guidance"), [
    ("run_briefing", "Do not stop the receipt check."),
    ("step_review", "Do not stop the receipt check."),
    ("control_interrupt", "Stop removing receipts."),
    ("control_interrupt", "Do not use Cursor for this follow-up."),
])
def test_checkpoint_dispatch_keeps_explicit_revision_guidance_literal(monkeypatch, kind, guidance):
    from unittest.mock import Mock
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.workflow import dispatcher, run_briefing, service
    from distr.core.workflow.router import StepRouter

    item = _create(kind=kind)
    with Session(engine) as db:
        run = db.get(AutoWorkflowRun, 20)
        run.run_data = json.dumps({"waiting_kind": kind, "pending_first_step_id": 30})
        db.commit()

    cancel = Mock(return_value=True)
    route_change = Mock(return_value={"success": True})
    dispatch = Mock(return_value={"success": True})
    reconsider = Mock(return_value={"success": True, "action": "steer"})
    review_again = Mock()
    monkeypatch.setattr(dispatcher, "cancel_run", cancel)
    monkeypatch.setattr(dispatcher, "_dispatch_workflow_step", dispatch)
    monkeypatch.setattr(service, "apply_run_harness_steer", route_change)
    monkeypatch.setattr(StepRouter, "resume_from_feedback", reconsider)
    monkeypatch.setattr(run_briefing, "enter_run_briefing_wait", review_again)
    monkeypatch.setattr(run_briefing, "gather_run_briefing_context", lambda _: None)
    monkeypatch.setattr("distr.core.workflow.steering_memory.record_run_steering_feedback", lambda **_: None)

    result = handle_telegram_workflow_reply(f"Revision: {guidance}", chat_id=123)
    assert result["success"] and result["action"] == "feedback" and result["run_id"] == item["run_id"]
    cancel.assert_not_called()
    route_change.assert_not_called()
    with Session(engine) as db:
        run = db.get(AutoWorkflowRun, 20)
        data = json.loads(run.run_data)
        if kind == "run_briefing":
            assert run.status == "waiting" and data["run_briefing_steering"] == guidance
            review_again.assert_called_once()
            dispatch.assert_not_called()
        elif kind == "step_review":
            assert reconsider.call_args.args == (30, 20, guidance)
            dispatch.assert_not_called()
        else:
            assert run.status == "running" and data["feedback"] == guidance
            dispatch.assert_called_once_with(20, 30)


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


def test_workflow_reply_message_is_human():
    from distr.core.workflow.interactions import workflow_reply_message

    approve = workflow_reply_message({"action": "approve", "run_id": 167})
    feedback = workflow_reply_message({"action": "feedback", "run_id": 167})
    stop = workflow_reply_message({"action": "stop", "run_id": 167})

    assert "carry on" in approve
    assert "accepted:" not in approve
    assert "passed that to run #167" in feedback
    assert "stopped run #167" in stop


def test_affirmative_and_question_classification():
    allowed = ["continue", "stop", "feedback"]
    assert classify_reply("what's actually waiting?", allowed)[0] is None
    assert classify_reply("why is it blocked", allowed)[0] is None
    assert classify_reply("yes go ahead", allowed)[0] == "continue"
    assert classify_reply("check again", allowed)[0] == "feedback"


def test_status_question_is_not_steering_and_reaches_the_agent():
    interaction = _create(kind="worker_needs_input", chat_id=123)

    reply = handle_telegram_workflow_reply(
        "what's actually waiting?", chat_id=123, resolver_id="123"
    )

    # None means the caller falls through to the conversational agent, which can
    # answer with the run context. The checkpoint stays pending.
    assert reply is None
    assert [item["token"] for item in pending_interactions(chat_id=123)] == [interaction["token"]]
