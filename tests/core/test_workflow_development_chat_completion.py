import json
from unittest.mock import Mock

import pytest

from tests.core.test_workflow_reply_claim import store

from distr.core.workflow.dispatcher import _finalize_development_chat_turn


@pytest.mark.parametrize('first', ['workflow', 'workflow_unconfirmed', 'workflow_damaged',
                                    'workflow_inflight', 'manual', 'manual_inflight'])
def test_workflow_and_message_client_share_the_exact_result_send_scope(store, monkeypatch, first):
    from distr.core import chat_turns
    from distr.core.db import Chat, SourceReplyDraft
    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
    from distr.core.incoming import source_reply as manual
    from distr.core.workflow import reply_delivery
    from distr.core.workflow.reply_channel import resolve_reply_target
    from tests.core.test_reply_delivery import _ticket

    ticket_id = _ticket(store, linked_project_id=9, source_thread_id='original@g.us')
    monkeypatch.setattr(chat_turns, 'get_session', store)
    monkeypatch.setattr(chat_turns, '_broadcast', Mock())
    monkeypatch.setattr('distr.core.chat.record_chat_audit_event', Mock())
    monkeypatch.setattr('distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge._generate_report', lambda _: 'Work done.')
    monkeypatch.setattr(manual, 'resolve_target', lambda source: {'channel': 'whatsapp', 'jid': source['thread_id']})
    send_workflow = Mock(return_value={'success': True})
    if first == 'workflow_unconfirmed':
        send_workflow.side_effect = TimeoutError('Provider acknowledgement lost')
    monkeypatch.setitem(reply_delivery.ADAPTERS, 'whatsapp', send_workflow)
    send_manual = Mock()
    proposal = {'source': 'whatsapp', 'project_id': 9, 'source_scope': [
        {'source': 'whatsapp', 'source_thread_id': 'original@g.us', 'message_ids': ['original']},
        {'source': 'whatsapp', 'source_thread_id': 'independent@g.us', 'message_ids': ['independent']},
    ]}
    with store.begin() as db:
        root = Chat(title='Merged request', project_id=9,
                    params=json.dumps({'incoming_proposal_handoff': {'proposal': proposal}}))
        workflow = AutoWorkflow(name='Closing reply')
        db.add_all([root, workflow])
        db.flush()
        turn = Chat(parent_id=root.id, input='Do the work')
        run = AutoWorkflowRun(chat_id=root.id, ticket_id=ticket_id, workflow_id=workflow.id, status='completed')
        db.add_all([turn, run])
        db.flush()
        scope = {'run_id': run.id, 'workflow_id': workflow.id, 'chat_id': root.id, 'turn_id': turn.id}
        ticket = db.get(KanbanTicket, ticket_id)
        envelope = reply_delivery._delivery_envelope(db, ticket, resolve_reply_target(ticket), '')
    chat_id = scope['chat_id']
    _finalize_development_chat_turn(chat_id, 'completed', {'run_id': scope['run_id']}, turn_id=scope['turn_id'])
    with store() as db:
        response_id = db.query(Chat).filter_by(parent_id=chat_id, response='Work done.').one().id
    key = 'whatsapp:original@g.us'
    draft = manual.create_draft(chat_id, response_id, key, complete=lambda *_: 'Manual reply.')
    details = {'workflow': scope, 'delivery': envelope, 'ticket_id': ticket_id, 'body': 'Workflow reply.', 'channel': 'whatsapp'}
    if first == 'workflow_inflight':
        def check_manual_during_workflow_send(**_):
            with pytest.raises(manual.ReplyHistoryUnavailableError):
                manual.act_on_draft(chat_id, draft['id'], 'approve', draft['text'], sender=send_manual)
            return {'success': True}
        send_workflow.side_effect = check_manual_during_workflow_send
    if first == 'manual_inflight':
        def check_workflow_during_manual_send(*_):
            result = reply_delivery.execute_approved_workflow_reply(details)
            assert result['delivery_status'] == 'scope_conflict' and result['unconfirmed']
        send_manual.side_effect = check_workflow_during_manual_send
    if first in {'manual', 'manual_inflight'}:
        assert manual.act_on_draft(chat_id, draft['id'], 'approve', draft['text'], sender=send_manual)['status'] == 'sent'
        outcome = reply_delivery.execute_approved_workflow_reply(details)
        assert outcome['delivery_status'] == 'scope_conflict'
        send_workflow.assert_not_called()
        send_manual.side_effect = None
    else:
        outcome = reply_delivery.execute_approved_workflow_reply(details)
        assert outcome['delivered'] is (first != 'workflow_unconfirmed')
        if first == 'workflow_damaged':
            with store.begin() as db:
                db.query(OrchestratorEvent).filter_by(source='reply_delivery').one().payload = '{broken'
        _, blocked = manual.reply_review(chat_id, response_id)
        assert list(blocked) == [key]
        with pytest.raises(manual.ReplyHistoryUnavailableError):
            manual.act_on_draft(chat_id, draft['id'], 'approve', 'Retain my edit.', sender=send_manual)
        with store() as db:
            stored = db.get(SourceReplyDraft, draft['id'])
            assert stored.status == 'draft' and json.loads(stored.payload)['text'] == 'Retain my edit.'
        send_manual.assert_not_called()
        send_workflow.assert_called_once()

    independent = manual.create_draft(chat_id, response_id, 'whatsapp:independent@g.us', complete=lambda *_: 'Independent.')
    assert manual.act_on_draft(chat_id, independent['id'], 'approve', independent['text'], sender=send_manual)['status'] == 'sent'
    assert send_manual.call_args.args[0]['jid'] == 'independent@g.us'
    with store.begin() as db:
        later = Chat(parent_id=chat_id, response='A separate follow-up result.')
        db.add(later)
        db.flush()
        later_id = later.id
        later.params = json.dumps({'workflow_reply': {'run_id': scope['run_id'], 'turn_id': later_id}})
    assert manual.reply_review(chat_id, later_id)[1] == {}


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
