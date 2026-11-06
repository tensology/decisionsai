"""Undo v2: dismissal snapshots, restore into the triage cache, forced re-triage, migration."""

from __future__ import annotations

import json
import time

import pytest
from sqlalchemy import create_engine, text

from distr.core.incoming import proposals as proposals_mod


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    from distr.core.db import IncomingProposalDismissal, IncomingProposalRestoration, engine, get_session
    from distr.core.db.migrations import ensure_incoming_dismissal_snapshot_columns

    ensure_incoming_dismissal_snapshot_columns(engine)
    events = []
    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change', lambda **kw: events.append(kw))
    monkeypatch.setattr('distr.core.tensology_client.decide_work_request', lambda *a, **k: {})
    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests', lambda: [])
    monkeypatch.setattr(proposals_mod, '_TRIAGE_CACHE',
                        {'at': 0.0, 'proposals': [], 'running': False})
    monkeypatch.setattr(proposals_mod, '_RESTORED_SNAPSHOTS', {})
    proposals_mod.invalidate_proposals_list_cache()
    with get_session() as db:
        db.query(IncomingProposalDismissal).delete()
        db.query(IncomingProposalRestoration).delete()
        db.commit()
    yield events
    proposals_mod.invalidate_proposals_list_cache()


def _local(name='snap'):
    return {'id': f'local:whatsapp:{name}', 'origin': 'local', 'source': 'whatsapp',
            'dedupe_key': f'whatsapp:120363429388641227@g.us', 'source_thread_id': '120363429388641227@g.us',
            'title': f'Fix {name}', 'summary': 'Client asks for a fix', 'project_id': 7,
            'project_name': 'DecisionsAI', 'tasks': ['a', 'b'], 'message_ids': [f'{name}-m1', f'{name}-m2']}


def _snapshot(key):
    from distr.core.db import get_session
    with get_session() as db:
        return db.execute(text('SELECT snapshot FROM incoming_proposal_dismissals WHERE key=:k'),
                          {'k': key}).scalar()


def test_dismiss_stores_full_snapshot():
    proposal = _local()
    proposals_mod.dismiss_proposal(proposal)
    key = proposals_mod.dismissal_receipt_keys(proposal)[0]
    stored = json.loads(_snapshot(key))
    assert stored == proposal  # identity fields (WhatsApp ids) kept verbatim


def test_snapshot_redacts_secrets_strips_bytes_and_caps_size():
    proposal = {**_local('secret'),
                'summary': 'use key sk-abcdefghijklmnopqrstuvwx and password: hunter22 card 4111 1111 1111 1111',
                'api_key': 'EXAMPLE_ACCESS_KEY_VALUE', 'attachments': [{'bytes': 'x' * 100}],
                '_internal': 'drop', 'messages': ['y' * 3000] * 20}
    snap = json.loads(proposals_mod.proposal_snapshot_json(proposal))
    assert 'sk-abcdefghijklmnopqrstuvwx' not in snap['summary']
    assert 'hunter22' not in snap['summary'] and '4111 1111' not in snap['summary']
    assert snap['api_key'] == '[redacted]'
    assert 'attachments' not in snap and '_internal' not in snap
    assert 'messages' not in snap  # shed to fit the 20 KB cap
    assert len(proposals_mod.proposal_snapshot_json(proposal).encode()) <= 20_000
    assert snap['dedupe_key'] == proposal['dedupe_key'] and snap['message_ids'] == proposal['message_ids']


def test_restore_puts_snapshot_back_into_triage_cache_without_triage(monkeypatch):
    calls = []
    monkeypatch.setattr(proposals_mod, '_triage_conversations', lambda *a, **k: calls.append(1) or [])
    monkeypatch.setattr('distr.core.incoming.service.list_development_incoming', lambda **k: {'items': []})
    proposal = _local('back')
    proposals_mod._TRIAGE_CACHE.update({'at': time.monotonic(), 'proposals': [proposal]})
    proposals_mod.dismiss_proposal(proposal)
    proposals_mod._TRIAGE_CACHE['proposals'] = []  # e.g. re-triaged / restarted since
    key = proposals_mod.dismissal_receipt_keys(proposal)[0]
    result = proposals_mod.undismiss_proposal(key)
    assert result['snapshot_restored'] is True and result['pending_retriage'] is False
    assert result['proposal'] == proposals_mod._restored_card(proposal)
    assert proposals_mod._TRIAGE_CACHE['proposals'] == [proposal]
    listed = proposals_mod.list_incoming_proposals()['proposals']
    assert [p['id'] for p in listed] == [proposals_mod._proposal_handoff_key(proposal)] and listed[0]['title'] == proposal['title']
    time.sleep(0.05)
    assert calls == []


def test_restored_snapshot_survives_a_retriage_that_drops_it(monkeypatch):
    proposal = _local('pin')
    proposals_mod.dismiss_proposal(proposal)
    proposals_mod.undismiss_proposal(proposals_mod.dismissal_receipt_keys(proposal)[0])
    out = proposals_mod._undismissed_proposals(proposals_mod._with_restored_snapshots([]))
    assert out == [proposals_mod._restored_card(proposal)]
    proposals_mod.dismiss_proposal(proposal)  # dismissing again unpins
    assert proposals_mod._with_restored_snapshots([]) == []


def test_restore_without_snapshot_returns_known_task_without_retriage(monkeypatch):
    from distr.core.db import IncomingProposalDismissal, IncomingProposalRestoration, get_session
    with get_session() as db:
        db.add(IncomingProposalDismissal(key='local:whatsapp:legacy:abc', conversation_key='whatsapp:legacy',
                                         message_ids='["l1"]', title='Known task', summary='Known summary'))
        db.commit()
    proposals_mod._TRIAGE_CACHE.update({'at': time.monotonic(), 'proposals': []})
    result = proposals_mod.undismiss_proposal('local:whatsapp:legacy:abc')
    assert result['pending_retriage'] is False and result['snapshot_restored'] is True
    assert result['proposal']['title'] == 'Known task'
    assert result['proposal']['description'] == 'Known summary'
    assert result['proposal']['message_ids'] == ['l1']
    assert result['proposal']['source_thread_id'] == 'legacy'
    assert result['proposal']['tasks'] == [] and 'project_id' not in result['proposal']
    monkeypatch.setattr(proposals_mod, '_RESTORED_SNAPSHOTS', {})
    proposals_mod._TRIAGE_CACHE['proposals'] = []
    monkeypatch.setattr(proposals_mod, '_local_proposals', lambda *a: [])
    assert proposals_mod.list_incoming_proposals(refresh_local=False)['proposals'] == [result['proposal']]


def test_forced_resync_bypasses_ttl_but_is_rate_limited(monkeypatch):
    calls = []
    monkeypatch.setattr(proposals_mod, '_triage_conversations', lambda *a, **k: calls.append(1) or [])
    monkeypatch.setattr(proposals_mod, '_accepted_proposal_keys', lambda: set())
    monkeypatch.setattr('distr.core.incoming.service.list_development_incoming', lambda **k: {'items': []})

    def wait_idle():
        for _ in range(100):
            if not proposals_mod._TRIAGE_CACHE['running']:
                return
            time.sleep(0.02)

    proposals_mod._TRIAGE_CACHE.update({'at': time.monotonic(), 'proposals': []})
    proposals_mod.list_incoming_proposals()  # fresh TTL, not forced -> no triage
    time.sleep(0.05)
    assert calls == []
    proposals_mod.list_incoming_proposals(force=True)
    wait_idle()
    assert calls == [1]
    proposals_mod.list_incoming_proposals(force=True)  # within 30s gap -> no stampede
    wait_idle()
    assert calls == [1]
    proposals_mod._TRIAGE_CACHE['forced_at'] = time.monotonic() - 31
    proposals_mod.list_incoming_proposals(force=True)
    wait_idle()
    assert calls == [1, 1]


def test_migration_idempotent_and_old_schema_safe(tmp_path, monkeypatch):
    from distr.core.db.migrations import ensure_incoming_dismissal_snapshot_columns

    old = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    from distr.core.db import IncomingProposalRestoration, WhatsAppMessage
    from distr.core.db.projects import IncomingProposalProject, Project
    IncomingProposalRestoration.__table__.create(old, checkfirst=True)
    WhatsAppMessage.__table__.create(old, checkfirst=True)
    Project.__table__.create(old, checkfirst=True)
    IncomingProposalProject.__table__.create(old, checkfirst=True)
    with old.begin() as conn:
        conn.execute(text('CREATE TABLE incoming_proposal_dismissals (key VARCHAR PRIMARY KEY, '
                          'conversation_key VARCHAR NOT NULL, message_ids TEXT NOT NULL, created_at DATETIME NOT NULL)'))
    # Old schema: dismiss + restore still work through the ORM (snapshot is deferred).
    from sqlalchemy.orm import sessionmaker
    import contextlib
    Session = sessionmaker(bind=old)

    @contextlib.contextmanager
    def session():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    with old.begin() as conn:
        for col in ('title', 'summary'):
            conn.execute(text(f'ALTER TABLE incoming_proposal_dismissals ADD COLUMN {col} TEXT'))
    monkeypatch.setattr('distr.core.db.get_session', session)
    proposal = _local('old')
    assert proposals_mod.dismiss_proposal(proposal) is True
    key = proposals_mod.dismissal_receipt_keys(proposal)[0]
    assert proposals_mod.list_dismissed_proposals()[0]['key'] == key
    res = proposals_mod.undismiss_proposal(key)
    assert res['restored'] is True and res['pending_retriage'] is False
    ensure_incoming_dismissal_snapshot_columns(old)
    ensure_incoming_dismissal_snapshot_columns(old)
    with old.connect() as conn:
        cols = [r[1] for r in conn.execute(text('PRAGMA table_info(incoming_proposal_dismissals)'))]
    assert cols.count('snapshot') == 1 and cols.count('title') == 1
