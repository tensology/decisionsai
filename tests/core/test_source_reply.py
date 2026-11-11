import json
from unittest.mock import Mock

import pytest

from distr.core.db import Chat, SourceReplyDraft, get_session
from distr.core.incoming import source_reply as replies


@pytest.fixture
def thread(monkeypatch):
    proposal = {'source': 'whatsapp', 'project_id': 9, 'source_thread_id': 'fixture@g.us',
                'message_ids': ['voice'], 'participant': 'Client group',
                'evidence': [{'id': 'voice', 'body': 'Please bill by bytes.', 'transcript': 'Full voice transcript.'}]}
    with get_session() as db:
        root = Chat(title='Reply fixture', project_id=9, params=json.dumps({'incoming_proposal_handoff': {'proposal': proposal}}))
        db.add(root); db.flush()
        response = Chat(parent_id=root.id, response='I implemented per-byte billing. Deployment has not happened.')
        db.add(response); db.commit()
        ids = root.id, response.id
    monkeypatch.setattr(replies, 'resolve_target', lambda source: {'channel': 'whatsapp', 'jid': source['thread_id'], 'label': source['label']})
    yield ids
    with get_session() as db:
        db.query(SourceReplyDraft).filter(SourceReplyDraft.chat_id == ids[0]).delete()
        for cid in reversed(ids):
            row = db.get(Chat, cid)
            if row: db.delete(row)
        db.commit()


def make(thread, **kwargs):
    return replies.create_draft(*thread, 'whatsapp:fixture@g.us', complete=lambda *args: 'I have made the change. It still needs deploying.', **kwargs)


def test_approval_sends_exact_edited_text_once_and_survives_reload(thread):
    sender = Mock()
    draft = make(thread)
    assert draft['status'] == 'draft'
    sender.assert_not_called()
    result = replies.act_on_draft(thread[0], draft['id'], 'approve', 'My exact edited reply.', sender=sender)
    assert result['status'] == 'sent'
    # Engine state updates must not erase a send claim and permit a duplicate.
    with get_session() as db:
        db.get(Chat, thread[0]).params = '{}'
        db.commit()
    sender.assert_called_once_with(draft['target'], 'My exact edited reply.', draft['id'])
    assert replies.act_on_draft(thread[0], draft['id'], 'approve', 'Another text', sender=sender)['already_sent']
    assert sender.call_count == 1


def test_cancel_and_rewrite_never_send_and_invalidate_old_approval(thread):
    sender = Mock()
    old = make(thread)
    new = make(thread, previous='My edited text', replaces=old['id'], instruction='Shorter')
    with pytest.raises(ValueError):
        replies.act_on_draft(thread[0], old['id'], 'approve', old['text'], sender=sender)
    replies.act_on_draft(thread[0], new['id'], 'cancel', sender=sender)
    with pytest.raises(ValueError):
        replies.act_on_draft(thread[0], new['id'], 'approve', new['text'], sender=sender)
    sender.assert_not_called()


@pytest.mark.parametrize("change", ["response", "restored_summary", "deleted", "legacy"])
def test_approval_requires_review_again_if_the_result_cannot_be_verified(thread, monkeypatch, change):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.gui.web.routes.development.thread_tools import register_routes

    if change == "restored_summary":
        with get_session() as db:
            db.get(Chat, thread[1]).response = "pending approval automation_recommendation"
            root = db.get(Chat, thread[0])
            params = json.loads(root.params)
            params["development"] = {"execution": {"summary": "The fix is ready for deployment."}}
            root.params = json.dumps(params)
            db.commit()
    draft = make(thread)
    with get_session() as db:
        response = db.get(Chat, thread[1])
        if change == "response":
            response.response = "Verification failed. Deployment is on hold."
        elif change == "restored_summary":
            root = db.get(Chat, thread[0])
            params = json.loads(root.params)
            params["development"]["execution"]["summary"] = "Verification failed. Deployment is on hold."
            root.params = json.dumps(params)
        elif change == "deleted":
            db.delete(response)
        else:
            row = db.get(SourceReplyDraft, draft["id"])
            payload = json.loads(row.payload)
            payload.pop("response_revision", None)
            row.payload = json.dumps(payload)
        db.commit()

    sender = Mock()
    monkeypatch.setattr(replies, "send_reply", sender)
    before_approval = replies.reply_drafts(*thread)[0]
    assert before_approval["rewrite_required"] and before_approval["rewrite_notice"]
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    result = TestClient(app).post(
        f'/workflows/studio/tasks/{thread[0]}/source-replies/{draft["id"]}/approve',
        json={"approved": True, "text": "Keep my exact edits."},
    )
    assert result.status_code == 409
    assert result.json()["reason"] == "rewrite_required"
    assert result.json()["send_attempted"] is False
    sender.assert_not_called()
    saved = replies.reply_drafts(*thread)[0]
    assert saved["text"] == "Keep my exact edits."
    assert saved["status"] == "draft" and saved["rewrite_required"]
    if change != "deleted":
        seen = []
        rewritten = replies.create_draft(*thread, "whatsapp:fixture@g.us", previous=saved["text"],
                                         replaces=saved["id"],
                                         complete=lambda p, m, messages, s: (seen.extend(messages) or "A fresh reply."))
        assert rewritten["id"] != saved["id"] and not rewritten.get("rewrite_required")
        if change in {"response", "restored_summary"}:
            assert "Verification failed" in json.dumps(seen)


def test_result_change_while_drafting_rejects_stale_model_output(thread):
    def complete(*_args):
        with get_session() as db:
            db.get(Chat, thread[1]).response = "The change failed verification."
            db.commit()
        return "The change is ready."

    with pytest.raises(ValueError, match="response changed while drafting"):
        replies.create_draft(*thread, "whatsapp:fixture@g.us", complete=complete)
    assert replies.reply_drafts(*thread) == []


def test_uncertain_send_cannot_be_retried_blindly(thread):
    draft = make(thread)
    sender = Mock(side_effect=TimeoutError())
    with pytest.raises(RuntimeError, match='not confirmed'):
        replies.act_on_draft(thread[0], draft['id'], 'approve', 'Approved.', sender=sender)
    with pytest.raises(ValueError, match='unconfirmed'):
        replies.act_on_draft(thread[0], draft['id'], 'approve', 'Approved.', sender=sender)
    assert sender.call_count == 1
    with get_session() as db:
        stored = db.get(SourceReplyDraft, draft['id'])
        saved = {**json.loads(stored.payload), 'status': stored.status}
        assert saved['text'] == 'Approved.' and saved['status'] == 'unconfirmed'


@pytest.mark.parametrize('state', ['sending', 'unconfirmed', 'sent'])
def test_reopen_resumes_protected_send_and_preserves_exact_text_without_model(thread, monkeypatch, state):
    draft = make(thread)
    with get_session() as db:
        row = db.get(SourceReplyDraft, draft['id'])
        payload = json.loads(row.payload)
        payload['text'] = 'My exact reviewed message.'
        row.payload = json.dumps(payload)
        row.status = state
        db.commit()
    model = Mock(side_effect=AssertionError('Must resume without another model call'))
    resumed = replies.create_draft(*thread, 'whatsapp:fixture@g.us', complete=model)
    assert resumed['id'] == draft['id']
    assert resumed['status'] == state
    assert resumed['text'] == 'My exact reviewed message.'
    assert replies.reply_drafts(*thread) == [resumed]
    assert replies.create_draft(*thread, 'whatsapp:fixture@g.us', replaces=draft['id'], complete=model)['id'] == draft['id']
    model.assert_not_called()


def test_two_concurrent_generations_share_one_durable_send_identity(thread, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier = Barrier(2)
    def generate(*args, **kwargs):
        barrier.wait(timeout=5)
        return 'Text', {}
    monkeypatch.setattr(replies, 'generate_draft', generate)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(replies.create_draft, *thread, 'whatsapp:fixture@g.us') for _ in range(2)]
        drafts = [future.result(timeout=10) for future in futures]
    assert drafts[0]['id'] == drafts[1]['id']
    assert len(replies.reply_drafts(*thread)) == 1


def test_legacy_duplicate_draft_cannot_hide_unconfirmed_send(thread):
    protected = make(thread)
    with get_session() as db:
        db.get(SourceReplyDraft, protected['id']).status = 'unconfirmed'
        duplicate = {**protected, 'id': 'legacy-later-draft'}
        db.add(SourceReplyDraft(id=duplicate['id'], chat_id=thread[0], payload=json.dumps(duplicate), status='draft'))
        db.commit()
    assert replies.create_draft(*thread, 'whatsapp:fixture@g.us')['status'] == 'unconfirmed'
    assert replies.reply_drafts(*thread)[0]['id'] == protected['id']
    sender = Mock()
    with pytest.raises(ValueError, match='already sending'):
        replies.act_on_draft(thread[0], duplicate['id'], 'approve', 'Duplicate.', sender=sender)
    sender.assert_not_called()


def test_reject_foreign_response_source_and_draft(thread):
    with pytest.raises(ValueError, match='original sources'):
        replies.create_draft(*thread, 'whatsapp:foreign@g.us')
    with pytest.raises(ValueError, match='response'):
        replies.create_draft(thread[0], 99999999, 'whatsapp:fixture@g.us')
    with pytest.raises(ValueError, match='unavailable'):
        replies.act_on_draft(thread[0], 'foreign', 'approve', 'Text')
    draft = make(thread)
    with pytest.raises(ValueError, match='required'):
        replies.act_on_draft(thread[0], draft['id'], 'approve', ' ')


def test_reply_all_deduplicates_self_honors_reply_to_and_keeps_cc():
    target = replies.mail_target('gmail', {'id': 'm', 'from': 'Original <original@example.com>',
        'reply_to': 'Support <support@example.com>', 'to': 'Paul <paul@example.com>, Friend <friend@example.com>',
        'cc': 'friend@example.com, reviewer@example.com, paul@example.com', 'subject': 'Billing',
        'message_id_header': '<rfc@example.com>', 'threadId': 'thread'}, ['paul@example.com'])
    assert target['to'] == ['support@example.com', 'friend@example.com']
    assert target['cc'] == ['reviewer@example.com']
    assert target['in_reply_to'] == '<rfc@example.com>'
    assert target['subject'] == 'Re: Billing'
    assert target['recipient_names'] == ['Support', 'Friend']
    with pytest.raises(ValueError, match='identity'):
        replies.mail_target('mailshot', {'from': 'client@example.com'}, [])


def test_merge_keeps_separate_choices_and_no_invented_destination():
    choices = replies.source_choices({'source': 'whatsapp', 'source_scope': [
        {'source': 'whatsapp', 'source_thread_id': 'a@g.us', 'message_ids': ['a']},
        {'source': 'gmail', 'source_thread_id': 'mail-thread', 'message_ids': ['b']},
        {'source': 'whatsapp', 'source_thread_id': 'a@g.us', 'message_ids': ['a']}]})
    assert [source['key'] for source in choices] == ['whatsapp:a@g.us', 'gmail:mail-thread']
    assert choices[0]['message_ids'] == ['a'] and choices[1]['message_ids'] == ['b']
    assert replies.source_choices({'source': 'gmail'}) == []


def test_canonical_merge_resolves_real_sources_not_synthetic_collection():
    choices = replies.source_choices({'source': 'whatsapp', 'source_thread_id': 'merged:collection',
        'message_ids': ['merged-message:1', 'merged-message:2'], 'evidence': [
            {'id': 'merged-message:1', 'original_source': {'channel': 'whatsapp', 'source_thread_id': 'group@g.us', 'source_message_id': 'voice'}},
            {'id': 'merged-message:2', 'original_source': {'channel': 'mailshot', 'source_thread_id': 'email-thread', 'source_message_id': 'email'}}]})
    assert [(row['key'], row['message_ids']) for row in choices] == [
        ('whatsapp:group@g.us', ['voice']), ('mailshot:email-thread', ['email'])]


def test_merged_background_conversation_is_not_offered_as_a_reply_source():
    proposal = {
        'source': 'gmail', 'source_thread_id': 'merged:collection',
        'evidence': [{'original_source': {
            'channel': 'gmail', 'source_thread_id': 'request-thread', 'source_message_id': 'request',
        }}],
        'context_evidence': [{'original_source': {
            'channel': 'gmail', 'source_thread_id': 'background-thread', 'source_message_id': 'background',
        }}],
    }
    assert [choice['key'] for choice in replies.source_choices(proposal)] == ['gmail:request-thread']


def test_whatsapp_transport_uses_exact_group_and_detects_dry_run(monkeypatch):
    from distr.core.integrations.whatsapp import relay_client
    send = Mock(return_value={'success': True})
    monkeypatch.setattr(relay_client, 'send_message_via_relay', send)
    replies.send_reply({'channel': 'whatsapp', 'jid': 'source-group@g.us'}, 'Exact approved text.', 'key')
    send.assert_called_once_with(jid='source-group@g.us', text='Exact approved text.')
    send.return_value = {'success': True, 'dry_run': True}
    with pytest.raises(replies.ReplyPreflightError, match='dry-run mode'):
        replies.send_reply({'channel': 'whatsapp', 'jid': 'source-group@g.us'}, 'Text', 'key')


def test_mailshot_transport_reply_all_threads_without_appending_signature(monkeypatch):
    from distr.core import tensology_client
    client = Mock()
    client.post.return_value = {'_id': 'sent-id'}
    monkeypatch.setattr(tensology_client, 'configured_tensology_client', lambda **kwargs: client)
    target = {**replies.mail_target('mailshot', {'id': 'mail', 'messageId': '<original@example.com>',
        'from': 'client@example.com', 'to': 'paul@example.com', 'cc': 'reviewer@example.com'}, ['paul@example.com']),
        'from': 'paul@example.com'}
    replies.send_reply(target, 'Exact approved text.', 'approval-key')
    args, kwargs = client.post.call_args
    assert args[0] == 'mail/send'
    assert args[1]['to'] == ['client@example.com'] and args[1]['cc'] == ['reviewer@example.com']
    assert args[1]['body'] == 'Exact approved text.'
    assert args[1]['inReplyTo'] == '<original@example.com>'
    assert args[1]['include_signature'] is False
    assert kwargs == {'approved': True, 'idempotency_key': 'approval-key'}
    client.post.return_value = {'error': 'rejected'}
    with pytest.raises(RuntimeError, match='did not confirm'):
        replies.send_reply(target, 'Exact approved text.', 'approval-key')


def test_gmail_preview_uses_exact_thread_and_keeps_relevant_context(monkeypatch):
    from distr.core.agent.services.integrations import google_workspace
    connector = Mock()
    connector._make_request.return_value = {'sendAs': [
            {'sendAsEmail': 'paul@example.com', 'isPrimary': True},
            {'sendAsEmail': 'orders@example.com', 'treatAsAlias': True, 'verificationStatus': 'accepted'},
            {'sendAsEmail': 'separate@example.com', 'treatAsAlias': False, 'verificationStatus': 'accepted'},
            {'sendAsEmail': 'pending@example.com', 'treatAsAlias': True, 'verificationStatus': 'pending'},
    ]}
    connector.get_email.side_effect = lambda mid: {'id': mid, 'threadId': 'exact-thread',
        'from': 'client@example.com', 'to': 'paul@example.com, Orders <ORDERS@example.com>',
        'cc': 'orders@example.com, reviewer@example.com, separate@example.com, pending@example.com',
        'message_id_header': '<source@example.com>'}
    monkeypatch.setattr(google_workspace, 'GoogleWorkspaceConnector', lambda: connector)
    target = replies.resolve_target({'channel': 'gmail', 'thread_id': 'exact-thread',
        'message_ids': ['earlier', 'latest'], 'scoped_ids': True})
    assert target['source_message_ids'] == ['earlier', 'latest']
    assert target['to'] == ['client@example.com']
    assert target['cc'] == ['reviewer@example.com', 'separate@example.com', 'pending@example.com']
    assert 'Orders' not in target['recipient_names']
    connector.get_email.return_value = None
    connector.get_email.side_effect = lambda mid: {'threadId': 'foreign-thread'}
    with pytest.raises(ValueError, match='verified'):
        replies.resolve_target({'channel': 'gmail', 'thread_id': 'exact-thread', 'message_ids': ['foreign']})


def test_gmail_preview_anchors_reply_all_to_request_and_keeps_background_context(monkeypatch):
    from distr.core.agent.services.integrations import google_workspace

    connector = Mock()
    connector._make_request.return_value = {'sendAs': [{'sendAsEmail': 'paul@example.com', 'isPrimary': True}]}
    connector.get_email.side_effect = lambda mid: {
        'id': mid, 'threadId': 'exact-thread', 'from': f'{mid}@example.com',
        'to': 'paul@example.com', 'message_id_header': f'<{mid}@example.com>',
    }
    monkeypatch.setattr(google_workspace, 'GoogleWorkspaceConnector', lambda: connector)
    [choice] = replies.source_choices({
        'source': 'gmail', 'source_thread_id': 'exact-thread', 'message_ids': ['request'],
        'source_context_message_ids': ['background'],
    })

    target = replies.resolve_target(choice)

    assert target['to'] == ['request@example.com']
    assert target['source_message_ids'] == ['request', 'background']
    connector.get_email.assert_called_once_with('request')


def test_mailshot_preview_uses_verified_senders_and_original_email(monkeypatch):
    from distr.core import tensology_client
    client = Mock()
    client.get.side_effect = lambda path: ([{'email': 'paul@example.com', 'primary': True, 'can_send': True}]
        if path == 'mail/senders' else {'_id': 'original', 'from': 'client@example.com', 'to': 'paul@example.com',
          'cc': 'reviewer@example.com', 'messageId': '<original@example.com>'})
    monkeypatch.setattr(tensology_client, 'configured_tensology_client', lambda **kwargs: client)
    target = replies.resolve_target({'channel': 'mailshot', 'thread_id': 'original', 'message_ids': ['original']})
    assert target['from'] == 'paul@example.com'
    assert target['to'] == ['client@example.com'] and target['cc'] == ['reviewer@example.com']
    assert target['in_reply_to'] == '<original@example.com>'


def test_http_requires_explicit_approval_and_never_sends_on_preview(monkeypatch):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.gui.web.routes.development.thread_tools import register_routes
    app = FastAPI(); router = APIRouter(); register_routes(router, None); app.include_router(router)
    act = Mock()
    monkeypatch.setattr(replies, 'act_on_draft', act)
    result = TestClient(app).post('/workflows/studio/tasks/1/source-replies/key/approve', json={'text': 'No approval'})
    assert result.status_code == 409
    act.assert_not_called()


def test_drafting_uses_the_same_restored_result_the_user_sees(thread):
    with get_session() as db:
        row = db.get(Chat, thread[1]); row.response = 'pending approval automation_recommendation'
        root = db.get(Chat, thread[0]); params = json.loads(root.params)
        params['development'] = {'execution': {'summary': 'The fix passed verification. Deployment is pending.'}}
        root.params = json.dumps(params); db.commit()
    seen = []
    replies.create_draft(*thread, 'whatsapp:fixture@g.us', complete=lambda p, m, messages, s: (seen.extend(messages) or 'The fix is ready to deploy.'))
    assert 'The fix passed verification. Deployment is pending.' in json.dumps(seen)
    assert 'automation_recommendation' not in json.dumps(seen)


def test_draft_prompt_contains_transcript_selected_response_and_humanizer_rules(thread):
    seen = []
    def complete(provider, model, messages, settings):
        seen.extend(messages)
        return 'Changed\u2014pending deployment.'
    draft = replies.create_draft(*thread, 'whatsapp:fixture@g.us', complete=complete)
    prompt = json.dumps(seen)
    assert 'Full voice transcript.' in prompt
    assert 'Deployment has not happened.' in prompt
    assert 'Never invent facts' in prompt and 'Humanizer' in prompt
    assert draft['text'] == 'Changed,pending deployment.'


@pytest.mark.parametrize('channel', ['whatsapp', 'gmail', 'mailshot'])
def test_draft_greets_relevant_people_before_a_plain_language_update(monkeypatch, channel):
    from datetime import datetime
    from distr.core import read_response, settings
    monkeypatch.setattr(settings, 'load_settings_from_db', lambda: {})
    monkeypatch.setattr(read_response, 'resolve_oracle_model', lambda settings: ('fixture', 'fixture'))
    seen = []
    text, _ = replies.generate_draft(
        {'evidence': [{'id': 'current', 'sender_name': 'Greg', 'body': 'Can you change the billing?'},
                      {'id': 'unrelated', 'sender_name': 'Someone Else', 'body': 'Another project.'}]},
        {'channel': channel, 'label': 'Project group', 'message_ids': ['current'], 'recipient_names': ['Greg']},
        'The billing calculation has changed. Deployment is pending.',
        previous='A technical draft.', instruction='Make it clearer.',
        complete=lambda p, m, messages, s: (seen.extend(messages) or 'Hi Greg,\n\nI have changed how the usage charge is calculated. It is not live yet.'))
    rules = seen[0]['content']
    context = json.loads(seen[1]['content'])
    assert 'Start with a friendly greeting' in rules
    assert 'Hi when the recipients\' local time is unknown' in rules
    assert 'Do not greet Paul' in rules and 'invent a name' in rules
    assert 'everyday language' in rules and 'test counts' in rules
    assert 'Do not claim completion' in rules
    assert context['recipient_names'] == ['Greg']
    assert context['source_messages'][0]['sender_name'] == 'Greg'
    assert len(context['source_messages']) == 1
    assert datetime.fromisoformat(context['current_local_time']).tzinfo is not None
    assert context['rewrite_request'] == 'Make it clearer.'
    assert text.startswith('Hi Greg,')


def test_email_greeting_names_are_from_actual_recipients_only():
    target = replies.mail_target('gmail', {
        'from': 'Old Sender <old@example.com>', 'reply_to': 'Greg <greg@example.com>',
        'to': [{'name': 'Paul', 'email': 'paul@example.com'}, {'name': 'Jane', 'email': 'jane@example.com'}],
        'cc': 'Jane <jane@example.com>, unnamed@example.com'}, ['paul@example.com'])
    assert target['recipient_names'] == ['Greg', 'Jane']
    assert 'unnamed@example.com' in target['cc']


def test_create_draft_keeps_verified_names_for_greeting(thread, monkeypatch):
    monkeypatch.setattr(replies, 'resolve_target', lambda source: {
        'channel': 'whatsapp', 'jid': source['thread_id'], 'recipient_names': ['Greg', 'Jane']})
    seen = []
    replies.create_draft(*thread, 'whatsapp:fixture@g.us',
        complete=lambda p, m, messages, s: (seen.extend(messages) or 'Hi Greg and Jane,\n\nThe change is ready to put live.'))
    assert json.loads(seen[1]['content'])['recipient_names'] == ['Greg', 'Jane']


def test_gmail_transport_keeps_approved_to_cc_and_rfc_thread_headers(monkeypatch):
    import base64
    from email import message_from_bytes
    from distr.core.agent.services.integrations import google_workspace
    connector = Mock()
    connector._make_request.side_effect = [
        {'sendAs': [{'sendAsEmail': 'paul@example.com', 'isPrimary': True}]},
        {'id': 'sent', 'threadId': 'original-thread'},
    ]
    monkeypatch.setattr(google_workspace, 'GoogleWorkspaceConnector', lambda: connector)
    original = {'id': 'm', 'from': 'client@example.com', 'to': 'paul@example.com',
        'cc': 'reviewer@example.com', 'subject': 'Work', 'message_id_header': '<original@example.com>',
        'threadId': 'original-thread'}
    connector.get_email.return_value = original
    target = {**replies.mail_target('gmail', original, ['paul@example.com']), 'from': 'paul@example.com'}
    replies.send_reply(target, 'Exact approved message.', 'key')
    payload = connector._make_request.call_args.kwargs['json']
    message = message_from_bytes(base64.urlsafe_b64decode(payload['raw']))
    assert payload['threadId'] == 'original-thread'
    assert message['From'] == 'paul@example.com'
    assert message['To'] == 'client@example.com' and message['Cc'] == 'reviewer@example.com'
    assert message['In-Reply-To'] == '<original@example.com>'
    assert message.get_payload().strip() == 'Exact approved message.'


def test_merged_reply_choice_keeps_all_original_ids_for_the_same_conversation():
    choices = replies.source_choices({
        'source': 'gmail', 'source_thread_id': 'merged:collection', 'message_ids': ['synthetic'],
        'source_scope': [
            {'source': 'gmail', 'source_thread_id': 'one', 'message_ids': ['first'], 'participant': 'Original sender'},
            {'source': 'gmail', 'source_thread_id': 'two', 'message_ids': ['separate']},
            {'source': 'gmail', 'source_thread_id': 'one', 'message_ids': ['second'], 'source_context_message_ids': ['first', 'context']},
        ],
    })
    assert [(source['key'], source['message_ids']) for source in choices] == [
        ('gmail:one', ['first', 'second', 'context']), ('gmail:two', ['separate'])]
    assert choices[0]['label'] == 'Original sender'
    assert choices[0]['scoped_ids'] is True


def test_synthetic_merge_without_original_provenance_is_not_a_reply_destination():
    assert replies.source_choices({'source': 'gmail', 'source_thread_id': 'merged:unknown',
                                   'message_ids': ['merged-message:1']}) == []
