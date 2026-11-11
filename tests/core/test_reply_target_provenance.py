"""Outbound targets must retain a verified channel and a usable destination."""
from types import SimpleNamespace

import pytest

from distr.core.workflow.reply_channel import normalize_channel, resolve_reply_target
from tests.core.test_reply_delivery import db_factory, _ticket  # isolated in-memory DB fixture


@pytest.mark.parametrize('provider', ['slack', 'database', 'manual', 'development', 'unknown'])
def test_unknown_provider_cannot_use_email_transport(provider):
    assert normalize_channel(provider) == 'email'  # Tone compatibility.
    target = resolve_reply_target(dict(source_provider=provider,
        source_thread_id='original-thread', source_contact='client@example.com'))
    assert not target.can_send
    assert 'unsupported_source_provider' in target.missing


@pytest.mark.parametrize('alias', ['gmail', 'google_mail', 'mail', 'outlook', 'email'])
def test_known_mail_alias_preserves_exact_mailbox(alias):
    target = resolve_reply_target(SimpleNamespace(source_provider=alias,
        source_thread_id='original-thread', source_contact='Client Name <client@example.com>'))
    assert target.can_send and target.channel == 'email'
    assert target.contact == 'client@example.com'


@pytest.mark.parametrize('contact', ['Paul', 'Paul Smith', 'a@', 'bad@@example.com',
    'a@example.com,b@example.com', 'a@example.com, Name <b@example.com>',
    'a@example.com junk', 'mailto:a@example.com',
    'a@example.com\r\nBcc: other@example.com', 'Name <a@example.com> trailing'])
@pytest.mark.parametrize('provider', ['gmail', 'mailshot'])
def test_mail_sender_label_or_malformed_destination_is_not_sendable(provider, contact):
    target = resolve_reply_target(dict(source_provider=provider,
        source_thread_id='original-thread', source_contact=contact))
    assert not target.can_send and target.contact == ''
    assert 'source_contact' in target.missing


@pytest.mark.parametrize('origin', [
    {'whatsapp_message_wa_id': '3EB0A_PROVIDER_MESSAGE_ID'},
    {'source_external_id': '3EB0A_PROVIDER_MESSAGE_ID'},
    {'external_id': '3EB0A_PROVIDER_MESSAGE_ID'},
    {'source_url': 'https://wa.me/27634103646'},
    {'source_contact': 'Paul'},
    {'source_thread_id': '   ', 'whatsapp_message_wa_id': '3EB0A_PROVIDER_MESSAGE_ID'},
])
def test_whatsapp_message_identity_or_display_metadata_cannot_replace_conversation(origin):
    target = resolve_reply_target(dict(source_provider='whatsapp', **origin))
    assert not target.can_send and target.thread_id == ''
    assert 'source_thread_id' in target.missing


@pytest.mark.parametrize('provider', ['whatsapp', 'whats_app', 'wa'])
@pytest.mark.parametrize('thread', ['27634103646', '27634103646@s.whatsapp.net',
    '120363000000@g.us', '123456789@lid'])
def test_canonical_whatsapp_conversation_wins_over_message_id(provider, thread):
    target = resolve_reply_target(dict(source_provider=provider, source_thread_id=thread,
        whatsapp_message_wa_id='UNRELATED_MESSAGE_ID', source_contact='Client'))
    assert target.can_send and target.channel == 'whatsapp'
    assert target.thread_id == thread


@pytest.mark.parametrize('provider,contact', [('telegram', ''), ('mailshot', 'client@example.com')])
def test_known_channels_keep_valid_provenance_without_claiming_adapter_support(provider, contact):
    target = resolve_reply_target(dict(source_provider=provider,
        source_thread_id='original-thread', source_contact=contact))
    assert target.can_send and target.channel == provider


def test_legacy_external_provider_alias_still_selects_whatsapp_conversation():
    target = resolve_reply_target(dict(external_source='wa', source_thread_id='120363@g.us'))
    assert target.can_send and target.thread_id == '120363@g.us'


@pytest.mark.parametrize('origin', [
    dict(source_provider='slack', source_thread_id='thread', source_contact='client@example.com'),
    dict(source_provider='whatsapp', source_thread_id='', whatsapp_message_wa_id='MESSAGE_ID'),
    dict(source_provider='gmail', source_thread_id='gmail-thread', source_contact='Client Name'),
])
def test_delivery_boundary_refuses_unusable_origin_before_transport(db_factory, monkeypatch, origin):
    from unittest.mock import Mock
    from distr.core.workflow import reply_delivery
    transport = Mock(side_effect=AssertionError('Invalid provenance reached a transport'))
    monkeypatch.setitem(reply_delivery.ADAPTERS, 'email', transport)
    monkeypatch.setitem(reply_delivery.ADAPTERS, 'whatsapp', transport)
    ticket_id = _ticket(db_factory, **origin)
    result = reply_delivery.deliver_ticket_reply(ticket_id, 'Fixed and checked.', force=True)
    assert not result['delivered'] and result['reason'] == 'incomplete provenance'
    transport.assert_not_called()
