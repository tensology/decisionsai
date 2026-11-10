"""Render the real Incoming UI with actual backend source-scope filtering."""
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from distr.core.incoming import proposals, project_links
from tests.ui.test_incoming_dismiss_undo import fixture_base
from tests.ui.test_development_harness_playwright import _install_api

pytestmark = pytest.mark.e2e_playwright


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_only_linked_cards_and_counts_appear_and_revocation_shows_empty(page, fixture_base, monkeypatch, width):
    scope = {('whatsapp', 'linked-person'): {7}}
    monkeypatch.setattr(project_links, 'source_project_index', lambda **kwargs: scope)
    linked = {'id': 'local:whatsapp:linked-person', 'origin': 'local', 'source': 'whatsapp',
              'source_thread_id': 'linked-person', 'message_ids': ['m1'], 'project_id': 7,
              'project_name': 'DecisionsAI', 'title': 'Fix the linked project report',
              'description': 'A request from an explicitly project-linked conversation.', 'tasks': ['Check report']}
    rows = [linked, {**linked, 'id': linked['id'] + ':split-hash'},
            {**linked, 'id': 'personal', 'source_thread_id': 'personal@g.us', 'title': 'Personal coworking message'},
            {**linked, 'id': 'wrong-project', 'project_id': 8, 'title': 'Wrong AuctionNow assignment'}]
    _install_api(page, [])
    def api(route):
        if urlparse(route.request.url).path != '/api/workflows/studio/incoming/proposals':
            route.fallback()
            return
        allowed = proposals._scoped_proposals(rows)
        route.fulfill(json={'proposals': allowed, 'counts': {'whatsapp': len(allowed)}, 'sync': {'state': 'complete'}})
    page.route('**/api/workflows/studio/incoming/proposals*', api)
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(f'{fixture_base}/development/incoming/')
    cards = page.locator('#incoming-proposals-list .proposal-card')
    expect(cards).to_have_count(1)
    expect(cards.first).to_contain_text('Fix the linked project report')
    expect(page.locator('#incoming-proposals-count')).to_have_text('1')
    shots = Path('/tmp/incoming-linked-scope-verify')
    shots.mkdir(exist_ok=True)
    page.screenshot(path=str(shots / f'{width}-linked.png'), full_page=True)
    scope.clear()
    page.reload()
    expect(cards).to_have_count(0)
    expect(page.locator('#incoming-proposals-count')).to_have_text('0')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(shots / f'{width}-empty.png'), full_page=True)
