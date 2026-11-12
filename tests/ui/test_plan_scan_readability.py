"""A successful scan must not imply that it understands an unsupported app."""
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import expect

from distr.core.planning import service
from distr.gui.web.routes.development.planning import register_routes
from tests.core.test_plan_background_scan import _open, _wait, plan_db  # noqa: F401
from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_plan_inline_edit_playwright import Backend, PLAN_URL

pytestmark = pytest.mark.e2e_playwright


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
@pytest.mark.parametrize('coverage', ['none', 'partial'])
def test_scan_coverage_is_visible_without_displacing_plan_actions(page, width, coverage):
    backend = Backend()
    original = backend.workspace
    backend.workspace = lambda: {**original(), 'scan': {'state': 'done', 'coverage': coverage}}
    _install_api(page, [])
    page.route('**/api/**', backend.route)
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(PLAN_URL)
    hint = page.locator('.plan-scan-hint')
    expect(hint).to_contain_text('could not map' if coverage == 'none' else 'partial')
    expect(hint).to_contain_text('before building tickets')
    bounds = hint.bounding_box()
    assert bounds and bounds['x'] >= 0 and bounds['y'] >= 0
    assert bounds['x'] + bounds['width'] <= width
    assert bounds['y'] + bounds['height'] <= 900
    assert hint.evaluate('n => n.scrollWidth <= n.clientWidth'), 'The coverage warning must remain readable'
    sections = page.get_by_role('navigation', name='Plan sections')
    for label in ('Interfaces', 'Data', 'Documents'):
        expect(sections.get_by_role('button', name=label, exact=True)).to_be_visible()
    documents = sections.get_by_role('button', name='Documents', exact=True)
    documents.click()
    expect(documents).to_have_attribute('aria-pressed', 'true')
    expect(page.get_by_role('button', name='Print PDF', exact=True)).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    out = Path('/tmp/decisions-development-usability')
    out.mkdir(exist_ok=True)
    page.screenshot(path=str(out / f'scan-{coverage}-{width}.png'), animations='disabled')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_second_board_loads_its_finished_scan_and_preserves_chat_draft(page, width, plan_db, monkeypatch):
    gate = threading.Event()
    started = threading.Event()
    real_scan = service.materialize_project_scan

    def wait_for_release(workspace_id, **kwargs):
        started.set()
        assert gate.wait(15)
        return real_scan(workspace_id, **kwargs)

    monkeypatch.setattr(service, 'materialize_project_scan', wait_for_release)
    first = _open(plan_db, key='decisions:first')
    second_id = None
    reads = []
    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix='/api')
    client = TestClient(app)
    out = Path('/tmp/decisions-shared-project-plan')
    out.mkdir(exist_ok=True)
    page.set_viewport_size({'width': width, 'height': 900})
    _install_api(page, [])
    backend = Backend()

    def relay(route):
        nonlocal second_id
        request = route.request
        path = urlsplit(request.url).path
        if request.method == 'POST' and path.endswith('/plan-workspaces'):
            body = {**request.post_data_json, 'project_id': plan_db['project_id']}
            response = client.post(path, json=body)
            second_id = response.json()['id']
            assert second_id != first['id']
        elif re.fullmatch(r'/api/workflows/studio/plan-workspaces/\d+(?:/scan-status)?', path):
            if not path.endswith('/scan-status'):
                reads.append(path)
                if len(reads) == 1:
                    return route.fulfill(status=503, json={'detail': 'Temporary read failure.'})
            response = client.get(path)
        elif path.endswith('/conversation'):
            return route.fulfill(json={'messages': [], 'attachments': []})
        else:
            return backend.route(route)
        return route.fulfill(status=response.status_code, json=response.json())

    page.route('**/api/**', relay)
    try:
        assert started.wait(2)
        page.goto(PLAN_URL)
        page.get_by_role('button', name='Documents', exact=True).click()
        page.get_by_role('tab', name='PRD', exact=True).click()
        editor = page.get_by_role('textbox', name='Product requirements', exact=True)
        expect(editor).to_be_visible()
        expect(editor).not_to_contain_text('/auctions')
        message = page.get_by_role('textbox', name='Message', exact=True)
        message.fill('Review the auction journey for this board.')
        page.screenshot(path=out / f'{width}-scanning.png', animations='disabled')
        gate.set()
        expect(editor).to_contain_text('/auctions', timeout=12000)
        expect(message).to_have_value('Review the auction journey for this board.')
        expect(page.locator('.plan-scan-hint')).to_contain_text('Project map is partial')
        assert len(reads) == 2 and all(path.endswith(f'/{second_id}') for path in reads)
        assert service.plan_scan_status(first['id'])['state'] == 'done'
        assert service.plan_scan_status(second_id)['state'] == 'done'
        page.screenshot(path=out / f'{width}-settled.png', animations='disabled')
    finally:
        gate.set()
        _wait(first['id'])
        if second_id is not None:
            _wait(second_id)
