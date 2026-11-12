"""WhatsApp send requires a real relay acceptance and never invents an ACK."""
from unittest.mock import Mock

import pytest

from distr.core.integrations.whatsapp import relay_client as relay
from tests.core.test_reply_delivery import db_factory, _ticket

@pytest.fixture(autouse=True)
def _ask_first_off(monkeypatch):
    """These tests cover the transport/ledger with 'Ask before messaging clients' switched off.
    Ask-first holding is covered in tests/initiative/test_initiative_fixes_20261112.py."""
    monkeypatch.setattr('distr.core.initiative.outcome_flow.ask_before_client_messages', lambda settings=None: False)


@pytest.fixture
def post(monkeypatch):
    transport = Mock()
    monkeypatch.setattr(relay.requests, 'post', transport)
    monkeypatch.setattr(relay, 'relay_api_base', lambda: 'http://fixture.invalid')
    monkeypatch.setattr(relay, 'relay_request_headers', lambda: {'fixture': 'relay'})
    monkeypatch.setattr(relay, 'whatsapp_send_dry_run_enabled', lambda: False)
    return transport


@pytest.mark.parametrize('status,body', [
    (200, ValueError('HTML proxy page')),
    (200, {}),
    (200, None),
    (200, ['success']),
    (200, {'success': 'true'}),
    (200, {'success': 1}),
    (200, {'success': False, 'error': 'Relay rejected message'}),
    (200, {'success': True, 'error': 'Conflicting failure'}),
    (503, {'success': True}),
])
def test_non_ack_response_is_not_success_and_does_not_retry(post, status, body):
    response = Mock(status_code=status, text='<html>Unexpected proxy response</html>')
    if isinstance(body, Exception):
        response.json.side_effect = body
    else:
        response.json.return_value = body
    post.return_value = response
    result = relay.send_message_via_relay(jid='fixture@g.us', text='Exact reviewed text')
    assert result['success'] is False and result['status_code'] == status
    assert result['error'] == (body.get('error') if isinstance(body, dict) and body.get('error')
        else 'WhatsApp relay did not acknowledge the send. Check the original conversation before retrying.')
    post.assert_called_once()


@pytest.mark.parametrize('receipt', [
    {'success': True},  # Current relay contract: Baileys handoff accepted.
    {'success': True, 'jid': 'canonical@g.us', 'message_id': 'fixture-provider-id',
     'state': 'submitted', 'receipt': {'id': 'fixture-receipt'}},
])
def test_explicit_acceptance_preserves_the_relay_receipt(post, receipt):
    response = Mock(status_code=200)
    response.json.return_value = receipt
    post.return_value = response
    result = relay.send_message_via_relay(jid='fixture@g.us', text='Exact reviewed text', caption='Caption')
    assert result == {'jid': 'fixture@g.us', **receipt}
    post.assert_called_once_with('http://fixture.invalid/send', json={
        'jid': 'fixture@g.us', 'text': 'Exact reviewed text', 'caption': 'Caption', 'audio': None},
        headers={'fixture': 'relay'}, timeout=10)


def test_lost_http_ack_does_not_retry(post):
    post.side_effect = relay.requests.exceptions.Timeout('Relay response lost')
    assert relay.send_message_via_relay(jid='fixture@g.us', text='Reviewed.')['success'] is False
    post.assert_called_once()


def test_dry_run_is_flagged_and_never_contacts_relay(post, monkeypatch):
    monkeypatch.setattr(relay, 'whatsapp_send_dry_run_enabled', lambda: True)
    result = relay.send_message_via_relay(jid='fixture@g.us', text='Reviewed.')
    assert result['dry_run'] is True
    post.assert_not_called()


def test_workflow_keeps_ambiguous_relay_response_unconfirmed(post, db_factory):
    from distr.core.workflow import reply_delivery
    ticket_id = _ticket(db_factory)
    response = Mock(status_code=200, text='<html>Proxy page</html>')
    response.json.side_effect = ValueError('HTML proxy page')
    post.return_value = response
    first = reply_delivery.deliver_ticket_reply(ticket_id, 'Fixed and checked.')
    second = reply_delivery.deliver_ticket_reply(ticket_id, 'Fixed and checked.')
    assert not first['delivered'] and first['unconfirmed']
    assert second['duplicate'] and second['delivery_status'] == 'unconfirmed'
    post.assert_called_once()
