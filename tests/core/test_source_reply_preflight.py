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


def gmail_draft(thread, monkeypatch):
    target = dict(channel='gmail', to=['client@example.com'], cc=[], subject='Re: Request',
                  in_reply_to='<original@example.com>', references='', thread_id='original',
                  **{'from': 'paul@example.com'})
    monkeypatch.setattr(replies, 'resolve_target', lambda source: target)
    return make(thread)


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
    draft = make(thread)

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
    draft = make(thread)
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
            'project_id': 9, 'source': 'whatsapp',
            'source_thread_id': 'replacement@g.us' if reason == 'source_changed' else 'fixture@g.us',
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
        relay.assert_called_once_with(jid=draft['target']['jid'], text='Exact HTTP wording.')
        assert 'dry-run mode' in response.json()['detail']
