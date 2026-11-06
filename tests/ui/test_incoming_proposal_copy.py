"""Proposal copy uses full content, shared feedback, and the existing action row."""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_incoming_dismiss_undo import _mock, _proposal, fixture_base

pytestmark = pytest.mark.e2e_playwright
SHOTS = Path('/tmp/decisions-proposal-copy')


def _open(page, fixture_base):
    proposal = _proposal(1)
    proposal['title'] = 'Investigate D3/DPP sync failure for active jobs'
    proposal['description'] = ('Full description with <markup> & special characters.\n' * 8).strip()
    _install_api(page, [])
    _mock(page, [proposal])
    page.goto(f'{fixture_base}/development/incoming/')
    expect(page.get_by_role('button', name='Copy title and description')).to_be_visible()
    return proposal


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_copy_full_proposal_and_action_alignment(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 900})
    page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    proposal = _open(page, fixture_base)
    card = page.locator('.proposal-card')
    copy = card.get_by_role('button', name='Copy title and description')
    copy.click()
    expected = proposal['title'] + '\n\n' + proposal['description']
    assert page.evaluate('navigator.clipboard.readText()') == expected
    expect(page.locator('#studio-toast')).to_have_text('Proposal title and description copied.')
    expect(page.locator('#studio-toast')).to_be_visible()
    expect(page.locator('#incoming-proposal-modal')).to_have_count(0)
    assert card.count() == 1
    boxes = [card.locator(selector).bounding_box() for selector in
             ['.proposal-btn.primary', '.proposal-btn.ghost', '.proposal-drag', '.proposal-copy']]
    centers = [box['y'] + box['height'] / 2 for box in boxes]
    assert max(centers) - min(centers) < 1
    assert boxes[2]['x'] > boxes[3]['x'] + boxes[3]['width']
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    # Native keyboard activation must copy, without opening the card's evidence view.
    copy.focus()
    page.keyboard.press('Enter')
    page.keyboard.press('Space')
    expect(page.locator('#incoming-proposal-modal')).to_have_count(0)
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f'copy-{width}.png'))
    expect(page.locator('#studio-toast')).to_be_hidden(timeout=5000)


def test_copy_failure_reports_error_and_preserves_card(page, fixture_base):
    page.set_viewport_size({'width': 1280, 'height': 900})
    _open(page, fixture_base)
    page.evaluate("""() => Object.defineProperty(navigator.clipboard, 'writeText', {
        configurable: true, value: async () => { throw new Error('Permission denied'); }
    })""")
    page.get_by_role('button', name='Copy title and description').click()
    toast = page.locator('#studio-toast')
    expect(toast).to_contain_text('Could not copy the proposal.')
    expect(toast).to_have_class('studio-toast error')
    expect(page.locator('.proposal-card')).to_have_count(1)
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / 'copy-error.png'))
