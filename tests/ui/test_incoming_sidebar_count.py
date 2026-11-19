"""Incoming navigation counts proposal cards, independently of channel messages."""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_incoming_dismiss_undo import fixture_base, _mock, _proposal

pytestmark = pytest.mark.e2e_playwright
SHOTS = Path('/tmp/decisions-incoming-count')


@pytest.mark.parametrize('width', [390, 1440])
def test_badge_tracks_proposals_through_dismiss_restore_and_empty(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 950})
    _install_api(page, [])
    state = _mock(page, [_proposal(n) for n in range(3)])
    page.route('**/api/workflows/studio/incoming?*', lambda route: route.fulfill(json={
        'items': [{'key': f'gmail:{n}', 'source': 'gmail', 'source_thread_id': f'thread-{n}',
                   'source_message_id': f'msg-{n}', 'text': 'Pending message',
                   'processed': False, 'from_me': False} for n in range(7)],
        'links': [], 'counts': {'gmail': 7}, 'channels': {},
    }))
    page.goto(f'{fixture_base}/development/incoming/')

    def counts(n):
        expect(page.locator('#incoming-proposals-list .proposal-card')).to_have_count(n)
        expect(page.locator('#incoming-proposals-count')).to_have_text(str(n))
        expect(page.locator('#sidebar-incoming-count')).to_have_text(str(n))
        expect(page.locator('#incoming-gmail-count')).to_have_text('7')

    counts(3)
    SHOTS.mkdir(parents=True, exist_ok=True)
    if width == 390:
        page.locator('#sidebar-open').click()
        page.wait_for_function("getComputedStyle(document.querySelector('#studio-sidebar')).transform === 'matrix(1, 0, 0, 1, 0, 0)'", timeout=5000)
    page.screenshot(path=str(SHOTS / f'loaded-{width}.png'))
    if width == 390:
        page.locator('#sidebar-close').click()
    page.locator('[data-incoming-proposal-dismiss]').first.click()
    counts(2)
    page.locator('[data-incoming-undo]').click()
    counts(3)
    for n in (2, 1, 0):
        page.locator('[data-incoming-proposal-dismiss]').first.click()
        counts(n)
    assert state['rows'] == []
    expect(page.get_by_text('No proposals waiting for review.', exact=True)).to_be_visible()
    page.screenshot(path=str(SHOTS / f'empty-{width}.png'))


def test_pending_merge_is_counted_and_badge_survives_navigation(page, fixture_base):
    _install_api(page, [])
    page.route('**/api/workflows/studio/incoming/proposals?*', lambda route: route.fulfill(json={
        'proposals': [_proposal(1)],
        'pending_merges': [{'merge_key': 'unfinished-merge', 'title': 'Recover merge'}],
    }))
    page.goto(f'{fixture_base}/development/incoming/')
    expect(page.locator('#incoming-proposals-list .proposal-card')).to_have_count(2)
    expect(page.locator('#incoming-proposals-count')).to_have_text('2')
    expect(page.locator('#sidebar-incoming-count')).to_have_text('2')
    page.locator('#new-thread-button').click()
    expect(page.locator('#incoming-workspace')).to_be_hidden()
    expect(page.locator('#sidebar-incoming-count')).to_have_text('2')
