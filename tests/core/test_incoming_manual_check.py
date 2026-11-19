from unittest.mock import Mock

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


def test_only_explicit_post_queues_upstream_check_and_get_is_read_only(monkeypatch):
    from distr.gui.web.routes.development.incoming import register_routes
    upstream = Mock()
    upstream.post.return_value = {'checks': {'whatsapp': {'source_state': 'queued'}}, 'queued': ['whatsapp'], 'failed': {}}
    upstream.get.return_value = {'checks': {'whatsapp': {'source_state': 'complete'}}}
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **kwargs: upstream)
    local_check = Mock()
    monkeypatch.setattr('distr.core.incoming.mail_check.start_mail_check', local_check)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)
    url = '/workflows/studio/incoming/check'
    assert client.get(url).json()['checks']['whatsapp']['source_state'] == 'complete'
    upstream.post.assert_not_called()
    assert client.post(url).json()['queued'] == ['whatsapp']
    upstream.post.assert_called_once()
    assert upstream.post.call_args.args == ('incoming/check', {'sources': ['whatsapp', 'gmail', 'mailshot']})
    assert client.get(url).status_code == 200
    assert upstream.post.call_count == 1
    local_check.assert_not_called()


def test_connected_source_check_only_queues_selected_channel(monkeypatch):
    from distr.gui.web.routes.development.incoming import register_routes
    upstream = Mock()
    upstream.post.return_value = {'queued': ['gmail']}
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **kwargs: upstream)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)
    url = '/workflows/studio/incoming/check'
    assert client.post(url, json={'sources': ['gmail', 'gmail']}).json()['queued'] == ['gmail']
    assert client.post(url, json={'sources': ['unknown']}).status_code == 400
    upstream.post.assert_called_once()
    assert upstream.post.call_args.args == ('incoming/check', {'sources': ['gmail']})


def test_unavailable_upstream_does_not_claim_source_check_success(monkeypatch):
    from distr.gui.web.routes.development.incoming import register_routes
    upstream = Mock()
    upstream.post.side_effect = RuntimeError('offline')
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **kwargs: upstream)
    local_check = Mock()
    monkeypatch.setattr('distr.core.incoming.mail_check.start_mail_check', local_check)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    response = TestClient(app).post('/workflows/studio/incoming/check')
    assert response.status_code == 503
    assert response.json()['success'] is False
    local_check.assert_not_called()


def test_forced_proposal_reconciliation_never_queues_a_manual_source_check(monkeypatch):
    from distr.core.incoming import proposals
    upstream = Mock()
    upstream.get.return_value = []
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **kwargs: upstream)
    monkeypatch.setattr('distr.core.tensology_project_sync.sync_pinned_projects', lambda **kwargs: {})
    monkeypatch.setattr('distr.core.incoming.project_links.project_links', lambda **kwargs: [])
    monkeypatch.setattr('distr.core.incoming.proposal_sync.fetch_remote_proposals', lambda: [])
    monkeypatch.setattr('distr.core.incoming.proposal_sync.wait_for_triage', lambda *args: None)
    monkeypatch.setattr('distr.core.incoming.proposal_sync.reconcile', lambda local, remote, **kwargs: ([], {'state': 'complete'}))
    monkeypatch.setattr('distr.core.incoming.service.list_development_incoming', lambda **kwargs: {'items': []})
    monkeypatch.setattr(proposals, '_local_proposals', lambda *args: [])
    proposals.list_incoming_proposals(force=True)
    assert not any(call.args and call.args[0] == 'incoming/check' for call in upstream.post.call_args_list)
