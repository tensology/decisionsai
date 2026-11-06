"""Real proposal assignment API and database with the rendered Development UI."""
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import expect

from distr.core.db import get_session
from distr.core.db.projects import Project
from distr.core.incoming import proposals as mod
from distr.gui.web.routes.development.incoming import register_routes
from tests.ui.test_development_harness_playwright import _install_api, TEST_BASE_URL

pytestmark = pytest.mark.e2e_playwright
EVIDENCE = Path('/tmp/decisions-proposal-project-qa')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_project_save_refresh_failure_and_thread_payload(page, monkeypatch, width):
    page.set_default_timeout(5000)
    with get_session() as db:
        original, target = Project(name='Merrypak'), Project(name='Multisnack')
        db.add_all([original, target]); db.commit()
        old_id, new_id = original.id, target.id
    proposal = {'id': f'local:whatsapp:dpp-{width}', 'origin': 'local', 'source': 'whatsapp',
                'dedupe_key': f'whatsapp:dpp-{width}', 'project_id': old_id, 'project_name': 'Merrypak',
                'title': 'Normalize DPP delivery labels without hard-coded invoice mappings',
                'description': 'Derive the delivery label from the order delivery code, method, and service metadata.',
                'tasks': ['Check delivery labels'], 'message_ids': [f'dpp-{width}'], 'participant': 'Roland'}
    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests', lambda: [])
    monkeypatch.setitem(mod._TRIAGE_CACHE, 'proposals', [proposal])
    monkeypatch.setattr(mod, '_local_proposals', lambda *a: [proposal])
    monkeypatch.setattr('distr.core.incoming.service.list_development_incoming', lambda **k: {'items': []})
    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change', lambda **k: None)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None); app.include_router(router)
    client = TestClient(app)
    _install_api(page, [])
    page.route('**/api/projects', lambda r: r.fulfill(json=[{'id': old_id, 'name': 'Merrypak'}, {'id': new_id, 'name': 'Multisnack'}]))
    fail = False
    held = []
    hold_save = True
    handoffs = []

    def proposals(route):
        path = urlparse(route.request.url).path.removeprefix('/api')
        if path.endswith('/project') and fail:
            route.fulfill(status=503, json={'error': 'Project save unavailable. Try again.'})
            return
        if path.endswith('/project') and hold_save:
            held.append(route)
            return
        if path.endswith('/thread'):
            handoffs.append(route.request.post_data_json)
            route.fulfill(status=503, json={'error': 'Test handoff stopped before execution.'})
            return
        kwargs = {'json': route.request.post_data_json} if route.request.method == 'PUT' else {}
        response = client.request(route.request.method, path, **kwargs)
        route.fulfill(status=response.status_code, json=response.json())

    page.route('**/api/workflows/studio/incoming/proposals**', proposals)
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(TEST_BASE_URL + '/development/incoming/')
    card = page.get_by_role('button', name='Open evidence for ' + proposal['title'], exact=True)
    expect(card).to_contain_text('Merrypak')
    card.click()
    select = page.get_by_label('Project', exact=True)
    expect(select).to_have_value(str(old_id))
    assert str(new_id) in select.locator('option').evaluate_all('(options) => options.map(o => o.value)'), select.inner_html()
    EVIDENCE.mkdir(exist_ok=True)
    page.screenshot(path=str(EVIDENCE / f'initial-{width}.png'))
    select.select_option(str(new_id))
    expect(page.locator('#proposal-project-status')).to_have_text('Saving...')
    expect(select).to_be_disabled()
    expect(page.get_by_role('dialog').get_by_role('button', name='Create Thread')).to_be_disabled()
    page.screenshot(path=str(EVIDENCE / f'busy-{width}.png'))
    hold_save = False
    response = client.put('/workflows/studio/incoming/proposals/project', json=held[0].request.post_data_json)
    held[0].fulfill(status=response.status_code, json=response.json())
    expect(page.locator('#proposal-project-status')).to_have_text('Saved')
    expect(card).to_contain_text('Multisnack')
    page.screenshot(path=str(EVIDENCE / f'saved-{width}.png'))
    page.keyboard.press('Escape')
    expect(page.get_by_role('dialog')).to_have_count(0)
    expect(card).to_be_focused()
    page.reload()
    expect(card).to_contain_text('Multisnack')
    card.click()
    expect(select).to_have_value(str(new_id))
    fail = True
    select.select_option(str(old_id))
    expect(page.locator('#proposal-project-status')).to_contain_text('Project save unavailable')
    expect(select).to_have_value(str(new_id))
    expect(select).to_be_enabled()
    expect(page.locator('#proposal-project-status')).to_have_css('color', 'rgb(252, 165, 165)')
    expect(page.get_by_role('dialog').get_by_role('button', name='Create Thread')).to_be_enabled()
    page.screenshot(path=str(EVIDENCE / f'error-{width}.png'))
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert page.locator('.proposal-modal').evaluate('e => e.getBoundingClientRect().right <= innerWidth')
    fail = False
    select.select_option(str(old_id))
    expect(page.locator('#proposal-project-status')).to_have_text('Saved')
    select.select_option(str(new_id))
    expect(page.locator('#proposal-project-status')).to_have_text('Saved')
    page.get_by_role('dialog').get_by_role('button', name='Create Thread').click()
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert handoffs[0]['proposal']['project_id'] == new_id
    card.click()
    page.get_by_role('button', name='Close', exact=True).last.click()
    expect(page.get_by_role('dialog')).to_have_count(0)
    card.click()
    page.get_by_role('button', name='Close', exact=True).first.click()
    expect(page.get_by_role('dialog')).to_have_count(0)
    card.click()
    page.locator('.proposal-modal-overlay').click(position={'x': 4, 'y': 4})
    expect(page.get_by_role('dialog')).to_have_count(0)
    page.screenshot(path=str(EVIDENCE / f'list-{width}.png'))
