"""Restore is atomic, immediate and durable, including receipts predating snapshots."""
import json
import subprocess
import sys

import pytest
from sqlalchemy import text

from distr.core.incoming import proposals as p


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    from distr.core.db import IncomingProposalDismissal, IncomingProposalRestoration, get_session
    monkeypatch.setattr(p, '_RESTORED_SNAPSHOTS', {})
    monkeypatch.setattr(p, '_TRIAGE_CACHE', {'at':0.0, 'running':False, 'proposals':[]})
    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests', lambda: [])
    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change', lambda **kw: None)
    with get_session() as db:
        db.query(IncomingProposalDismissal).delete()
        db.query(IncomingProposalRestoration).delete()
        db.commit()
    p.invalidate_proposals_list_cache()
    yield
    with get_session() as db:
        db.query(IncomingProposalDismissal).delete()
        db.query(IncomingProposalRestoration).delete()
        db.commit()
    p.invalidate_proposals_list_cache()


def legacy(tensology=False):
    from distr.core.db import IncomingProposalDismissal, get_session
    proposal = {'id':'tensology:9001' if tensology else 'local:whatsapp:fixture@g.us',
                'source':'whatsapp', 'dedupe_key':'whatsapp:fixture@g.us',
                'title':'Recovered task', 'summary':'Recovered scope', 'message_ids':['fixture-1']}
    key = p._proposal_handoff_key(proposal)
    with get_session() as db:
        db.add(IncomingProposalDismissal(key=key, conversation_key=proposal['dedupe_key'],
                                         message_ids=json.dumps(proposal['message_ids']),
                                         title=proposal['title'], summary=proposal['summary']))
        db.commit()
    return key


@pytest.mark.parametrize('tensology', [False, True])
def test_legacy_restore_is_immediate_and_survives_fresh_process(tensology):
    key = legacy(tensology)
    result = p.undismiss_proposal(key)
    assert result['restored'] and not result['pending_retriage']
    assert result['proposal']['title'] == 'Recovered task'
    fresh = subprocess.run([sys.executable, '-c',
        'import json; from distr.core.incoming.proposals import _with_restored_snapshots; '
        'print(json.dumps(_with_restored_snapshots([])))'], capture_output=True, text=True, timeout=15)
    assert fresh.returncode == 0, fresh.stderr
    assert json.loads(fresh.stdout) == [result['proposal']]
    assert p.undismiss_proposal(key)['proposal'] == result['proposal']


def test_restored_window_wins_without_hiding_other_work():
    result = p.undismiss_proposal(legacy())['proposal']
    regenerated = {**result, 'title':'Different generated wording'}
    other = {**result, 'message_ids':['fixture-2'], 'title':'Different source window'}
    assert p._with_restored_snapshots([regenerated, other]) == [other, result]


def test_redismiss_removes_durable_restore(monkeypatch):
    from distr.core.db import IncomingProposalRestoration, get_session
    proposal = p.undismiss_proposal(legacy())['proposal']
    monkeypatch.setattr('distr.core.tensology_client.decide_work_request', lambda *a, **kw: {})
    p.dismiss_proposal(proposal)
    assert p._with_restored_snapshots([]) == []
    with get_session() as db:
        assert db.query(IncomingProposalRestoration).count() == 0


def test_restore_failure_retains_dismissal(monkeypatch):
    from distr.core.db import IncomingProposalDismissal, IncomingProposalRestoration, get_session
    key = legacy()
    monkeypatch.setattr(p, 'proposal_brief_json', lambda proposal: None)
    with pytest.raises(ValueError, match='dismissal was retained'):
        p.undismiss_proposal(key)
    with get_session() as db:
        assert db.get(IncomingProposalDismissal, key) is not None
        assert db.query(IncomingProposalRestoration).count() == 0


def test_unrelated_snapshot_is_not_associated_with_legacy_receipt():
    from distr.core.db import get_session
    key = legacy()
    with get_session() as db:
        db.execute(text('UPDATE incoming_proposal_dismissals SET snapshot=:s WHERE key=:k'),
                   {'k':key, 's':json.dumps({'id':'local:whatsapp:wrong@g.us','title':'Wrong task',
                                             'message_ids':['wrong-1']})})
        db.commit()
    proposal = p.undismiss_proposal(key)['proposal']
    assert proposal['title'] == 'Recovered task'
    assert proposal['message_ids'] == ['fixture-1']
    assert proposal['source_thread_id'] == 'fixture@g.us'


def test_source_evidence_is_restricted_to_receipt_thread():
    from distr.core.db import WhatsAppMessage, get_session
    key = legacy()
    with get_session() as db:
        db.add(WhatsAppMessage(message_id='fixture-1',jid='fixture@g.us',text='Original request'))
        db.add(WhatsAppMessage(message_id='fixture-other',jid='wrong@g.us',text='Unrelated'))
        db.commit()
    proposal = p.undismiss_proposal(key)['proposal']
    assert proposal['evidence'] == [{'id':'fixture-1','body':'Original request','direction':'inbound'}]


def test_hide_history_preserves_receipt_snapshot_and_suppression_across_process():
    from distr.core.db import IncomingProposalDismissal, get_session, engine
    from distr.core.db.migrations import ensure_incoming_dismissal_snapshot_columns
    ensure_incoming_dismissal_snapshot_columns(engine)
    key = legacy()
    with get_session() as db:
        before = tuple(db.execute(text('SELECT key,conversation_key,message_ids,created_at,title,summary,snapshot FROM incoming_proposal_dismissals WHERE key=:k'), {'k':key}).one())
    assert p.hide_dismissed_proposal(key)['hidden']
    assert p.list_dismissed_proposals() == []
    with get_session() as db:
        after = tuple(db.execute(text('SELECT key,conversation_key,message_ids,created_at,title,summary,snapshot FROM incoming_proposal_dismissals WHERE key=:k'), {'k':key}).one())
        assert before == after and db.get(IncomingProposalDismissal, key)
    fresh = subprocess.run([sys.executable, '-c',
        'from distr.core.incoming.proposals import list_dismissed_proposals,_undismissed_proposals; '
        'assert list_dismissed_proposals()==[]; '
        'assert _undismissed_proposals([{"id":"local:whatsapp:fixture@g.us",'
        '"dedupe_key":"whatsapp:fixture@g.us","message_ids":["fixture-1"]}])==[]'],
        capture_output=True,text=True,timeout=15)
    assert fresh.returncode == 0, fresh.stderr


def test_hide_history_hides_twins_but_not_other_windows():
    from distr.core.db import IncomingProposalDismissal, get_session, engine
    from distr.core.db.migrations import ensure_incoming_dismissal_snapshot_columns
    ensure_incoming_dismissal_snapshot_columns(engine)
    key = legacy(tensology=True)
    with get_session() as db:
        row = db.get(IncomingProposalDismissal, key)
        db.add(IncomingProposalDismissal(key='local:whatsapp:twin', conversation_key=row.conversation_key,
            message_ids=row.message_ids, created_at=row.created_at, title=row.title))
        db.add(IncomingProposalDismissal(key='local:whatsapp:new-window', conversation_key=row.conversation_key,
            message_ids='["other-message"]', title='Separate task'))
        db.commit()
    p.hide_dismissed_proposal(key)
    assert [row['title'] for row in p.list_dismissed_proposals()] == ['Separate task']


def test_two_restored_windows_have_distinct_stable_action_ids():
    from distr.core.db import IncomingProposalDismissal, get_session
    key = legacy()
    first = p.undismiss_proposal(key)['proposal']
    second_source = {**first, 'id': first['original_proposal_id'], 'message_ids':['fixture-2']}
    second_key = p._proposal_handoff_key(second_source)
    with get_session() as db:
        db.add(IncomingProposalDismissal(key=second_key,conversation_key=second_source['dedupe_key'],
            message_ids='["fixture-2"]',title='Second task'))
        db.commit()
    second = p.undismiss_proposal(second_key)['proposal']
    assert first['id'] != second['id']
    assert p._proposal_handoff_key(first) == key
    assert p._proposal_handoff_key(second) == second_key
    assert {tuple(row['message_ids']) for row in p._with_restored_snapshots([])} == {('fixture-1',),('fixture-2',)}


def test_restore_uses_richer_snapshot_on_twin_receipt():
    from distr.core.db import IncomingProposalDismissal, get_session
    key = legacy(tensology=True)
    original = {'id':key,'title':'Full original task','source':'whatsapp',
                'dedupe_key':'whatsapp:fixture@g.us','message_ids':['fixture-1'],
                'project_id':7,'tasks':['Original acceptance requirement']}
    with get_session() as db:
        row = db.get(IncomingProposalDismissal, key)
        db.execute(text('UPDATE incoming_proposal_dismissals SET snapshot=:s WHERE key=:k'),
                   {'s':json.dumps(original),'k':key})
        db.add(IncomingProposalDismissal(key='local:whatsapp:twin', conversation_key=row.conversation_key,
            message_ids=row.message_ids, created_at=row.created_at))
        db.commit()
    result = p.undismiss_proposal('local:whatsapp:twin')
    assert result['proposal'] == {**original, 'source_message_at': None, 'source_started_at': None, 'expires_at': None, 'is_expired': False}
    assert set(result['removed_keys']) == {key,'local:whatsapp:twin'}


def test_legacy_restore_uses_explicit_existing_source_project_link():
    from distr.core.db import WhatsAppPhoneLink, get_session
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.projects import Project
    key = legacy()
    with get_session() as db:
        project = Project(name='Known linked project')
        db.add(project)
        db.flush()
        board = KanbanBoard(name='Known linked board', default_project_id=project.id)
        db.add(board)
        db.flush()
        db.add(WhatsAppPhoneLink(board_id=board.id,phone_jid='fixture@g.us',contact_name='Known source'))
        project_id = project.id
        db.commit()
    restored = p.undismiss_proposal(key)['proposal']
    assert (restored['project_id'],restored['project_name']) == (project_id,'Known linked project')
    assert restored['source_conversation']['thread_id'] == 'fixture@g.us'


def test_restore_immediately_honors_saved_project_correction():
    from distr.core.db import get_session
    from distr.core.db.projects import IncomingProposalProject, Project
    key = legacy()
    with get_session() as db:
        project = Project(name='User corrected project')
        db.add(project)
        db.flush()
        db.merge(IncomingProposalProject(key=key,project_id=project.id))
        project_id = project.id
        db.commit()
    result = p.undismiss_proposal(key)['proposal']
    assert result['project_id'] == project_id
    assert result['project_name'] == 'User corrected project'
    assert result['project_corrected']


def test_history_delete_endpoint_rejects_missing_identity():
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.gui.web.routes.development.incoming import register_routes
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)
    for body in ({}, {'key':''}, []):
        assert client.request('DELETE','/workflows/studio/incoming/proposals/dismissed',json=body).status_code == 422
    assert client.request('DELETE','/workflows/studio/incoming/proposals/dismissed',content='{broken').status_code == 422
