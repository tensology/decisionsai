"""Undo for Incoming proposal dismissals: receipts, twins, idempotence, cache, routes."""

from __future__ import annotations

import pytest

from distr.core.incoming import proposals as proposals_mod


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    from distr.core.db import IncomingProposalDismissal, IncomingProposalRestoration, get_session

    events = []
    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change',
                        lambda **kw: events.append(kw))
    monkeypatch.setattr('distr.core.tensology_client.decide_work_request', lambda *a, **k: {})
    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests', lambda: [])
    monkeypatch.setattr(proposals_mod, '_RESTORED_SNAPSHOTS', {})
    monkeypatch.setattr(proposals_mod, '_TRIAGE_CACHE', {'at': 0.0, 'proposals': [], 'running': False})
    proposals_mod.invalidate_proposals_list_cache()
    with get_session() as db:
        db.query(IncomingProposalDismissal).delete()
        db.query(IncomingProposalRestoration).delete()
        db.commit()
    yield events
    proposals_mod.invalidate_proposals_list_cache()


def _local(name='undo-one'):
    return {'id': f'local:gmail:{name}', 'origin': 'local', 'source': 'gmail',
            'dedupe_key': f'gmail:{name}', 'title': f'Title {name}', 'message_ids': [f'{name}-m1']}


def _receipt_keys():
    from distr.core.db import IncomingProposalDismissal, IncomingProposalRestoration, get_session
    with get_session() as db:
        return sorted(r.key for r in db.query(IncomingProposalDismissal).all())


def test_undismiss_removes_receipt_and_proposal_passes_filter_again(_isolate):
    proposal = _local()
    assert proposals_mod.dismiss_proposal(proposal) is True
    assert proposals_mod._undismissed_proposals([proposal]) == []
    key = proposals_mod.dismissal_receipt_keys(proposal)[0]
    result = proposals_mod.undismiss_proposal(key)
    assert {k: result[k] for k in ('restored', 'key', 'removed_keys', 'upstream_dismissed', 'title')} == {
        'restored': True, 'key': key, 'removed_keys': [key], 'upstream_dismissed': False,
        'title': 'Title undo-one'}
    assert _receipt_keys() == []
    assert proposals_mod._undismissed_proposals([proposal]) == [proposal]
    assert _isolate[-1]['status'] == 'restored'


def test_undismiss_removes_tensology_twin_and_flags_upstream():
    proposal = {**_local('twin'), 'id': 'tensology:777', 'dedupe_key': 'gmail:twin'}
    # handoff key for a tensology id is the id itself, so force a twin with a local id variant
    proposal_local_id = {**proposal}
    proposals_mod.dismiss_proposal(proposal_local_id)
    keys = proposals_mod.dismissal_receipt_keys(proposal)
    assert keys == ['tensology:777']
    # Simulate the two-row case the dismiss writer produces when key != tensology:N
    from distr.core.db import IncomingProposalDismissal, IncomingProposalRestoration, get_session
    with get_session() as db:
        db.merge(IncomingProposalDismissal(key='gmail:twin:abc', conversation_key='gmail:twin',
                                           message_ids='["twin-m1"]'))
        db.commit()
    assert _receipt_keys() == ['gmail:twin:abc', 'tensology:777']
    result = proposals_mod.undismiss_proposal('gmail:twin:abc')
    assert sorted(result['removed_keys']) == ['gmail:twin:abc', 'tensology:777']
    assert result['upstream_dismissed'] is True
    assert _receipt_keys() == []


def test_undismiss_does_not_touch_other_dismissals_in_same_conversation():
    first, second = _local('conv-a'), _local('conv-a')
    second['message_ids'] = ['conv-a-m2']
    proposals_mod.dismiss_proposal(first)
    proposals_mod.dismiss_proposal(second)
    key = proposals_mod.dismissal_receipt_keys(first)[0]
    proposals_mod.undismiss_proposal(key)
    assert _receipt_keys() == [proposals_mod.dismissal_receipt_keys(second)[0]]


def test_undismiss_is_idempotent_and_validates_key(_isolate):
    proposal = _local('idem')
    proposals_mod.dismiss_proposal(proposal)
    key = proposals_mod.dismissal_receipt_keys(proposal)[0]
    assert proposals_mod.undismiss_proposal(key)['restored'] is True
    count = len(_isolate)
    again = proposals_mod.undismiss_proposal(key)
    assert again['restored'] is False and again['removed_keys'] == []
    assert len(_isolate) == count  # no event for a no-op
    with pytest.raises(ValueError):
        proposals_mod.undismiss_proposal('  ')


def test_undismiss_invalidates_list_cache(monkeypatch):
    proposal = _local('cache')
    monkeypatch.setitem(proposals_mod._TRIAGE_CACHE, 'proposals', [proposal])
    monkeypatch.setattr(proposals_mod, '_local_proposals', lambda *a: [proposal])
    monkeypatch.setattr('distr.core.incoming.service.list_development_incoming', lambda **k: {'items': []})
    assert [p['id'] for p in proposals_mod.list_incoming_proposals(refresh_local=False)['proposals']] == [proposal['id']]
    proposals_mod.dismiss_proposal(proposal)
    assert proposals_mod.list_incoming_proposals(refresh_local=False)['proposals'] == []
    assert proposals_mod._cached_proposals_list() is not None
    proposals_mod.undismiss_proposal(proposals_mod.dismissal_receipt_keys(proposal)[0])
    assert proposals_mod._cached_proposals_list() is None
    assert [p['id'] for p in proposals_mod.list_incoming_proposals(refresh_local=False)['proposals']] == [proposals_mod._proposal_handoff_key(proposal)]


def test_list_dismissed_newest_first_with_cached_title(monkeypatch):
    old, new = _local('old'), _local('new')
    proposals_mod.dismiss_proposal(old)
    proposals_mod.dismiss_proposal(new)
    monkeypatch.setitem(proposals_mod._TRIAGE_CACHE, 'proposals', [new])
    rows = proposals_mod.list_dismissed_proposals(limit=20)
    assert [r['key'] for r in rows][:2] == [proposals_mod.dismissal_receipt_keys(new)[0],
                                            proposals_mod.dismissal_receipt_keys(old)[0]]
    assert rows[0]['title'] == 'Title new' and rows[0]['title_recovered'] is True
    # The receipt now stores a title snapshot, so an uncached old dismissal still shows its title.
    assert rows[1]['title'] == 'Title old' and rows[1]['title_recovered'] is True and rows[1]['title_stored'] is True
    assert rows[0]['created_at'].endswith('Z')


def _client():
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.gui.web.routes.development.incoming import register_routes

    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    return TestClient(app)


def test_routes_dismiss_returns_keys_then_restore_and_list():
    client = _client()
    proposal = _local('route')
    res = client.post('/workflows/studio/incoming/proposals/dismiss', json={'proposal': proposal}).json()
    assert res['success'] is True and res['dismissed'] is True
    key = proposals_mod.dismissal_receipt_keys(proposal)[0]
    assert res['key'] == key and res['receipt_keys'] == [key] and res['upstream_dismissed'] is False
    listed = client.get('/workflows/studio/incoming/proposals/dismissed?limit=5').json()
    assert listed['success'] is True and listed['dismissed'][0]['key'] == key
    restored = client.post('/workflows/studio/incoming/proposals/restore', json={'key': key}).json()
    assert restored['success'] is True and restored['restored'] is True
    again = client.post('/workflows/studio/incoming/proposals/restore', json={'key': key})
    assert again.status_code == 200 and again.json()['restored'] is False
    bad = client.post('/workflows/studio/incoming/proposals/restore', json={})
    assert bad.status_code == 422 and bad.json()['success'] is False


def test_route_dismiss_tensology_reports_upstream():
    client = _client()
    proposal = {**_local('route-t'), 'id': 'tensology:991'}
    res = client.post('/workflows/studio/incoming/proposals/dismiss', json={'proposal': proposal}).json()
    assert res['receipt_keys'] == ['tensology:991'] and res['upstream_dismissed'] is True
    restored = client.post('/workflows/studio/incoming/proposals/restore', json={'key': 'tensology:991'}).json()
    assert restored['upstream_dismissed'] is True
