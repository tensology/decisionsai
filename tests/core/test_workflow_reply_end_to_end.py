"""Completed run -> durable Chat review -> explicit click -> original email ACK."""
import base64
import json
from email import policy
from email.parser import BytesParser
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from distr.core.db import Chat, ChatTurnEvent, DevApprovalRequest
from distr.core.db.kanban import KanbanLane, KanbanTicket
from distr.core.db.orchestrator import OrchestratorEvent
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
from distr.core.workflow import dev_approvals, dispatcher
from distr.gui.web.routes import dev_approvals as routes
from tests.core.test_reply_delivery import _ticket
from tests.core.test_workflow_reply_claim import store
from tests.core.test_workflow_reply_mail import gmail, posts


CLICK = {'origin': 'http://127.0.0.1:8765', 'sec-fetch-site': 'same-origin',
         'sec-fetch-mode': 'cors', 'X-Decisions-User-Action': 'click'}


@pytest.mark.parametrize("changed", ["same", "run", "ticket", "legacy"])
def test_previous_send_only_confirms_the_exact_managed_review(store, monkeypatch, changed):
    from distr.core.workflow import reply_delivery
    from distr.core.workflow.reply_channel import resolve_reply_target

    monkeypatch.setattr(routes, '_require_token', lambda request: None)
    monkeypatch.setattr(routes, '_post_card', lambda row: 'fixture-card')
    monkeypatch.setattr(routes, '_update_card', Mock())
    send = Mock(return_value={'success': True, 'message_id': 'first-provider-ack'})
    monkeypatch.setitem(reply_delivery.ADAPTERS, 'whatsapp', send)
    ticket_id = _ticket(store, linked_project_id=9)
    body = 'The change is ready for review.'
    with store() as db:
        root = Chat(title='Original work', project_id=9)
        workflow = AutoWorkflow(name='Work')
        db.add_all([root, workflow]); db.flush()
        turn = Chat(parent_id=root.id, input='Review the work')
        run = AutoWorkflowRun(workflow_id=workflow.id, chat_id=root.id, ticket_id=ticket_id, status='completed')
        db.add_all([turn, run]); db.flush()
        scope = {'workflow_id': workflow.id, 'run_id': run.id, 'chat_id': root.id, 'turn_id': turn.id}
        ticket = db.get(KanbanTicket, ticket_id)
        envelope = reply_delivery._delivery_envelope(db, ticket, resolve_reply_target(ticket), '')
        db.commit()
    original = {'ticket_id': ticket_id, 'channel': 'whatsapp', 'body': body, 'delivery': envelope, 'workflow': scope}
    assert reply_delivery.execute_approved_workflow_reply(original)['delivered']
    with store() as db:
        assert reply_delivery.recover_sent_workflow_reply(db, original)['result']['message_id'] == 'first-provider-ack'
        ticket = db.get(KanbanTicket, ticket_id)
        if changed == 'run':
            later = AutoWorkflowRun(workflow_id=scope['workflow_id'], chat_id=scope['chat_id'],
                                    ticket_id=ticket_id, status='completed')
            db.add(later); db.flush()
            scope = {**scope, 'run_id': later.id}
        elif changed == 'ticket':
            ticket.title = 'Revised work to review'
        elif changed == 'legacy':
            row = db.query(OrchestratorEvent).one()
            payload = json.loads(row.payload)
            payload.pop('workflow')
            row.payload = json.dumps(payload)
        current = reply_delivery._delivery_envelope(db, ticket, resolve_reply_target(ticket), '')
        db.commit()
    details = {**original, 'delivery': current, 'workflow': scope}
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        request = client.post('/api/dev-approvals/request', json={
            'tool': 'reply_delivery', 'action': 'whatsapp', 'args_hash': dev_approvals.args_hash(details),
            'chat_id': scope['chat_id'], 'turn_id': scope['turn_id'],
            'summary': 'Send the closing reply?', 'details': details,
        })
        assert request.json()['status'] == 'pending', request.text
        response = client.post(f'/api/dev-approvals/{request.json()["request_id"]}/approve', json={}, headers=CLICK)
    assert response.status_code == 200, response.text
    exact = changed == 'same'
    assert response.json()['status'] == ('sent' if exact else 'rejected')
    if not exact:
        assert 'earlier delivery' in response.json()['details']['delivery_outcome']['reason']
    send.assert_called_once()
    with store() as db:
        assert db.query(OrchestratorEvent).one().status == 'sent'
        assert bool(reply_delivery.recover_sent_workflow_reply(db, details)) is exact
        outcome = json.loads(db.get(AutoWorkflowRun, scope['run_id']).run_data)['reply_delivery']
        assert outcome['delivered'] is exact
        assert outcome['delivery_status'] == ('sent' if exact else 'scope_conflict')


@pytest.mark.parametrize('lost_provider_ack', [False, True])
def test_real_closing_path_survives_delayed_review_and_app_boot_without_send_replay(store, monkeypatch, lost_provider_ack):
    from distr.core import chat_turns
    monkeypatch.setattr(chat_turns, 'get_session', store)
    monkeypatch.setattr(dispatcher, 'get_session', store)
    monkeypatch.setattr(routes, '_require_token', lambda request: None)
    connector, _ = gmail(monkeypatch, post_error=TimeoutError('Provider ACK lost') if lost_provider_ack else None)
    ticket = _ticket(store, title='Add schema migration', linked_project_id=9, source_provider='gmail',
        source_thread_id='original-thread', source_external_id='original-message', source_contact='client@example.com')
    with store() as db:
        db.get(KanbanLane, db.get(KanbanTicket, ticket).lane_id).name = 'Backlog'
        root = Chat(title='Original client task', project_id=9)
        db.add(root); db.flush()
        turn = Chat(parent_id=root.id, input='Implement the client request.')
        workflow = AutoWorkflow(name='Implementation and checks')
        db.add_all([turn, workflow]); db.flush()
        root.params = json.dumps({'active_turn_chat_row_id': turn.id})
        db.add(AutoWorkflowStep(workflow_id=workflow.id, name='Do the work', action_type='agent_instruction', instruction='Implement the request.'))
        context = {'workflow_id': workflow.id, 'chat_id': root.id, 'turn_id': turn.id}
        db.commit()
    # Use the real run creation and hold only its external worker launch.
    monkeypatch.setattr(dispatcher, '_initializing_runs', set())
    monkeypatch.setattr(dispatcher, 'record_workflow_chat_event', Mock())
    monkeypatch.setattr(dispatcher, 'append_ticket_audit_entry', Mock())
    monkeypatch.setattr('distr.core.orchestration_events.emit_orchestration_event', Mock())
    with monkeypatch.context() as launch:
        timer = Mock()
        launch.setattr(dispatcher.threading, 'Timer', timer)
        started = dispatcher.start_workflow_run(context['workflow_id'], context='Implement the client request.',
            ticket_id=ticket, run_metadata={'chat_id': context['chat_id'], 'project_id': 9}, dispatch_async=True)
        assert started['status'] == 'started'
        timer.return_value.start.assert_called_once()
        context['run_id'] = started['run_id']
    with store() as db:
        run = db.get(AutoWorkflowRun, context['run_id'])
        data = json.loads(run.run_data)
        assert data['development_turn_id'] == context['turn_id']
        run.status = 'completed'
        data['terminal_receipt'] = {'status': 'completed'}
        run.run_data = json.dumps(data)
        newer = Chat(parent_id=context['chat_id'], input='An unrelated later instruction.')
        db.add(newer); db.flush()
        later_turn_id = newer.id
        db.get(Chat, context['chat_id']).params = json.dumps({'active_turn_chat_row_id': later_turn_id})
        db.commit()
    app = FastAPI(); app.include_router(routes.router)
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        monkeypatch.setattr(dev_approvals, 'transport', lambda method, path, body:
            (lambda response: (response.status_code, response.json()))(client.request(method, path, json=body)))
        body = 'Hi,\n\nThe billing change is checked.\n\nDeployment: Still pending.\n\nThanks'
        packet = f'status: done\n\nreply_text: ```text\n{body}\n```\nnotes: Internal-only audit.'
        outcome = dispatcher._deliver_run_reply(ticket, packet,
            **{**context, 'turn_id': later_turn_id})
        assert outcome['pending_approval'] and posts(connector) == []
        rid = outcome['approval_request_id']
        with store() as db:
            request = db.query(DevApprovalRequest).one()
            event = db.query(ChatTurnEvent).filter_by(event_id=request.event_id).one()
            assert json.loads(event.metadata_json)['approval_request']['details']['delivery']['resolved_target']['to'] == ['[truncated]']
            metadata = chat_turns.get_turns(context['chat_id'])['turns'][0]['events'][0]['metadata']
            assert event.chat_id == context['chat_id'] and event.turn_id == context['turn_id']
            assert metadata['approval_request']['details']['body'] == body
            assert metadata['approval_request']['details']['delivery']['resolved_target']['to'] == ['support@example.com']
            # Crash between the durable Chat event and storing its back-link.
            request.event_id = None
            # Workflow has already ended and a new turn is active when the user returns.
            from distr.core.kanban.lifecycle import move_ticket_to_delivery_lane
            assert move_ticket_to_delivery_lane(db, ticket, 'QA')
            db.get(Chat, context['chat_id']).params = '{}'
            db.commit()
        recovered = dispatcher._deliver_run_reply(ticket, packet, **{**context, 'turn_id': None})
        assert recovered['approval_request_id'] == rid and recovered['pending_approval']
        with store() as db:
            assert db.query(ChatTurnEvent).count() == 1
            assert db.query(DevApprovalRequest).one().event_id is not None
        monkeypatch.setattr(routes, '_BOOT_KEY', b'simulated new application process')
        response = client.post(f'/api/dev-approvals/{rid}/approve', json={}, headers=CLICK)
        assert response.status_code == 200, response.text
        expected = 'unconfirmed' if lost_provider_ack else 'sent'
        assert response.json()['status'] == expected
        duplicate = client.post(f'/api/dev-approvals/{rid}/approve', json={}, headers=CLICK)
        assert duplicate.status_code == 200 and duplicate.json()['status'] == expected
        assert len(posts(connector)) == 1
        if not lost_provider_ack:
            payload = posts(connector)[0].kwargs['json']
            message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(payload['raw']))
            assert payload['threadId'] == 'original-thread'
            assert message.get_content().strip() == body
            assert message['To'] == 'support@example.com' and message['Cc'] == 'reviewer@example.com'
        repeated = dispatcher._deliver_run_reply(ticket, packet,
            **{**context, 'turn_id': None})
        assert repeated.get('approval_request_id') == rid
        assert len(posts(connector)) == 1
        with store() as db:
            assert db.query(DevApprovalRequest).count() == 1
            data = json.loads(db.get(AutoWorkflowRun, context['run_id']).run_data)
            assert data['terminal_receipt']['reply_delivery']['unconfirmed'] is lost_provider_ack
            assert db.query(OrchestratorEvent).one().status == expected
            request = db.query(DevApprovalRequest).one()
            event = db.query(ChatTurnEvent).filter_by(event_id=request.event_id).one()
            assert json.loads(event.metadata_json)['approval_request']['status'] == expected


@pytest.mark.parametrize(('receipt_missing', 'unfinished_reply', 'legacy_delivery'), [
    (False, False, None), (True, False, None),
    (False, True, None), (True, True, None),
    (False, False, 'confirmed'), (False, False, 'unconfirmed'),
    (True, False, 'confirmed'), (True, False, 'unconfirmed'),
    (True, False, 'confirmed_scoped'),
    (True, False, 'earlier_run'), (True, False, 'earlier_unscoped_run'),
    (False, False, 'failed_draft'), (False, False, 'failed_only'),
])
def test_replayed_finalization_preserves_reply_outcome(
    store, monkeypatch, receipt_missing, unfinished_reply, legacy_delivery,
):
    from distr.core.db.workflow import AutoWorkflowStepResult
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.workflow import reply_delivery

    ticket = _ticket(store)
    send = Mock(return_value={'success': True, 'message_id': 'original-ack'})
    if legacy_delivery == 'unconfirmed':
        send.side_effect = TimeoutError('Provider ACK lost')
    monkeypatch.setitem(reply_delivery.ADAPTERS, 'whatsapp', send)
    monkeypatch.setattr(dispatcher, 'get_session', store)
    for name in ('_cleanup_run', '_finalize_development_chat_turn',
                 '_maybe_auto_start_next_queued_ticket', 'append_ticket_audit_entry'):
        monkeypatch.setattr(dispatcher, name, Mock())
    monkeypatch.setattr('distr.core.orchestrator.list_validation_records', Mock(return_value=[]))
    monkeypatch.setattr('distr.core.kanban.lifecycle.move_ticket_to_delivery_lane', Mock())
    monkeypatch.setattr('distr.core.kanban.ticket_workflow_engagement.record_ticket_workflow_elapsed', Mock())
    monkeypatch.setattr('distr.core.kanban.whatsapp_work_lifecycle.notify_post_completion', Mock())
    monkeypatch.setattr('distr.core.kanban.jira_work_lifecycle.prepare_completed_jira_review', Mock(return_value=None))
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', Mock(return_value={}))
    monkeypatch.setattr('distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge', Mock())
    monkeypatch.setattr('distr.core.workflow.development_harness._start_next_queued_instruction', Mock())
    if legacy_delivery in {'earlier_run', 'earlier_unscoped_run'}:
        earlier_body = 'I checked the checkout button.\n\nInternal audit output\n```\nExample'
        with store() as db:
            workflow = AutoWorkflow(name='Earlier run')
            db.add(workflow)
            db.flush()
            earlier_run = AutoWorkflowRun(workflow_id=workflow.id, ticket_id=ticket, status='completed')
            db.add(earlier_run)
            db.flush()
            earlier_scope = {'run_id': earlier_run.id, 'workflow_id': workflow.id}
            db.commit()
        assert dispatcher._deliver_run_reply(ticket, f'reply_text: {earlier_body}', **earlier_scope)['delivered']
        if legacy_delivery == 'earlier_unscoped_run':
            with store() as db:
                event = db.query(OrchestratorEvent).filter_by(ticket_id=ticket, source='reply_delivery').one()
                payload = json.loads(event.payload)
                del payload['workflow']
                event.payload = json.dumps(payload)
                db.commit()
        send.reset_mock()
    with store() as db:
        workflow = AutoWorkflow(name='Close request')
        db.add(workflow)
        db.flush()
        step = AutoWorkflowStep(workflow_id=workflow.id, name='Reply', action_type='agent_instruction')
        run = AutoWorkflowRun(workflow_id=workflow.id, ticket_id=ticket, status='completed')
        db.add_all([step, run])
        db.flush()
        response = 'reply_text: I checked the checkout button.'
        if unfinished_reply:
            response = 'reply_text: ```text\nI checked the checkout button.'
        status = 'failed' if legacy_delivery == 'failed_only' else 'completed'
        if legacy_delivery == 'failed_draft':
            status = 'passed'
        db.add(AutoWorkflowStepResult(run_id=run.id, step_id=step.id, status=status,
                                      agent_response=response))
        db.flush()
        audit = AutoWorkflowStep(workflow_id=workflow.id, name='Audit', action_type='agent_instruction')
        db.add(audit)
        db.flush()
        db.add(AutoWorkflowStepResult(run_id=run.id, step_id=audit.id, status='completed',
                                      agent_response='Internal audit output\n```\nExample\n```'))
        if legacy_delivery == 'failed_draft':
            db.flush()
            db.add(AutoWorkflowStepResult(run_id=run.id, step_id=audit.id, status='failed',
                                          agent_response='reply_text: Unverified draft.'))
        run_id, workflow_id = run.id, workflow.id
        db.commit()

    with monkeypatch.context() as legacy:
        if legacy_delivery in {'confirmed', 'unconfirmed', 'confirmed_scoped'}:
            extract = reply_delivery.extract_reply_text
            legacy.setattr(reply_delivery, 'extract_reply_text',
                           lambda packets: extract('\n\n'.join(packets) if isinstance(packets, list) else packets))
        dispatcher._finalize_terminal_run(run_id, workflow_id, 'completed')
    with store() as db:
        run = db.get(AutoWorkflowRun, run_id)
        data = json.loads(run.run_data)
        first = data['reply_delivery']
        if receipt_missing:
            del data['reply_delivery'], data['terminal_receipt']
            run.run_data = json.dumps(data)
            if legacy_delivery in {'confirmed', 'unconfirmed'}:
                event = db.query(OrchestratorEvent).filter_by(ticket_id=ticket, source='reply_delivery').one()
                payload = json.loads(event.payload)
                del payload['workflow']
                event.payload = json.dumps(payload)
            db.commit()
    no_reply = unfinished_reply or legacy_delivery == 'failed_only'
    assert first['delivered'] is (not no_reply and legacy_delivery != 'unconfirmed')
    if legacy_delivery == 'unconfirmed':
        assert first['unconfirmed'] is True

    dispatcher._finalize_terminal_run(run_id, workflow_id, 'completed')
    if no_reply:
        send.assert_not_called()
        assert first['reason'] == 'No closing reply was returned.'
    else:
        send.assert_called_once()
        expected = 'I checked the checkout button.'
        if legacy_delivery in {'confirmed', 'unconfirmed', 'confirmed_scoped'}:
            expected += '\n\nInternal audit output\n```\nExample'
        assert send.call_args.kwargs['body'] == expected
    with store() as db:
        data = json.loads(db.get(AutoWorkflowRun, run_id).run_data)
        replayed = data['reply_delivery']
        if receipt_missing and legacy_delivery in {'confirmed', 'unconfirmed'}:
            assert replayed['delivered'] is False and replayed['delivery_status'] == 'scope_conflict'
            assert replayed['unconfirmed'] is (legacy_delivery == 'unconfirmed')
        else:
            assert replayed == first
        assert data['terminal_receipt']['reply_delivery'] == replayed
