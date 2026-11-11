"""Verify Incoming conversation resync using the real browser UI."""

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_incoming_dismiss_undo import fixture_base

pytestmark = pytest.mark.e2e_playwright


@pytest.mark.parametrize('width', [390, 1440])
def test_resync_reloads_the_selected_conversation(page, fixture_base, tmp_path, width):
    requests = []
    _install_api(page, requests)
    items = []
    for source, title in [('whatsapp', 'Linked WhatsApp group'), ('gmail', 'Gmail launch review')]:
        for thread, label in [('first', 'First conversation'), ('selected', title)]:
            items.append({
                'key': f'{source}:{thread}', 'source': source, 'source_thread_id': thread,
                'source_message_id': thread, 'conversation_label': label, 'subject': label,
                'text': f'Original {source} {thread}', 'sender': 'Test sender',
                'created_at': '2026-11-10T09:00:00Z' if thread == 'first' else '2026-11-10T08:00:00Z',
                'board_id': 70 if thread == 'selected' else None,
                'links': [{'id': 5, 'board_id': 70, 'board_name': 'Menu Project'}] if thread == 'selected' else [],
            })
    state = {'relay_synced': False, 'gmail_new': False, 'gmail_error': False, 'relay_error': False}
    channel_reads = []

    def relay(route):
        assert route.request.method == 'POST'
        if state['relay_error']:
            return route.fulfill(status=503, json={'error': 'WhatsApp relay unavailable.'})
        state['relay_synced'] = True
        route.fulfill(json={'synced': 1})

    def channels(route):
        channel_reads.append(state['relay_synced'])
        fresh = [
            {**item, 'key': item['key'] + '-new', 'source_message_id': 'new',
             'created_at': '2026-11-11T08:00:00Z', 'text': f'Fresh {item["source"]} message'}
            for item in items
            if item['source_thread_id'] == 'selected' and (
                (item['source'] == 'whatsapp' and state['relay_synced'])
                or (item['source'] == 'gmail' and state['gmail_new'])
            )
        ]
        route.fulfill(json={'items': items + fresh, 'channels': {'gmail': {
            'connected': not state['gmail_error'],
            'error': 'Gmail is temporarily unavailable.' if state['gmail_error'] else '',
        }}})

    page.route('**/api/tickets/whatsapp/sync', relay)
    page.route('**/api/workflows/studio/incoming?*', channels)
    page.add_init_script("""(() => {
        const fetchOriginal = window.fetch;
        window.fetch = async (url, options) => {
            if (window.pauseResync && (String(url).endsWith('/tickets/whatsapp/sync')
                || (String(url).includes('/studio/incoming?') && options?.cache === 'no-store'))) {
                await new Promise(resolve => { window.continueResync = resolve; });
                window.pauseResync = false;
            }
            return fetchOriginal(url, options);
        };
    })()""")
    page.set_viewport_size({'width': width, 'height': 850})
    page.goto(f'{fixture_base}/development/incoming/')
    expect(page.locator('#incoming-whatsapp-count')).to_have_text('2')

    for source, name in [('whatsapp', 'WhatsApp'), ('gmail', 'Gmail')]:
        page.get_by_role('tab', name=name).click()
        panel = page.locator(f'#incoming-panel-{source}')
        row = panel.locator('[data-incoming-conversation="whatsapp:selected"]' if source == 'whatsapp'
                            else '[data-incoming-gmail-thread="gmail:selected"]')
        row.click()
        if width == 390:
            panel.get_by_role('button', name='Back to conversations').click()
        button = panel.get_by_role('button', name=f'Resync {name}', exact=True)
        status = panel.locator(f'#incoming-{source}-resync-status')
        expect(button).to_be_visible()
        page.evaluate('window.pauseResync = true')
        state['gmail_new'] = source == 'gmail'
        button.click()
        expect(button).to_be_disabled()
        expect(button).to_have_text('↻Syncing...')
        page.screenshot(path=tmp_path / f'{source}-syncing.png')
        page.evaluate('window.continueResync()')
        expect(button).to_be_enabled()
        expect(status).to_have_text(f'{name} conversations refreshed.')
        expect(row).to_contain_text(f'Fresh {source} message')
        page.screenshot(path=tmp_path / f'{source}-list.png')
        if width == 390:
            row.click()
        expect(panel.locator('article').get_by_text(f'Fresh {source} message', exact=True)).to_be_visible()
        if width == 390:
            page.screenshot(path=tmp_path / f'{source}-reader.png')
            panel.get_by_role('button', name='Back to conversations').click()
        state['relay_error' if source == 'whatsapp' else 'gmail_error'] = True
        button.click()
        expect(status).to_have_text(
            'WhatsApp relay unavailable.' if source == 'whatsapp' else 'Gmail is temporarily unavailable.'
        )
        expect(row).to_contain_text(f'Fresh {source} message')
        expect(button).to_be_enabled()
        page.screenshot(path=tmp_path / f'{source}-error.png')

    assert channel_reads[0] is False and channel_reads[-1] is True
    assert not any(path.endswith('/incoming/check') for _, path in requests)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_mailshot_resync_distinguishes_outage_from_empty_inbox(page, fixture_base, tmp_path, width):
    requests = []
    _install_api(page, requests)
    state = {'error_code': 'refresh_failed'}
    errors = {
        'refresh_failed': 'Mailshot could not be refreshed. Try again.',
        'connection_required': 'Reconnect Tensology in Settings to load Mailshot.',
    }

    def channels(route):
        code = state['error_code']
        route.fulfill(json={'items': [], 'channels': {'mailshot': {
            'connected': not code, 'error': errors.get(code, ''), 'error_code': code,
        }}})

    page.route('**/api/workflows/studio/incoming?*', channels)
    page.set_viewport_size({'width': width, 'height': 850})
    page.goto(f'{fixture_base}/development/incoming/')
    tab = page.get_by_role('tab', name='Mailshot')
    tab.click()
    expect(tab).to_have_attribute('aria-selected', 'true')
    panel = page.get_by_role('tabpanel', name='Mailshot')
    retry = panel.get_by_role('button', name='Resync Mailshot')
    empty = panel.get_by_text('No Tensology Mailshot messages are available.')
    settings = panel.get_by_role('link', name='Open Settings')

    expect(panel.get_by_text(errors['refresh_failed'])).to_be_visible()
    expect(empty).to_have_count(0)
    expect(settings).to_have_count(0)
    page.screenshot(path=tmp_path / 'mailshot-error.png', animations='disabled')

    state['error_code'] = 'connection_required'
    retry.click()
    expect(panel.get_by_text(errors['connection_required'])).to_be_visible()
    expect(settings).to_have_attribute('href', '/settings#advanced')
    page.screenshot(path=tmp_path / 'mailshot-reconnect.png', animations='disabled')

    state['error_code'] = ''
    retry.click()
    expect(panel.get_by_text('Mailshot conversations refreshed.')).to_be_visible()
    expect(empty).to_be_visible()
    expect(settings).to_have_count(0)
    expect(panel.get_by_text('Select a conversation', exact=True)).to_have_count(0)
    expect(retry).to_be_enabled()
    page.screenshot(path=tmp_path / 'mailshot-empty.png', animations='disabled')
    assert not any(path.endswith('/incoming/check') for _, path in requests)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
