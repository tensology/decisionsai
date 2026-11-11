"""Check that Source and the WhatsApp markers identify the same request."""

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_incoming_dismiss_undo import fixture_base

pytestmark = pytest.mark.e2e_playwright


@pytest.mark.parametrize('width', [390, 1440])
def test_source_scope_and_full_project_badge(page, fixture_base, tmp_path, width):
    _install_api(page, [])
    project = 'AW Hypnotherapy'
    thread = '12345@s.whatsapp.net'
    rows = [
        {'id': 'old', 'body': 'Unrelated chat history', 'sent_at': '2026-11-09T08:00:00Z'},
        {'id': 'request', 'body': 'Please pull my current Git version.', 'sent_at': '2026-11-11T10:25:00Z', 'sender_name': 'Test contact'},
    ]
    proposal = {'id': 'test-proposal', 'source': 'whatsapp', 'source_thread_id': thread,
                'title': 'Retrieve current Git version', 'project_id': 7, 'project_name': project,
                'message_ids': ['request'], 'source_context_message_ids': ['old', 'request'], 'evidence': rows}
    page.route('**/api/workflows/studio/incoming/proposals?*',
               lambda route: route.fulfill(json={'proposals': [proposal]}))
    page.route('**/api/workflows/studio/incoming/proposals/files/ensure',
               lambda route: route.fulfill(json={'success': True, 'attachments': []}))
    page.route('**/api/workflows/studio/incoming?*', lambda route: route.fulfill(json={
        'items': [{
            'key': 'whatsapp:' + row['id'], 'source': 'whatsapp', 'source_thread_id': thread,
            'source_message_id': row['id'], 'text': row['body'], 'created_at': row['sent_at'],
            'conversation_label': 'Test contact', 'sender': 'Test contact',
            'links': [{'id': 1, 'board_id': 70, 'board_name': project}],
        } for row in rows], 'links': [],
    }))
    page.set_viewport_size({'width': width, 'height': 850})
    page.goto(f'{fixture_base}/development/incoming/')
    page.get_by_text('Retrieve current Git version', exact=True).click()
    source = page.get_by_role('tab', name='Source 1')
    source.click()
    expect(source).to_have_attribute('aria-selected', 'true')
    dialog = page.get_by_role('dialog')
    expect(dialog.locator('.proposal-message')).to_have_count(1)
    expect(dialog.locator('.proposal-message')).to_contain_text(rows[1]['body'])
    expect(dialog.get_by_text(rows[0]['body'])).to_have_count(0)
    page.screenshot(path=tmp_path / 'proposal-source.png', animations='disabled')
    dialog.get_by_role('button', name='View in conversation').click()
    reader = page.locator('#incoming-whatsapp-reader')
    expect(reader.locator('[data-proposal-marker="start"] + [data-wa-mid="request"]')).to_be_visible()
    expect(reader.locator('[data-wa-mid="request"] + [data-proposal-marker="end"]')).to_be_visible()
    expect(reader.get_by_text(rows[0]['body'])).to_be_visible()
    page.screenshot(path=tmp_path / 'whatsapp-request.png', animations='disabled')
    if width == 390:
        page.get_by_role('button', name='Back to conversations').click()
    badge = page.locator('#incoming-list .incoming-board-pill')
    expect(badge).to_have_text(project)
    assert badge.evaluate('node => node.scrollWidth <= node.clientWidth')
    expect(badge).to_have_css('text-overflow', 'clip')
    page.screenshot(path=tmp_path / 'project-badge.png', animations='disabled')
