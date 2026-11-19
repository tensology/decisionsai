"""Copy each WhatsApp message without sender metadata or neighbouring content."""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_incoming_dismiss_undo import fixture_base

pytestmark = pytest.mark.e2e_playwright
SHOTS = Path('/tmp/decisions-whatsapp-copy')


def _item(n, text, **extra):
    return {'key': f'whatsapp:{n}', 'database_id': n, 'source': 'whatsapp',
            'source_thread_id': 'copy@g.us', 'source_message_id': f'message-{n}',
            'conversation_label': 'Project team', 'chat_type': 'group',
            'sender': f'Participant {n}', 'text': text, 'from_me': False,
            'created_at': f'2026-11-18T09:00:{n:02d}Z', **extra}


def _open(page, fixture_base, rows):
    _install_api(page, [])
    page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    page.route('**/api/workflows/studio/incoming?*', lambda route: route.fulfill(json={
        'items': rows, 'links': [], 'channels': {},
    }))
    page.route('**/api/workflows/studio/incoming/proposals?*', lambda route: route.fulfill(json={'proposals': []}))
    page.route('**/api/workflows/studio/incoming/whatsapp/history?*', lambda route: route.fulfill(json={
        'items': [_item(1, 'Older message\nKept exactly.')], 'has_older': False,
    }))
    page.goto(f'{fixture_base}/development/incoming/')
    page.locator('#incoming-folder-whatsapp').click()
    page.locator('[data-incoming-conversation="whatsapp:copy@g.us"]').click()
    SHOTS.mkdir(parents=True, exist_ok=True)
    return page.locator('#incoming-whatsapp-reader')


def _copy(page, reader, n, text, *, keyboard=False):
    button = reader.locator(f'[data-wa-mid="message-{n}"]').get_by_role('button', name='Copy message', exact=True)
    if keyboard:
        button.focus()
        page.keyboard.press('Enter')
    else:
        button.click()
    expect(page.locator('#studio-toast')).to_have_text('Message copied.')
    assert page.evaluate('navigator.clipboard.readText()') == text
    expect(button).to_be_enabled()
    return button


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_copy_every_participant_and_own_message_and_history(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 1000})
    rows = [_item(2, 'Please check <delivery> & "pricing".\nKeep this second line.'),
            _item(3, 'I will check it.', from_me=True),
            _item(4, 'Thanks, please include the receipt.')]
    reader = _open(page, fixture_base, rows)
    expect(reader.get_by_role('button', name='Copy message', exact=True)).to_have_count(3)
    for row in rows:
        button = _copy(page, reader, row['database_id'], row['text'], keyboard=row['from_me'])
        box = button.bounding_box()
        assert box['width'] >= 24 and box['height'] >= 24
        bubble = button.locator('xpath=ancestor::div[contains(@class,"incoming-message-bubble")]')
        body = bubble.locator(':scope > p').bounding_box()
        assert box['y'] >= body['y'] + body['height']
    reader.get_by_role('button', name='Load older messages').click()
    _copy(page, reader, 1, 'Older message\nKept exactly.', keyboard=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(SHOTS / f'messages-{width}.png'), animations='disabled')


def test_caption_transcript_empty_media_and_clipboard_failure(page, fixture_base):
    page.set_viewport_size({'width': 1280, 'height': 1000})
    reader = _open(page, fixture_base, [
        _item(2, 'Invoice attached.', media_type='document', media_filename='invoice.pdf'),
        _item(3, '[Transcription] Please update the delivery charge.', media_type='voice'),
        _item(4, '', media_type='document', media_filename='receipt.pdf'),
    ])
    _copy(page, reader, 2, 'Invoice attached.')
    _copy(page, reader, 3, 'Please update the delivery charge.')
    expect(reader.locator('[data-wa-mid="message-4"] [data-wa-message-copy]')).to_be_disabled()
    page.screenshot(path=str(SHOTS / 'media.png'), animations='disabled')
    page.evaluate("""() => Object.defineProperty(navigator.clipboard, 'writeText', {
        configurable: true, value: async () => { throw new Error('Permission denied'); }
    })""")
    button = reader.locator('[data-wa-mid="message-2"] [data-wa-message-copy]')
    button.click()
    expect(page.locator('#studio-toast')).to_contain_text('Could not copy the message.')
    expect(button).to_be_enabled()
    expect(reader.locator('[data-wa-mid="message-2"]')).to_contain_text('Invoice attached.')
    page.screenshot(path=str(SHOTS / 'clipboard-error.png'), animations='disabled')
