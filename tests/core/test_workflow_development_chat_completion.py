import json
from unittest.mock import Mock

from tests.core.test_workflow_reply_claim import store

from distr.core.workflow.dispatcher import _finalize_development_chat_turn


def test_completed_workflow_closes_turn_and_appends_result(monkeypatch):
    complete = Mock()
    terminal = Mock()
    monkeypatch.setattr("distr.core.chat_turns.complete_turn", complete)
    monkeypatch.setattr("distr.core.chat_turns.terminal_turn", terminal)
    monkeypatch.setattr("distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge._generate_report", lambda result: "All done.")

    _finalize_development_chat_turn(17, "completed", {"success": True}, turn_id=77)

    complete.assert_called_once_with(
        17, turn_id=77, display_text="All done.", transcript_notice="All done."
    )
    terminal.assert_not_called()


def test_workflow_completion_without_launch_turn_leaves_chat_alone(monkeypatch):
    complete = Mock()
    terminal = Mock()
    monkeypatch.setattr("distr.core.chat_turns.complete_turn", complete)
    monkeypatch.setattr("distr.core.chat_turns.terminal_turn", terminal)

    _finalize_development_chat_turn(17, "completed", {"success": True}, turn_id=None)

    complete.assert_not_called()
    terminal.assert_not_called()


def test_concurrent_finalizers_publish_one_result(store, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from distr.core import chat, chat_turns
    from distr.core.db import Chat, ChatTurnEvent

    monkeypatch.setattr(chat_turns, 'get_session', store)
    monkeypatch.setattr(chat, 'get_session', store)
    monkeypatch.setattr(chat, 'record_chat_audit_event', Mock())
    monkeypatch.setattr(chat_turns, '_broadcast', Mock())
    with store() as db:
        root = Chat(title='Concurrent completion')
        db.add(root)
        db.flush()
        launch = Chat(parent_id=root.id, input='Original request')
        newer = Chat(parent_id=root.id, input='Newer request')
        db.add_all([launch, newer])
        db.flush()
        root_id, launch_id, newer_id = root.id, launch.id, newer.id
        root.params = json.dumps({'active_turn_chat_row_id': newer_id})
        db.commit()

    barrier = Barrier(2)

    def build_report(_):
        barrier.wait(timeout=5)
        return 'One workflow result'

    monkeypatch.setattr(
        'distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge._generate_report', build_report
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_finalize_development_chat_turn, root_id, 'completed', {}, turn_id=launch_id)
            for _ in range(2)
        ]
        for future in futures:
            future.result(timeout=10)

    with store() as db:
        assert db.query(ChatTurnEvent).filter_by(turn_id=launch_id, event_type='turn_completed').count() == 1
        assert db.query(Chat).filter_by(parent_id=root_id, response='One workflow result').count() == 1
        assert json.loads(db.get(Chat, root_id).params)['active_turn_chat_row_id'] == newer_id
