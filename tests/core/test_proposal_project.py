"""Persisted project corrections must win over triage and stale handoffs."""
import json
import subprocess
import sys
from unittest.mock import Mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.core.db import get_session
from distr.core.db.projects import IncomingProposalProject, Project
from distr.core.incoming import proposals as mod
from distr.gui.web.routes.development.incoming import register_routes


@pytest.fixture
def assignment(monkeypatch):
    with get_session() as db:
        first, second = Project(name='Merrypak fixture'), Project(name='Multisnack fixture')
        db.add_all([first, second]); db.commit()
        original, target = first.id, second.id
    proposal = {'id': f'local:whatsapp:project-{target}', 'origin': 'local', 'source': 'whatsapp',
                'dedupe_key': f'whatsapp:project-{target}', 'project_id': original,
                'project_name': 'Merrypak fixture', 'title': 'Normalize DPP delivery labels',
                'description': 'Keep source brief', 'tasks': ['Check labels'], 'message_ids': ['a', 'b']}
    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests', lambda: [])
    monkeypatch.setitem(mod._TRIAGE_CACHE, 'proposals', [proposal])
    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change', Mock())
    return proposal, original, target


def test_correction_survives_database_reconnect_process_and_retriage(assignment):
    from distr.core.db import engine
    proposal, original, target = assignment
    changed = mod.reassign_proposal_project(proposal, target)
    assert changed['project_id'] == target
    assert changed['project_name'] == 'Multisnack fixture'
    assert changed['description'] == proposal['description']
    assert proposal['project_id'] == original
    engine.dispose()
    regenerated = {**proposal, 'title': 'New AI wording', 'project_id': original}
    assert mod.apply_project_corrections([regenerated])[0]['project_id'] == target
    assert mod.list_incoming_proposals(refresh_local=False)['proposals'][0]['project_id'] == target
    result = subprocess.run([sys.executable, '-c',
        'import json,sys; from distr.core.incoming.proposals import apply_project_corrections; '
        'p,target=json.load(sys.stdin); assert apply_project_corrections([p])[0]["project_id"]==target'],
        input=json.dumps([proposal, target]), text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    # A later request in the same conversation is a distinct work snapshot.
    later = {**proposal, 'message_ids': ['a', 'b', 'new-request']}
    assert mod.apply_project_corrections([later])[0]['project_id'] == original


@pytest.mark.parametrize('upstream', [False, True])
def test_stale_handoff_uses_saved_project_and_preserves_source(assignment, monkeypatch, upstream):
    proposal, original, target = assignment
    if upstream:
        proposal = {**proposal, 'id': f'tensology:{900000 + target}', 'origin': 'tensology'}
        client = Mock()
        client.get.return_value = {**proposal, 'id': 900000 + target,
                                   'project': {'name': 'Merrypak fixture'}, 'attachments': []}
        monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **k: client)
        monkeypatch.setattr('distr.core.tensology_client.decide_work_request', Mock())
        monkeypatch.setattr('distr.core.tensology_client._resolve_tensology_project', Mock(side_effect=AssertionError('Must use correction')))
    mod.reassign_proposal_project(proposal, target)
    create = Mock(return_value=123)
    monkeypatch.setattr('distr.core.workflow.development_threads.ensure_development_thread', create)
    monkeypatch.setattr('distr.core.incoming.media.proposal_attachments', lambda *a, **k: [])
    monkeypatch.setattr('distr.core.incoming.media.register_thread_attachments', Mock())
    monkeypatch.setattr(mod, '_record_proposal_handoff', Mock())
    assert mod.create_proposal_thread(proposal) == 123
    assert create.call_args.kwargs['project_id'] == target
    assert 'Project: Multisnack fixture' in create.call_args.kwargs['starting_question']
    assert 'Keep source brief' in create.call_args.kwargs['starting_question']


def test_http_validation_and_canonical_lookup(assignment):
    proposal, original, target = assignment
    app, router = FastAPI(), APIRouter()
    register_routes(router, None); app.include_router(router)
    client = TestClient(app)
    url = '/workflows/studio/incoming/proposals/project'
    for value in ('2', True, None, -1, 987654321):
        assert client.put(url, json={'proposal_id': proposal['id'], 'project_id': value, 'message_ids': proposal['message_ids']}).status_code == 422
    assert client.put(url, content='invalid').status_code == 422
    assert client.put(url, json={'proposal_id': 'missing', 'project_id': target, 'message_ids': []}).status_code == 404
    assert client.put(url, json={'proposal_id': proposal['id'], 'project_id': target, 'message_ids': ['stale']}).status_code == 409
    assert mod.apply_project_corrections([proposal])[0]['project_id'] == original
    response = client.put(url, json={'proposal_id': proposal['id'], 'project_id': target, 'message_ids': proposal['message_ids']})
    assert response.status_code == 200
    assert response.json()['proposal']['project_id'] == target
    client.put(url, json={'proposal_id': proposal['id'], 'project_id': target, 'message_ids': proposal['message_ids']})
    with get_session() as db:
        assert db.query(IncomingProposalProject).filter_by(key=mod._proposal_handoff_key(proposal)).count() == 1


def test_accepted_proposal_cannot_be_reassigned(assignment, monkeypatch):
    proposal, original, target = assignment
    monkeypatch.setattr(mod, '_accepted_proposal_keys', lambda: {mod._proposal_handoff_key(proposal)})
    with pytest.raises(ValueError, match='already has a thread'):
        mod.reassign_proposal_project(proposal, target)
    assert mod.apply_project_corrections([proposal])[0]['project_id'] == original


def test_merge_checks_corrected_projects_before_model_or_thread(assignment):
    proposal, original, target = assignment
    mod.reassign_proposal_project(proposal, target)
    second = {**proposal, 'id': 'local:other-merge', 'message_ids': ['other']}
    complete = Mock()
    with pytest.raises(ValueError, match='same project'):
        mod.merge_proposals(proposal, second, complete=complete)
    complete.assert_not_called()


def test_missing_corrected_project_never_falls_back_to_old_project(assignment, monkeypatch):
    proposal, original, target = assignment
    mod.reassign_proposal_project(proposal, target)
    with get_session() as db:
        # SET NULL is the database's project-deletion behavior.
        db.query(IncomingProposalProject).filter_by(key=mod._proposal_handoff_key(proposal)).update({'project_id': None})
        db.commit()
    assert mod.apply_project_corrections([proposal])[0]['project_id'] is None
    create = Mock()
    monkeypatch.setattr('distr.core.workflow.development_threads.ensure_development_thread', create)
    with pytest.raises(ValueError, match='Link the proposal'):
        mod.create_proposal_thread(proposal)
    create.assert_not_called()
