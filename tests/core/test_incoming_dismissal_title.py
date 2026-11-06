"""Dismissal receipts keep a plain-text title/summary snapshot; additive migration; NULL legacy rows still work."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from distr.core.incoming import proposals as proposals_mod


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    from distr.core.db import IncomingProposalDismissal, IncomingProposalRestoration, get_session

    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change', lambda **kw: None)
    monkeypatch.setattr('distr.core.tensology_client.decide_work_request', lambda *a, **k: {})
    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests', lambda: [])
    monkeypatch.setattr(proposals_mod, '_proposal_title_index', lambda: {})   # nothing cached in memory
    monkeypatch.setattr(proposals_mod, '_RESTORED_SNAPSHOTS', {})
    monkeypatch.setattr(proposals_mod, '_TRIAGE_CACHE', {'at': 0.0, 'proposals': [], 'running': False})
    proposals_mod.invalidate_proposals_list_cache()
    with get_session() as db:
        db.query(IncomingProposalDismissal).delete()
        db.query(IncomingProposalRestoration).delete()
        db.commit()
    yield
    proposals_mod.invalidate_proposals_list_cache()


def test_old_schema_migration_preserves_rows_and_is_idempotent(tmp_path):
    from distr.core.db.migrations import ensure_incoming_dismissal_snapshot_columns

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE incoming_proposal_dismissals (key VARCHAR PRIMARY KEY, conversation_key VARCHAR NOT NULL,"
                          " message_ids TEXT NOT NULL, created_at DATETIME NOT NULL)"))
        conn.execute(text("INSERT INTO incoming_proposal_dismissals VALUES ('k1','gmail:t1','[\"m1\"]','2026-11-03 08:00:00')"))
        conn.commit()
    ensure_incoming_dismissal_snapshot_columns(engine)
    ensure_incoming_dismissal_snapshot_columns(engine)          # second run: no error, no duplicate column
    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(incoming_proposal_dismissals)"))]
        row = conn.execute(text("SELECT key, conversation_key, message_ids, title, summary FROM incoming_proposal_dismissals")).one()
    assert cols.count("title") == 1 and cols.count("summary") == 1
    assert tuple(row) == ("k1", "gmail:t1", '["m1"]', None, None)


def test_dismiss_stores_plain_truncated_snapshot_and_list_returns_it():
    proposal = {'id': 'local:gmail:t2', 'origin': 'local', 'source': 'gmail', 'dedupe_key': 'gmail:t2',
                'title': '<b>Fix</b>\n invoice ' + 'x' * 300, 'summary': 'Customer <i>asked</i> ' + 'y' * 600,
                'message_ids': ['t2-m1']}
    proposals_mod.dismiss_proposal(proposal)
    row = proposals_mod.list_dismissed_proposals()[0]
    assert row['title'].startswith('Fix invoice x') and len(row['title']) == 200 and '<' not in row['title']
    assert row['summary'].startswith('Customer asked y') and len(row['summary']) == 500
    assert row['title_recovered'] is True and row['title_stored'] is True
    restored = proposals_mod.undismiss_proposal(row['key'])
    assert restored['restored'] is True and restored['title'] == row['title']


def test_null_legacy_rows_fall_back_to_title_not_stored():
    from distr.core.db import IncomingProposalDismissal, IncomingProposalRestoration, get_session
    with get_session() as db:
        db.add(IncomingProposalDismissal(key='legacy:1', conversation_key='gmail:legacy', message_ids='[]'))
        db.commit()
    row = proposals_mod.list_dismissed_proposals()[0]
    assert row['key'] == 'legacy:1' and row['title'] is None and row['summary'] is None
    assert row['title_recovered'] is False and row['title_stored'] is False
    assert proposals_mod.undismiss_proposal('legacy:1')['restored'] is True


def test_dismiss_without_title_still_works():
    proposals_mod.dismiss_proposal({'id': 'local:gmail:t3', 'source': 'gmail', 'dedupe_key': 'gmail:t3', 'message_ids': []})
    row = proposals_mod.list_dismissed_proposals()[0]
    assert row['title'] is None and row['title_recovered'] is False
