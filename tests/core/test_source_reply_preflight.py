"""No-send failures stay reviewable; uncertain provider writes stay protected."""
import json
from unittest.mock import Mock

import pytest

from distr.core.db import Chat, SourceReplyDraft, get_session
from distr.core.incoming import source_reply as replies
from distr.core.incoming.source_reply import resolve_target as resolve_provider_target
from tests.core.test_source_reply import thread, make


def stored(draft):
    with get_session() as db:
        row = db.get(SourceReplyDraft, draft['id'])
        return {**json.loads(row.payload), 'status': row.status}


def make_gmail_draft(thread):
    replies.register_reply_context(thread[0], {
        'project_id': 9, 'source': 'gmail', 'source_thread_id': 'original', 'message_ids': ['mail'],
    })
    return replies.create_draft(*thread, 'gmail:original', complete=lambda *_: 'Ready to review.')


def gmail_draft(thread, monkeypatch):
    target = dict(channel='gmail', to=['client@example.com'], cc=[], subject='Re: Request',
                  in_reply_to='<original@example.com>', references='', thread_id='original',
                  **{'from': 'paul@example.com'})
    monkeypatch.setattr(replies, 'resolve_target', lambda source: target)
    return make_gmail_draft(thread)


def connector(monkeypatch, *, profile='other@example.com', get_error=None, post_error=None):
    from distr.core.agent.services.integrations import google_workspace
    def request(method, *args, **kwargs):
        if method == 'GET':
            if get_error:
                raise get_error
            return {'sendAs': [{'sendAsEmail': profile, 'isPrimary': True}]}
        if post_error:
            raise post_error
        return {'id': 'confirmed-message', 'threadId': 'original'}
    client = Mock()
    client._make_request.side_effect = request
    monkeypatch.setattr(google_workspace, 'GoogleWorkspaceConnector', lambda: client)
    return client


@pytest.mark.parametrize('failure', ['account', 'profile-timeout', 'header', 'missing-thread', 'missing-reference'])
def test_definite_gmail_preflight_failure_keeps_reviewed_draft_without_post(thread, monkeypatch, failure):
    draft = gmail_draft(thread, monkeypatch)
    client = connector(monkeypatch, profile='other@example.com' if failure == 'account' else 'paul@example.com',
                       get_error=TimeoutError() if failure == 'profile-timeout' else None)
    if failure in {'header', 'missing-thread', 'missing-reference'}:
        with get_session() as db:
            row = db.get(SourceReplyDraft, draft['id'])
            payload = json.loads(row.payload)
            if failure == 'header':
                payload['target']['subject'] = 'Bad\nHeader'
            elif failure == 'missing-thread':
                payload['target'].pop('thread_id')
            else:
                payload['target']['in_reply_to'] = ''
            row.payload = json.dumps(payload)
            db.commit()
    with pytest.raises(replies.ReplyPreflightError, match='Nothing was sent'):
        replies.act_on_draft(thread[0], draft['id'], 'approve', 'My exact reviewed wording.')
    assert not any(call.args[0] == 'POST' for call in client._make_request.call_args_list)
    assert stored(draft)['status'] == 'draft'
    assert stored(draft)['text'] == 'My exact reviewed wording.'


def test_restore_account_then_deliberate_approval_sends_once(thread, monkeypatch):
    draft = gmail_draft(thread, monkeypatch)
    connector(monkeypatch)
    with pytest.raises(replies.ReplyPreflightError):
        replies.act_on_draft(thread[0], draft['id'], 'approve', 'Reviewed.')
    client = connector(monkeypatch, profile='paul@example.com')
    assert replies.act_on_draft(thread[0], draft['id'], 'approve', 'Reviewed.')['status'] == 'sent'
    assert replies.act_on_draft(thread[0], draft['id'], 'approve', 'Duplicate.')['already_sent']
    assert [call.args[0] for call in client._make_request.call_args_list] == ['GET', 'POST']


@pytest.mark.parametrize("change", ["board", "response", "source", "token_refresh"])
def test_gmail_does_not_post_if_development_changes_during_provider_preflight(thread, monkeypatch, change):
    from distr.core.agent.services.integrations import google_workspace

    draft = gmail_draft(thread, monkeypatch)
    real_request = google_workspace.GoogleWorkspaceConnector._make_request
    client = connector(monkeypatch, profile="paul@example.com")
    request = client._make_request.side_effect
    provider_post = Mock()
    monkeypatch.setattr(google_workspace.requests, "request", provider_post)

    def move():
        if change == "source":
            replies.register_reply_context(thread[0], {
                "project_id": 9, "source": "gmail", "source_thread_id": "replacement", "message_ids": ["new"],
            })
        else:
            with get_session() as db:
                if change == "response":
                    db.get(Chat, thread[1]).response = "Changed while checking Gmail."
                else:
                    root = db.get(Chat, thread[0])
                    root.params = json.dumps({**json.loads(root.params), "development": {"board_key": "moved"}})
                db.commit()
        return True

    def request_with_change(method, *args, **kwargs):
        if method == "POST":
            return real_request(client, method, *args, **kwargs)
        if change != "token_refresh":
            move()
        return request(method, *args, **kwargs)

    client.access_token = "fixture-token"
    client._ensure_valid_token.side_effect = move if change == "token_refresh" else lambda: True
    client._make_request.side_effect = request_with_change
    with pytest.raises(replies.ReplyPreflightError, match="Nothing was sent"):
        replies.act_on_draft(thread[0], draft["id"], "approve", "Keep the edited reply.")
    provider_post.assert_not_called()
    assert stored(draft)["status"] == "draft"
    assert stored(draft)["text"] == "Keep the edited reply."


def test_whatsapp_scope_is_rechecked_after_relay_authentication(thread, monkeypatch):
    from distr.core.integrations.whatsapp import relay_client

    draft = make(thread)
    post = Mock()
    monkeypatch.setattr(relay_client, "whatsapp_send_dry_run_enabled", lambda: False)
    monkeypatch.setattr(relay_client, "relay_api_base", lambda: "https://relay.example.test")
    monkeypatch.setattr(relay_client.requests, "post", post)

    def authenticate():
        with get_session() as db:
            root = db.get(Chat, thread[0])
            root.params = json.dumps({**json.loads(root.params), "development": {"board_key": "moved"}})
            db.commit()
        return {}

    monkeypatch.setattr(relay_client, "relay_request_headers", authenticate)
    with pytest.raises(replies.ReplyRewriteRequiredError, match="Nothing was sent"):
        replies.act_on_draft(thread[0], draft["id"], "approve", "Keep the edited reply.")
    post.assert_not_called()
    assert stored(draft)["status"] == "draft"
    assert stored(draft)["text"] == "Keep the edited reply."


@pytest.mark.parametrize('change', ['recipient', 'alias', 'sender', 'subject', 'reference', 'thread', 'unavailable', 'development_scope'])
def test_mailshot_rechecks_the_reviewed_envelope_before_post(thread, monkeypatch, change):
    client = Mock()
    senders = [{'email': 'paul@example.com', 'primary': True, 'can_send': True}]
    original = {'id': 'mail', 'thread_id': 'original', 'subject': 'Request', 'messageId': '<request@example.com>',
                'from': 'client@example.com', 'to': 'paul@example.com', 'cc': 'orders@example.com'}

    def get(path):
        if path == 'mail/senders':
            return senders
        assert path == 'mail/messages/mail'
        return {'email': original} if original else {}

    client.get.side_effect = get
    client.post.return_value = {'id': 'provider-confirmation'}
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **_: client)
    monkeypatch.setattr(replies, 'resolve_target', resolve_provider_target)
    replies.register_reply_context(thread[0], {
        'project_id': 9, 'source': 'mailshot', 'source_thread_id': 'original', 'message_ids': ['mail'],
    })
    draft = replies.create_draft(*thread, 'mailshot:original', complete=lambda *_: 'Ready for review.')
    if change == 'alias':
        senders.append({'email': 'orders@example.com', 'primary': False})
    elif change == 'sender':
        senders[0]['email'] = 'other@example.com'
    elif change == 'unavailable':
        original.clear()
    elif change == 'development_scope':
        def move_during_preflight(path):
            if path == 'mail/messages/mail':
                with get_session() as db:
                    root = db.get(Chat, thread[0])
                    root.params = json.dumps({**json.loads(root.params), "development": {"board_key": "moved"}})
                    db.commit()
            return get(path)

        client.get.side_effect = move_during_preflight
    else:
        field, value = {
            'recipient': ('reply_to', 'new@example.com'), 'subject': ('subject', 'Revised request'),
            'reference': ('messageId', '<different@example.com>'), 'thread': ('thread_id', 'different'),
        }[change]
        original[field] = value

    expected = replies.ReplyPreflightError if change == 'unavailable' else replies.ReplyRewriteRequiredError
    with pytest.raises(expected, match='Nothing was sent'):
        replies.act_on_draft(thread[0], draft['id'], 'approve', 'My reviewed wording.')
    client.post.assert_not_called()
    assert stored(draft)['status'] == 'draft'
    assert stored(draft)['text'] == 'My reviewed wording.'
    assert stored(draft).get('rewrite_required', False) is (change != 'unavailable')
    if change == 'recipient':
        revised = replies.create_draft(
            *thread, 'mailshot:original', previous='My reviewed wording.', replaces=draft['id'],
            complete=lambda *_: 'My reviewed wording.',
        )
        assert revised['target']['to'] == ['new@example.com']
        client.post.assert_not_called()
        assert replies.act_on_draft(thread[0], revised['id'], 'approve', revised['text'])['status'] == 'sent'
        assert replies.act_on_draft(thread[0], revised['id'], 'approve', 'Duplicate.')['already_sent']
        client.post.assert_called_once()


@pytest.mark.parametrize('alias_was_ours', [False, True])
def test_alias_change_after_review_requires_rewrite_without_post(thread, monkeypatch, alias_was_ours):
    from distr.core.agent.services.integrations import google_workspace

    state = {'alias_is_ours': alias_was_ours}
    client = Mock()
    def request(method, url, **kwargs):
        if method == 'POST':
            return {'id': 'confirmed-message'}
        return {'sendAs': [
            {'sendAsEmail': 'paul@example.com', 'isPrimary': True},
            {'sendAsEmail': 'orders@example.com', 'treatAsAlias': state['alias_is_ours'],
             'verificationStatus': 'accepted'},
        ]}
    client._make_request.side_effect = request
    client.get_email.return_value = {'id': 'mail', 'threadId': 'original',
        'from': 'client@example.com', 'to': 'paul@example.com', 'cc': 'orders@example.com'}
    monkeypatch.setattr(google_workspace, 'GoogleWorkspaceConnector', lambda: client)
    target = resolve_provider_target({'channel': 'gmail', 'thread_id': 'original', 'message_ids': ['mail']})
    monkeypatch.setattr(replies, 'resolve_target', lambda source: target)
    draft = make_gmail_draft(thread)

    state['alias_is_ours'] = not alias_was_ours
    with pytest.raises(replies.ReplyRewriteRequiredError, match='recipients changed'):
        replies.act_on_draft(thread[0], draft['id'], 'approve', 'Keep these exact edits.')
    assert not any(call.args[0] == 'POST' for call in client._make_request.call_args_list)
    assert stored(draft)['status'] == 'draft'
    assert stored(draft)['rewrite_required'] is True
    assert stored(draft)['text'] == 'Keep these exact edits.'
    assert replies.reply_drafts(*thread)[0]['rewrite_required'] is True


def test_second_window_approval_returns_actual_sent_wording(thread):
    draft = make(thread)
    sender = Mock()
    replies.act_on_draft(thread[0], draft['id'], 'approve', 'First window sent wording.', sender=sender)
    result = replies.act_on_draft(thread[0], draft['id'], 'approve', 'Second window unsent wording.', sender=sender)
    assert result['already_sent'] and result['text'] == 'First window sent wording.'
    assert stored(draft)['text'] == 'First window sent wording.'
    sender.assert_called_once_with(draft['target'], 'First window sent wording.', draft['id'])


def test_source_removed_in_same_project_blocks_approval_and_preserves_edits(thread):
    draft = make(thread)
    replies.register_reply_context(thread[0], {
        'project_id': 9, 'source': 'whatsapp', 'source_thread_id': 'replacement@g.us',
        'message_ids': ['replacement'],
    })
    sender = Mock()
    with pytest.raises(replies.ReplyPreflightError, match='source changed'):
        replies.act_on_draft(thread[0], draft['id'], 'approve', 'My newest edits.', sender=sender)
    sender.assert_not_called()
    assert stored(draft)['status'] == 'draft'
    assert stored(draft)['text'] == 'My newest edits.'
    fresh = replies.create_draft(*thread, 'whatsapp:replacement@g.us', complete=lambda *args: 'New review.')
    assert fresh['target']['jid'] == 'replacement@g.us'


def test_source_removed_during_generation_cannot_save_stale_draft(thread):
    def complete(*args):
        replies.register_reply_context(thread[0], {
            'project_id': 9, 'source': 'whatsapp', 'source_thread_id': 'replacement@g.us',
            'message_ids': ['replacement'],
        })
        return 'Generated from the former source.'

    with pytest.raises(ValueError, match='source changed while drafting'):
        replies.create_draft(*thread, 'whatsapp:fixture@g.us', complete=complete)
    assert replies.reply_drafts(*thread) == []


@pytest.mark.parametrize('retain_original', [False, True])
@pytest.mark.parametrize('legacy_draft', [False, True])
def test_same_conversation_reply_requires_original_message_scope(thread, retain_original, legacy_draft):
    draft = make(thread)
    if legacy_draft:
        with get_session() as db:
            row = db.get(SourceReplyDraft, draft['id'])
            payload = json.loads(row.payload)
            payload['target']['source_message_ids'] = payload.pop('source_message_ids')
            payload.pop('primary_message_ids')
            row.payload = json.dumps(payload)
            db.commit()
    ids = ['voice', 'follow-up'] if retain_original else ['follow-up']
    replies.register_reply_context(thread[0], {
        'project_id': 9, 'source': 'whatsapp', 'source_thread_id': 'fixture@g.us',
        'message_ids': ids,
    })
    sender = Mock()
    if retain_original:
        assert replies.act_on_draft(thread[0], draft['id'], 'approve', 'Reviewed wording.', sender=sender)['status'] == 'sent'
        sender.assert_called_once()
    else:
        with pytest.raises(replies.ReplyPreflightError, match='source messages changed'):
            replies.act_on_draft(thread[0], draft['id'], 'approve', 'Reviewed wording.', sender=sender)
        sender.assert_not_called()
        assert stored(draft)['status'] == 'draft'
        assert stored(draft)['text'] == 'Reviewed wording.'
        assert replies.reply_drafts(*thread)[0]['rewrite_required'] is True
        replacement = replies.create_draft(*thread, draft['source_key'], replaces=draft['id'],
                                           complete=lambda *args: 'Updated reply.')
        assert replies.act_on_draft(thread[0], replacement['id'], 'approve', 'Updated reply.', sender=sender)['status'] == 'sent'
        sender.assert_called_once()


def test_reply_rewrite_required_when_original_request_is_only_background(thread):
    draft = make(thread)
    replies.register_reply_context(thread[0], {
        'project_id': 9, 'source': 'whatsapp', 'source_thread_id': 'fixture@g.us',
        'message_ids': ['new-request'], 'source_context_message_ids': ['voice'],
    })
    sender = Mock()
    with pytest.raises(replies.ReplyPreflightError, match='source messages changed'):
        replies.act_on_draft(thread[0], draft['id'], 'approve', 'Reviewed wording.', sender=sender)
    sender.assert_not_called()


def test_refresh_removing_last_reply_source_invalidates_retained_draft(thread, monkeypatch):
    from distr.gui.web.routes import chat as chat_routes
    from tests.core.test_chat_development_surface_isolation import _client

    # Use the same store as the thread fixture even if another test first
    # imported the route while its isolated session factory was patched in.
    monkeypatch.setattr(chat_routes, 'get_session', get_session)
    draft = make(thread)
    client = _client()
    path = f'/api/chats/{thread[0]}'
    assert client.get(path).json()['has_reply_source'] is True
    replies.register_reply_context(thread[0], {
        'project_id': 9, 'source': 'whatsapp', 'source_thread_id': 'fixture@g.us',
        'message_ids': [], 'source_context_message_ids': ['voice'],
    })
    assert replies.source_choices(replies.proposal_for_thread(thread[0])) == []
    assert client.get(path).json()['has_reply_source'] is False
    sender = Mock()
    with pytest.raises(replies.ReplySourceChangedError, match='no longer has a reply source'):
        replies.act_on_draft(thread[0], draft['id'], 'approve', 'Keep my wording.', sender=sender)
    sender.assert_not_called()
    assert stored(draft)['text'] == 'Keep my wording.'


def test_development_transcript_includes_only_durable_sent_receipts_for_its_responses(thread, monkeypatch):
    from distr.gui.web.routes import chat as chat_routes
    from tests.core.test_chat_development_surface_isolation import _client

    monkeypatch.setattr(chat_routes, "get_session", get_session)
    draft = make(thread)
    with get_session() as db:
        root = db.get(Chat, thread[0])
        params = json.loads(root.params)
        root.params = json.dumps({**params, "development": {"source_type": "proposal"}})
        newer = Chat(parent_id=thread[0], response="A second response is ready.")
        db.add(newer)
        db.flush()
        newer_id = newer.id
        db.commit()
    pending = make((thread[0], newer_id))
    with get_session() as db:
        db.get(SourceReplyDraft, pending["id"]).status = "unconfirmed"
        db.commit()
    sender = Mock()
    assert replies.act_on_draft(thread[0], draft["id"], "approve", "Reviewed.", sender=sender)["status"] == "sent"
    with get_session() as db:
        db.add(SourceReplyDraft(id="malformed-fixture", chat_id=thread[0], status="sent", payload="{invalid"))
        db.commit()

    response = _client().get(f"/api/chats/{thread[0]}?surface=development")
    assert response.status_code == 200
    messages = [row for row in response.json()["messages"] if row["role"] == "assistant"]
    assert {row["chat_row_id"] for row in messages if row.get("source_reply_sent")} == {thread[1]}
    assert any(row["chat_row_id"] == newer_id and not row.get("source_reply_sent") for row in messages)
    sender.assert_called_once()


def test_proactive_failure_is_visible_but_cannot_supply_a_source_reply(thread, monkeypatch):
    from distr.core.orchestrator_proactive import _add_proactive_turn_event, PROACTIVE_RESULT_HELD
    from distr.gui.web.routes import chat as chat_routes
    from tests.core.test_chat_development_surface_isolation import _client

    chat_id, turn_id = thread
    monkeypatch.setattr(chat_routes, "get_session", get_session)
    with get_session() as db:
        root = db.get(Chat, chat_id)
        root.params = json.dumps({**json.loads(root.params), "development": {"source_type": "proposal"}})
        db.get(Chat, turn_id).response = PROACTIVE_RESULT_HELD
        db.commit()
    old_draft = make(thread)
    with get_session() as db:
        _add_proactive_turn_event(db, chat_id, turn_id, "turn_failed", "failed", PROACTIVE_RESULT_HELD)
        db.commit()

    transcript = _client().get(f"/api/chats/{chat_id}?surface=development").json()
    message = next(row for row in transcript["messages"] if row["role"] == "assistant")
    assert message["content"] == PROACTIVE_RESULT_HELD
    assert message["source_reply_eligible"] is False
    model = Mock()
    with pytest.raises(ValueError, match="Choose a response"):
        replies.create_draft(*thread, "whatsapp:fixture@g.us", complete=model)
    model.assert_not_called()
    sender = Mock()
    with pytest.raises(replies.ReplyRewriteRequiredError, match="unavailable"):
        replies.act_on_draft(chat_id, old_draft["id"], "approve", "Keep this edit.", sender=sender)
    sender.assert_not_called()
    assert stored(old_draft)["text"] == "Keep this edit."


def test_development_thread_reports_only_unresolved_attachment_names(thread, monkeypatch, tmp_path):
    from pathlib import Path
    from distr.core.incoming.media import _record
    from distr.gui.web.routes import chat as chat_routes
    from tests.core.test_chat_development_surface_isolation import _client

    monkeypatch.setattr(chat_routes, 'get_session', get_session)
    monkeypatch.setattr('distr.core.incoming.media.get_session', get_session)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    file = tmp_path / '.decisions/workspaces/projects/9/recovered.txt'
    file.parent.mkdir(parents=True)
    file.write_text('Original bytes')
    verified = _record(file, 'recovered.txt', 'text/plain', 'recovery fixture')
    delivered = file.with_name('delivered.txt')
    delivered.write_text('Delivered bytes')
    registered = _record(delivered, 'delivered.txt', 'text/plain', 'delivered fixture')
    with get_session() as db:
        root = db.get(Chat, thread[0])
        params = json.loads(root.params)
        root.params = json.dumps({
            **params, 'development': {'source_type': 'proposal'},
            'incoming_attachments': [verified, registered],
            'incoming_attachment_failures': [
                {'name': 'recovered.txt', 'sha256': verified['sha256'], 'error': 'Original provider error'},
                {'name': 'missing.pdf', 'path': '/internal/missing.pdf', 'error': 'Provider response'},
                None,
            ],
        })
        db.commit()
    api = _client()
    endpoint = f'/api/chats/{thread[0]}?surface=development'
    assert api.get(endpoint).json()['unavailable_attachment_names'] == ['missing.pdf']
    delivered.unlink()
    assert api.get(endpoint).json()['unavailable_attachment_names'] == ['missing.pdf', 'delivered.txt']
    file.write_text('Changed bytes!')
    assert api.get(endpoint).json()['unavailable_attachment_names'] == ['recovered.txt', 'missing.pdf', 'delivered.txt']
    delivered.write_text('Changed content')
    assert api.get(endpoint).json()['unavailable_attachment_names'] == ['recovered.txt', 'missing.pdf', 'delivered.txt']
    delivered.write_text('Delivered bytes')
    assert api.get(endpoint).json()['unavailable_attachment_names'] == ['recovered.txt', 'missing.pdf']


def test_development_thread_reports_telegram_delivery_without_exposing_the_receipt(thread, monkeypatch):
    from distr.core.incoming.telegram_updates import record_delivery
    from distr.gui.web.routes import chat as chat_routes
    from tests.core.test_chat_development_surface_isolation import _client

    monkeypatch.setattr(chat_routes, 'get_session', get_session)
    monkeypatch.setattr('distr.core.incoming.telegram_updates.get_session', get_session)
    api = _client()
    endpoint = f'/api/chats/{thread[0]}?surface=development'
    catalog = '/api/chats?include_archived=true&surface=development'
    key, token, recipient = 'job:done', f'proposal-update:{thread[0]}:private-token', 9712345
    pending = {'key': key, 'text': 'Private proposal result', 'offset': 0}

    def save(receipt, *, retained=False):
        with get_session() as db:
            root = db.get(Chat, thread[0])
            params = json.loads(root.params)
            params['development'] = {'source_type': 'proposal'}
            params['telegram_proposal_update'] = receipt
            if retained:
                params['incoming_proposal_handoff']['telegram_approval'] = {
                    'job_id': 'new-job', 'chat_id': recipient, 'outcome': {'stage': 'done', 'text': 'Private future result'},
                }
            else:
                params['incoming_proposal_handoff'].pop('telegram_approval', None)
            root.params = json.dumps(params)
            db.commit()

    cases = [
        ({'recipient_id': recipient, 'pending': pending}, 'queued'),
        ({'recipient_id': recipient, 'pending': {**pending, 'relay_retry_at': 100}}, 'retrying'),
        ({'recipient_id': recipient, 'pending': {**pending, 'awaiting': {
            'token': token, 'end': 10, 'state': 'sending',
        }}}, 'sending'),
        ({'recipient_id': recipient, 'pending': {**pending, 'awaiting': {
            'token': token, 'end': 10, 'state': 'unconfirmed', 'error': 'Internal relay details',
        }}}, 'unconfirmed'),
        ({'pending': pending}, 'held'),
        ({'recipient_id': recipient, 'sent': key}, 'sent'),
        ({'pending': ['Malformed receipt']}, 'unavailable'),
        ({}, None),
    ]
    for receipt, state in cases:
        save(receipt)
        response = api.get(endpoint)
        assert response.status_code == 200
        status = response.json()['telegram_proposal_update_status']
        listed = api.get(catalog)
        assert listed.status_code == 200
        summary = next(chat for chat in listed.json()['chats'] if chat['id'] == thread[0])
        assert summary['telegram_proposal_update_status'] == status
        if state is None:
            assert status is None
        else:
            assert status['state'] == state
            assert set(status) == {'state', 'message'}
            assert 'Telegram' in status['message']
        for private in (token, str(recipient), 'Private proposal result', 'Internal relay details', key):
            assert private not in response.text
            assert private not in listed.text
    save({'recipient_id': recipient, 'sent': key}, retained=True)
    assert api.get(endpoint).json()['telegram_proposal_update_status']['state'] == 'queued'
    save({'recipient_id': recipient, 'pending': {**pending, 'awaiting': {
        'token': token, 'end': len(pending['text']), 'state': 'unconfirmed',
    }}})
    assert record_delivery(token, telegram_message_id=71, telegram_chat_id=recipient, flush=False)
    assert not record_delivery(token, telegram_message_id=71, telegram_chat_id=recipient, flush=False)
    assert api.get(endpoint).json()['telegram_proposal_update_status']['state'] == 'sent'
    summary = next(chat for chat in api.get(catalog).json()['chats'] if chat['id'] == thread[0])
    assert summary['telegram_proposal_update_status']['state'] == 'sent'


def test_concurrent_approval_calls_transport_once(thread):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    draft = make(thread)
    entered, release = Event(), Event()

    def transport(*args):
        entered.set()
        assert release.wait(timeout=5)

    sender = Mock(side_effect=transport)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(replies.act_on_draft, thread[0], draft['id'], 'approve', 'First review.', sender=sender)
        try:
            assert entered.wait(timeout=5)
            second = pool.submit(replies.act_on_draft, thread[0], draft['id'], 'approve', 'Second review.', sender=sender)
            with pytest.raises(ValueError, match='sending'):
                second.result(timeout=5)
        finally:
            release.set()
        assert first.result(timeout=5)['status'] == 'sent'

    sender.assert_called_once_with(draft['target'], 'First review.', draft['id'])
    assert stored(draft)['text'] == 'First review.'


@pytest.mark.parametrize('field,new_value', [('subject', 'Changed subject'), ('message_id_header', '<changed@example.com>')])
def test_gmail_original_thread_headers_are_rechecked_before_post(thread, monkeypatch, field, new_value):
    client = connector(monkeypatch, profile='paul@example.com')
    original = {'id': 'mail', 'threadId': 'original', 'subject': 'Request',
                'from': 'client@example.com', 'to': 'paul@example.com',
                'message_id_header': '<original@example.com>'}
    client.get_email.return_value = original
    target = resolve_provider_target({'channel': 'gmail', 'thread_id': 'original', 'message_ids': ['mail']})
    monkeypatch.setattr(replies, 'resolve_target', lambda _: target)
    draft = make_gmail_draft(thread)
    original[field] = new_value

    with pytest.raises(replies.ReplyRewriteRequiredError, match='subject or reply reference changed'):
        replies.act_on_draft(thread[0], draft['id'], 'approve', 'My exact reviewed wording.')
    assert not any(call.args[0] == 'POST' for call in client._make_request.call_args_list)
    assert stored(draft)['status'] == 'draft'
    assert stored(draft)['rewrite_required'] is True
    assert stored(draft)['text'] == 'My exact reviewed wording.'


@pytest.mark.parametrize('returned_thread', ['different-thread', None])
def test_gmail_post_receipt_must_confirm_the_reviewed_thread(thread, monkeypatch, returned_thread):
    draft = gmail_draft(thread, monkeypatch)
    client = connector(monkeypatch, profile='paul@example.com')
    previous_request = client._make_request.side_effect
    client._make_request.side_effect = lambda method, *args, **kwargs: (
        {'id': 'provider-message', 'threadId': returned_thread}
        if method == 'POST' else previous_request(method, *args, **kwargs)
    )
    with pytest.raises(RuntimeError, match='not confirmed'):
        replies.act_on_draft(thread[0], draft['id'], 'approve', 'Exact approved text.')
    assert stored(draft)['status'] == 'unconfirmed'
    assert stored(draft)['text'] == 'Exact approved text.'
    with pytest.raises(ValueError, match='unconfirmed'):
        replies.act_on_draft(thread[0], draft['id'], 'approve', 'Retry.')
    assert [call.args[0] for call in client._make_request.call_args_list] == ['GET', 'POST']


def test_provider_post_timeout_still_prevents_resend(thread, monkeypatch):
    draft = gmail_draft(thread, monkeypatch)
    client = connector(monkeypatch, profile='paul@example.com', post_error=TimeoutError())
    with pytest.raises(RuntimeError, match='not confirmed'):
        replies.act_on_draft(thread[0], draft['id'], 'approve', 'Reviewed.')
    assert stored(draft)['status'] == 'unconfirmed'
    with pytest.raises(ValueError, match='unconfirmed'):
        replies.act_on_draft(thread[0], draft['id'], 'approve', 'Retry.')
    assert [call.args[0] for call in client._make_request.call_args_list] == ['GET', 'POST']


def test_whatsapp_dry_run_keeps_exact_text_for_explicit_approval(thread, monkeypatch):
    from distr.core.integrations.whatsapp import relay_client

    draft = make(thread)
    relay = Mock(side_effect=[{'success': True, 'dry_run': True}, {'success': True}])
    monkeypatch.setattr(relay_client, 'send_message_via_relay', relay)
    with pytest.raises(replies.ReplyPreflightError, match='Nothing was sent.*dry-run mode'):
        replies.act_on_draft(thread[0], draft['id'], 'approve', 'Exact approved text.')
    assert stored(draft)['status'] == 'draft'
    assert stored(draft)['text'] == 'Exact approved text.'
    assert replies.act_on_draft(thread[0], draft['id'], 'approve', 'Explicitly reapproved.')['status'] == 'sent'
    assert [call.kwargs['text'] for call in relay.call_args_list] == ['Exact approved text.', 'Explicitly reapproved.']


@pytest.mark.parametrize('outcome', ['sent', 'draft', 'unconfirmed'])
def test_transient_receipt_write_failure_retries_only_the_local_update(thread, outcome):
    from sqlite3 import OperationalError as SQLiteOperationalError
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    draft = make(thread)
    with get_session() as db:
        engine = db.get_bind()
    failed = False

    def reject_first_receipt_write(_connection, _cursor, statement, parameters, *_):
        nonlocal failed
        if not failed and statement.lstrip().upper().startswith('UPDATE SOURCE_REPLY_DRAFTS'):
            failed = True
            raise OperationalError(statement, parameters, SQLiteOperationalError('database is locked'))

    def provider(*_args):
        event.listen(engine, 'before_cursor_execute', reject_first_receipt_write)
        if outcome == 'draft':
            raise replies.ReplyPreflightError('Nothing was sent.')
        if outcome == 'unconfirmed':
            raise TimeoutError()
        return {'id': 'provider-confirmed'}

    sender = Mock(side_effect=provider)
    try:
        if outcome == 'draft':
            with pytest.raises(replies.ReplyPreflightError, match='Nothing was sent'):
                replies.act_on_draft(thread[0], draft['id'], 'approve', 'Exact wording.', sender=sender)
        elif outcome == 'unconfirmed':
            with pytest.raises(RuntimeError, match='not confirmed'):
                replies.act_on_draft(thread[0], draft['id'], 'approve', 'Exact wording.', sender=sender)
        else:
            assert replies.act_on_draft(thread[0], draft['id'], 'approve', 'Exact wording.', sender=sender)['status'] == 'sent'
    finally:
        event.remove(engine, 'before_cursor_execute', reject_first_receipt_write)
    assert failed
    assert stored(draft)['status'] == outcome
    assert stored(draft)['text'] == 'Exact wording.'
    sender.assert_called_once_with(draft['target'], 'Exact wording.', draft['id'])


def test_project_change_requires_new_review_and_preserves_edited_text(thread):
    draft = make(thread)
    assert draft['project_id'] == 9
    with get_session() as db:
        db.get(Chat, thread[0]).project_id = 10
        db.commit()
    sender = Mock()
    with pytest.raises(replies.ReplyPreflightError, match='project changed'):
        replies.act_on_draft(thread[0], draft['id'], 'approve', 'My newest edits.', sender=sender)
    sender.assert_not_called()
    assert stored(draft)['text'] == 'My newest edits.'
    fresh = make(thread, replaces=draft['id'], previous='My newest edits.')
    assert fresh['project_id'] == 10 and fresh['id'] != draft['id']
    assert replies.act_on_draft(thread[0], fresh['id'], 'approve', 'Newly reviewed.', sender=sender)['status'] == 'sent'


def test_project_change_during_generation_does_not_save_wrong_scope(thread):
    def complete(*args):
        with get_session() as db:
            db.get(Chat, thread[0]).project_id = 10
            db.commit()
        return 'Generated under old scope.'
    with pytest.raises(ValueError, match='project changed while drafting'):
        replies.create_draft(*thread, 'whatsapp:fixture@g.us', complete=complete)
    assert replies.reply_drafts(*thread) == []


@pytest.mark.parametrize("phase", ["after_draft", "during_draft"])
def test_same_project_board_change_requires_fresh_reply_review(thread, phase):
    def move():
        with get_session() as db:
            root = db.get(Chat, thread[0])
            params = json.loads(root.params)
            params["development"] = {"board_key": "another-board", "board_provider": "tensology",
                                     "board_ticket_key": "another-ticket"}
            root.params = json.dumps(params)
            db.commit()

    if phase == "during_draft":
        def complete(*_):
            move()
            return "Old board draft."

        with pytest.raises(ValueError, match="board or ticket changed while drafting"):
            replies.create_draft(*thread, "whatsapp:fixture@g.us", complete=complete)
        assert replies.reply_drafts(*thread) == []
        return

    draft = make(thread)
    move()
    [review] = replies.reply_drafts(*thread)
    assert review["rewrite_required"] and "board or ticket" in review["rewrite_notice"]
    sender = Mock()
    with pytest.raises(replies.ReplyRewriteRequiredError, match="board or ticket"):
        replies.act_on_draft(thread[0], draft["id"], "approve", "Preserve these edits.", sender=sender)
    sender.assert_not_called()
    assert stored(draft)["text"] == "Preserve these edits."
    rewritten = make(thread, replaces=draft["id"], previous="Preserve these edits.")
    assert replies.act_on_draft(thread[0], rewritten["id"], "approve", "Reviewed again.", sender=sender)["status"] == "sent"
    sender.assert_called_once()


def test_reply_review_tracks_actual_ticket_board_but_allows_normal_lane_moves(thread):
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket

    with get_session() as db:
        first = KanbanBoard(name="Reply board", default_project_id=9)
        second = KanbanBoard(name="Replacement board", default_project_id=9)
        initial_lane = KanbanLane(name="In progress", board=first)
        next_lane = KanbanLane(name="QA", board=first)
        other_lane = KanbanLane(name="QA", board=second)
        ticket = KanbanTicket(title="Proposal request", lane=initial_lane, source_chat_id=thread[0])
        db.add_all([first, second, initial_lane, next_lane, other_lane, ticket])
        db.flush()
        board_ids, ticket_id = (first.id, second.id), ticket.id
        lane_ids = (next_lane.id, other_lane.id)
        root = db.get(Chat, thread[0])
        root.params = json.dumps({**json.loads(root.params), "development": {"ticket_id": ticket_id}})
        db.commit()
    try:
        draft = make(thread)
        with get_session() as db:
            db.get(KanbanTicket, ticket_id).lane_id = lane_ids[0]
            db.commit()
        assert not replies.reply_drafts(*thread)[0].get("rewrite_required")
        with get_session() as db:
            db.get(KanbanTicket, ticket_id).lane_id = lane_ids[1]
            db.commit()
        sender = Mock()
        with pytest.raises(replies.ReplyRewriteRequiredError, match="board or ticket"):
            replies.act_on_draft(thread[0], draft["id"], "approve", "Reviewed.", sender=sender)
        sender.assert_not_called()
    finally:
        with get_session() as db:
            for board_id in board_ids:
                db.delete(db.get(KanbanBoard, board_id))
            db.commit()


def test_project_change_still_allows_cancelling_old_review(thread):
    draft = make(thread)
    with get_session() as db:
        db.get(Chat, thread[0]).project_id = 10
        db.commit()
    sender = Mock()
    assert replies.act_on_draft(thread[0], draft['id'], 'cancel', sender=sender)['status'] == 'cancelled'
    sender.assert_not_called()


@pytest.mark.parametrize('project', [9, 10])
def test_legacy_draft_requires_retained_project_match(thread, project):
    draft = make(thread)
    with get_session() as db:
        row = db.get(SourceReplyDraft, draft['id'])
        payload = json.loads(row.payload)
        payload.pop('project_id')
        row.payload = json.dumps(payload)
        db.get(Chat, thread[0]).project_id = project
        db.commit()
    sender = Mock()
    if project == 9:
        assert replies.act_on_draft(thread[0], draft['id'], 'approve', 'Reviewed.', sender=sender)['status'] == 'sent'
    else:
        with pytest.raises(replies.ReplyPreflightError):
            replies.act_on_draft(thread[0], draft['id'], 'approve', 'Reviewed.', sender=sender)
        sender.assert_not_called()


@pytest.mark.parametrize('reason', [None, 'source_changed', 'rewrite_required', 'dry_run'])
def test_http_preflight_contract_matches_persisted_state(thread, monkeypatch, reason):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.gui.web.routes.development.thread_tools import register_routes
    draft = make(thread) if reason == 'dry_run' else gmail_draft(thread, monkeypatch)
    if reason in {'source_changed', 'rewrite_required'}:
        replies.register_reply_context(thread[0], {
            'project_id': 9, 'source': 'gmail',
            'source_thread_id': 'replacement' if reason == 'source_changed' else 'original',
            'message_ids': ['replacement'],
        })
    transport = connector(monkeypatch)
    if reason == 'dry_run':
        from distr.core.integrations.whatsapp import relay_client
        relay = Mock(return_value={'success': True, 'dry_run': True})
        monkeypatch.setattr(relay_client, 'send_message_via_relay', relay)
    router = APIRouter()
    register_routes(router, None)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post(f'/workflows/studio/tasks/{thread[0]}/source-replies/{draft["id"]}/approve',
                               json={'approved': True, 'text': 'Exact HTTP wording.'})
    assert response.status_code == 409
    expected = {'detail': '', 'code': 'reply_not_sent', 'send_attempted': False, 'draft_status': 'draft'}
    if reason in {'source_changed', 'rewrite_required'}:
        expected['reason'] = reason
    assert response.json() | {'detail': ''} == expected
    assert stored(draft)['status'] == 'draft' and stored(draft)['text'] == 'Exact HTTP wording.'
    assert [call.args[0] for call in transport._make_request.call_args_list] == ([] if reason else ['GET'])
    if reason == 'dry_run':
        relay.assert_called_once()
        assert relay.call_args.kwargs['jid'] == draft['target']['jid']
        assert relay.call_args.kwargs['text'] == 'Exact HTTP wording.'
        assert 'dry-run mode' in response.json()['detail']
