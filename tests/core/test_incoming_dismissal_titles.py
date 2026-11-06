"""Every new dismissal stores a title/summary (WhatsApp direct + group, all dismiss paths)."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from distr.core.incoming import proposals as proposals_mod


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    from distr.core.db import IncomingProposalDismissal, IncomingProposalRestoration, engine, get_session
    from distr.core.db.migrations import ensure_incoming_dismissal_snapshot_columns

    ensure_incoming_dismissal_snapshot_columns(engine)
    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change', lambda **kw: None)
    monkeypatch.setattr('distr.core.tensology_client.decide_work_request', lambda *a, **k: {})
    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests', lambda: [])
    monkeypatch.setattr(proposals_mod, '_TRIAGE_CACHE', {'at': 0.0, 'proposals': [], 'running': False})
    monkeypatch.setattr(proposals_mod, '_RESTORED_SNAPSHOTS', {})
    proposals_mod.invalidate_proposals_list_cache()
    with get_session() as db:
        db.query(IncomingProposalDismissal).delete()
        db.query(IncomingProposalRestoration).delete()
        db.commit()
    yield
    proposals_mod.invalidate_proposals_list_cache()


def _wa(thread, name):
    return {'id': f'local:whatsapp:{thread}', 'origin': 'local', 'source': 'whatsapp',
            'dedupe_key': f'whatsapp:{thread}', 'source_thread_id': thread,
            'title': f'Fix {name}', 'summary': f'Summary {name}', 'project_id': 7,
            'message_ids': [f'{name}-m1']}


def _row(key):
    from distr.core.db import get_session
    with get_session() as db:
        return db.execute(text('SELECT title, summary, snapshot FROM incoming_proposal_dismissals WHERE key=:k'),
                          {'k': key}).fetchone()


def _client():
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.gui.web.routes.development.incoming import register_routes
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize('thread', ['78834242191494@lid', '120363429388641227@g.us'])
def test_whatsapp_dismiss_stores_title_summary_snapshot_and_survives_restart(thread):
    client = _client()
    proposal = _wa(thread, 'chat')
    res = client.post('/workflows/studio/incoming/proposals/dismiss', json={'proposal': proposal}).json()
    title, summary, snapshot = _row(res['key'])
    assert (title, summary) == ('Fix chat', 'Summary chat')
    assert json.loads(snapshot)['dedupe_key'] == f'whatsapp:{thread}'
    proposals_mod._TRIAGE_CACHE['proposals'] = []  # simulate restart: caches gone
    proposals_mod.invalidate_proposals_list_cache()
    listed = client.get('/workflows/studio/incoming/proposals/dismissed').json()['dismissed']
    assert listed[0]['title'] == 'Fix chat' and listed[0]['title_recovered'] is True
    assert listed[0]['title_stored'] is True


def test_stale_card_without_title_falls_back_to_triage_cache():
    full = _wa('stale@lid', 'cached')
    proposals_mod._TRIAGE_CACHE['proposals'] = [full]
    bare = {k: v for k, v in full.items() if k not in ('title', 'summary')}
    proposals_mod.dismiss_proposal(bare)
    title, summary, _ = _row(proposals_mod.dismissal_receipt_keys(bare)[0])
    assert (title, summary) == ('Fix cached', 'Summary cached')


@pytest.mark.parametrize('status', ['dismissed', 'sent_to_decisions', 'approved'])
@pytest.mark.parametrize('empty_title', [None, '', '   ', '<b></b>'])
def test_remote_decision_without_title_keeps_existing_title_and_snapshot(status, empty_title):
    proposal = {**_wa('remote@g.us', 'remote'), 'id': 'tensology:4242'}
    proposals_mod.dismiss_proposal(proposal)
    before = _row('tensology:4242')
    assert proposals_mod.record_remote_proposal_decision(
        {'request_id': 4242, 'status': status, 'source': 'whatsapp', 'thread_id': 'remote@g.us',
         'message_ids': ['remote-m1'], 'title': empty_title}) is True
    after = _row('tensology:4242')
    assert after[0] == 'Fix remote' and after[1] == 'Summary remote'
    assert after[2] == before[2]


def test_titleless_update_recovers_receipt_text_from_saved_snapshot():
    from distr.core.db import get_session

    proposal = {**_wa('snapshot@g.us', 'snapshot'), 'id': 'tensology:4243'}
    proposals_mod.dismiss_proposal(proposal)
    before = _row('tensology:4243')
    with get_session() as db:
        db.execute(text('UPDATE incoming_proposal_dismissals SET title=NULL, summary=NULL WHERE key=:k'),
                   {'k': 'tensology:4243'})
        db.commit()
    proposals_mod._TRIAGE_CACHE['proposals'] = []
    proposals_mod.invalidate_proposals_list_cache()
    proposals_mod.record_remote_proposal_decision(
        {'request_id': 4243, 'status': 'dismissed', 'source': 'whatsapp',
         'thread_id': 'snapshot@g.us', 'message_ids': ['snapshot-m1']})
    assert tuple(_row('tensology:4243')) == tuple(before)


def test_remote_decision_uses_event_title():
    proposals_mod.record_remote_proposal_decision(
        {'request_id': 4343, 'status': 'dismissed', 'source': 'whatsapp', 'thread_id': 'x@lid',
         'message_ids': ['x1'], 'title': 'Upstream title'})
    assert _row('tensology:4343')[0] == 'Upstream title'


def test_merge_path_stores_titles_for_both_originals(monkeypatch):
    monkeypatch.setattr('distr.core.chat.ChatService.create_new_chat', lambda **kw: (501, None))
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {})
    monkeypatch.setattr('distr.core.incoming.media.register_thread_attachments', lambda *a, **kw: None)
    monkeypatch.setattr('distr.core.tensology_notify.notify_proposal_created', lambda *a, **kw: None)
    monkeypatch.setattr(proposals_mod, '_merge_proposal_attachments', lambda *a, **kw: [])
    a, b = _wa('m@g.us', 'first'), _wa('m@g.us', 'second')
    b['message_ids'] = ['second-m1']
    result = proposals_mod.merge_proposals(a, b, complete=lambda *a: '{"title":"Combined task"}')
    assert result['chat_id'] == 501
    for p in (a, b):
        title, summary, snapshot = _row(proposals_mod.dismissal_receipt_keys(p)[0])
        assert (title, summary) == (p['title'], p['summary'])
        assert json.loads(snapshot)['title'] == p['title']


def test_telegram_dismiss_path_stores_original_title_and_source_snapshot(monkeypatch):
    from distr.core import tensology_client

    monkeypatch.setattr(tensology_client, 'fetch_pending_work_requests', lambda: [{
        'id': 4444, 'title': 'Fix Telegram-selected task', 'description': 'Original task summary',
        'source': 'whatsapp', 'source_conversation': {'thread_id': 'telegram-selected@g.us'},
        'evidence': [{'id': 'telegram-selected-m1', 'body': 'Please fix it'}],
    }])
    reply = tensology_client.handle_proposal_decision_reply('dismiss 1', source='telegram')
    assert 'Fix Telegram-selected task' in reply
    title, summary, snapshot = _row('tensology:4444')
    assert (title, summary) == ('Fix Telegram-selected task', 'Original task summary')
    assert json.loads(snapshot)['message_ids'] == ['telegram-selected-m1']


def test_title_and_summary_are_redacted_and_capped():
    proposal = {**_wa('r@lid', 'r'), 'title': 'Rotate sk-abcdefghijklmnopqrstuvwx ' + 'x' * 400,
                'summary': 'password: hunter22 ' + 'y' * 900}
    proposals_mod.dismiss_proposal(proposal)
    title, summary, _ = _row(proposals_mod.dismissal_receipt_keys(proposal)[0])
    assert 'sk-abcdefghijklmnopqrstuvwx' not in title and len(title) <= 200
    assert 'hunter22' not in summary and len(summary) <= 500
