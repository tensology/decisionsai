"""One user decision cannot authorize two concurrent tool executions."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from distr.core.workflow import dev_approvals
from distr.gui.web.routes import dev_approvals as routes
from tests.core.test_workflow_reply_claim import store
from tests.core.test_workflow_reply_end_to_end import CLICK


def test_same_app_concurrent_consumers_authorize_one_execution(store, monkeypatch):
    monkeypatch.setattr(routes, '_require_token', lambda request: None)
    app = FastAPI(); app.include_router(routes.router)
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        ahash = dev_approvals.args_hash({'event_id': 'fixture-event'})
        request = client.post('/api/dev-approvals/request', json={'tool': 'calendar', 'action': 'delete', 'args_hash': ahash}).json()
        rid = request['request_id']
        assert client.post(f'/api/dev-approvals/{rid}/approve', json={}, headers=CLICK).status_code == 200
        token = client.get(f'/api/dev-approvals/{rid}').json()['approval_id']
        payload = {'approval_id': token, 'tool': 'calendar', 'action': 'delete', 'args_hash': ahash}
        entered, release, lock = Event(), Event(), Lock()
        count = 0
        original_sign = routes._sign
        def after_loaded_approval(row, approval_hash):
            nonlocal count
            with lock:
                count += 1
                first = count == 1
            if first:
                entered.set()
                assert release.wait(5)
            else:
                release.set()
            return original_sign(row, approval_hash)
        monkeypatch.setattr(routes, '_sign', after_loaded_approval)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(client.post, '/api/dev-approvals/consume', json=payload)
                assert entered.wait(5)
                second = pool.submit(client.post, '/api/dev-approvals/consume', json=payload)
                # Without a claim both reads reach the old approved state. With
                # a write claim the second waits until the first is released.
                release.wait(.15)
                release.set()
                outcomes = [first.result(5), second.result(5)]
            assert all(response.status_code == 200 for response in outcomes)
            assert sum(response.json()['ok'] for response in outcomes) == 1
        finally:
            release.set()
