"""A Gmail attachment shows once in the reader and its PDF preview frame is allowed to load.

2026-11-11 pdf-preview regression: the PDF used to render twice (per-message card list plus the
bottom attachment bar) and the preview iframe was blocked by X-Frame-Options: DENY.
"""

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_incoming_dismiss_undo import fixture_base  # noqa: F401

pytestmark = pytest.mark.e2e_playwright

STAND_IN = b'<!doctype html><p>Preview stand-in</p>'
NAME = 'Crystal_Logic_Service_Level_Agreement_Draft_v0.6.pdf'
PREVIEW = f'/api/workflows/studio/incoming/mail/attachment?message_id=crystal&attachment_id=ANG-1&filename={NAME}'


@pytest.mark.parametrize('frame_headers,blocked', [
    ({'X-Frame-Options': 'SAMEORIGIN', 'Content-Security-Policy': "default-src 'self'; frame-ancestors 'self';"}, False),
    ({'X-Frame-Options': 'DENY', 'Content-Security-Policy': "default-src 'self'; frame-ancestors 'none';"}, True),
])
def test_pdf_attachment_renders_once_and_preview_frame_loads(page, fixture_base, frame_headers, blocked):
    requests = []
    _install_api(page, requests)
    item = {
        'key': 'gmail:crystal', 'source': 'gmail', 'source_thread_id': 'crystal', 'source_message_id': 'crystal',
        'conversation_label': 'Proposed support agreement', 'subject': 'Proposed support agreement',
        'sender': 'Paul <paul@example.com>', 'recipient': 'roland@example.com', 'created_at': '2026-11-11T13:06:18Z',
        'text': 'I have attached a draft agreement.', 'html_body': '<p>I have attached a draft agreement.</p>',
        'attachments': [{'filename': NAME, 'mime_type': 'application/pdf', 'size': 91370, 'attachment_id': 'ANG-1',
                         'content_id': '', 'inline': False, 'message_id': 'crystal', 'preview_url': PREVIEW}],
    }
    page.route('**/api/workflows/studio/incoming?*', lambda route: route.fulfill(json={'items': [item], 'channels': {'gmail': {'connected': True}}}))
    served = []
    page.route('**/api/workflows/studio/incoming/mail/attachment?*', lambda route: (served.append(route.request.url), route.fulfill(
        body=STAND_IN, headers={'Content-Type': 'text/html', 'Content-Disposition': f'inline; filename="{NAME}"', **frame_headers})))
    console = []
    page.on('console', lambda message: console.append(message.text))
    page.set_viewport_size({'width': 1440, 'height': 900})
    page.goto(f'{fixture_base}/development/incoming/')
    page.get_by_role('tab', name='Gmail').click()
    panel = page.locator('#incoming-panel-gmail')
    panel.locator('[data-incoming-gmail-thread="gmail:crystal"]').click()

    expect(panel.locator('.incoming-gmail-message')).to_have_count(1)
    # Exactly one entry for the PDF: the bottom bar, not a second card under the message text.
    expect(panel.locator('.incoming-mail-attachments, .incoming-mail-attachment')).to_have_count(0)
    chips = panel.locator('.incoming-mail-attach-chip')
    expect(chips).to_have_count(1)
    expect(chips).to_have_attribute('title', NAME)
    expect(chips.locator('svg.file-type-icon')).to_have_attribute('data-file-kind', 'pdf')

    chips.click()
    frame = page.locator('#incoming-file-preview-modal iframe.file-preview-frame')
    expect(frame).to_have_attribute('src', PREVIEW)
    # The PDF viewer's own toolbar is the only download; the title is icon + full file name, close stays.
    modal = page.locator('#incoming-file-preview-modal')
    expect(modal.locator('header h3 .file-preview-title-name')).to_have_text(NAME)
    expect(modal.locator('header h3 svg.file-type-icon')).to_have_attribute('data-file-kind', 'pdf')
    expect(modal.get_by_role('link', name='Download')).to_have_count(0)
    expect(modal.locator('footer')).to_have_count(0)
    expect(modal.get_by_role('button', name='Close')).to_be_visible()
    page.wait_for_timeout(800)
    assert served, 'preview frame never requested the attachment'
    # Headless Chromium has no PDF viewer, so the stand-in body is HTML; framing rules are the same.
    loaded = frame.element_handle().content_frame()
    text = loaded.evaluate("() => document.body ? document.body.innerText : ''") if loaded else ''
    assert ('Preview stand-in' in text) is (not blocked), (text, loaded.url if loaded else None, console)


def test_image_attachment_preview_keeps_download(page, fixture_base):
    _install_api(page, [])
    url = '/api/workflows/studio/incoming/mail/attachment?message_id=img&attachment_id=IMG-1&filename=photo.png'
    item = {
        'key': 'gmail:img', 'source': 'gmail', 'source_thread_id': 'img', 'source_message_id': 'img',
        'conversation_label': 'Site photo', 'subject': 'Site photo', 'sender': 'a@example.com',
        'created_at': '2026-11-11T13:06:18Z', 'text': 'Photo attached.',
        'attachments': [{'filename': 'photo.png', 'mime_type': 'image/png', 'attachment_id': 'IMG-1',
                         'message_id': 'img', 'preview_url': url}],
    }
    png = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000'
                        '1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082')
    page.route('**/api/workflows/studio/incoming?*', lambda route: route.fulfill(json={'items': [item], 'channels': {'gmail': {'connected': True}}}))
    page.route('**/api/workflows/studio/incoming/mail/attachment?*', lambda route: route.fulfill(body=png, headers={'Content-Type': 'image/png'}))
    page.goto(f'{fixture_base}/development/incoming/')
    page.get_by_role('tab', name='Gmail').click()
    panel = page.locator('#incoming-panel-gmail')
    panel.locator('[data-incoming-gmail-thread="gmail:img"]').click()
    panel.locator('.incoming-mail-attach-chip').click()
    modal = page.locator('#incoming-file-preview-modal')
    expect(modal.locator('img.file-preview-image')).to_be_visible()
    expect(modal.get_by_role('link', name='Download')).to_have_attribute('download', 'photo.png')


LONG_NAMES = [
    'Crystal_Logic_Service_Level_Agreement_Draft_v0.6_with_appendices_and_signature_pages_FINAL.pdf',
    'Quarterly financial model with very long descriptive name for the board pack.xlsx',
    'Proposal deck – Tensology x Crystal Logic onboarding and support rollout plan.pptx',
    'Meeting notes from the 11 November workshop including every action item.docx',
    'site-photo-from-the-server-room-rack-layout-overview.jpg', 'voice-note-from-roland.m4a',
    'walkthrough-recording-of-the-support-portal.mp4', 'logs-and-config-bundle-for-diagnostics.zip',
    'readme-for-the-integration-scripts.txt', 'export_script_for_reports.py', 'unknown-binary-blob.bin',
    'Second_copy_of_the_service_level_agreement_for_review_purposes_only.pdf',
]
KINDS = ['pdf', 'sheet', 'slides', 'word', 'image', 'audio', 'video', 'archive', 'text', 'code', 'file', 'pdf']


@pytest.mark.parametrize('source,tab,mid_prefix', [('gmail', 'Gmail', ''), ('mailshot', 'Mailshot', 'email-')])
def test_many_long_attachment_names_show_in_full_and_scroll(page, fixture_base, source, tab, mid_prefix):
    _install_api(page, [])
    mid = f'{mid_prefix}many'
    attachments = [{'filename': name, 'mime_type': '', 'attachment_id': f'A{i}', 'message_id': mid,
                    'preview_url': f'/api/workflows/studio/incoming/mail/attachment?message_id={mid}&attachment_id=A{i}'}
                   for i, name in enumerate(LONG_NAMES)]
    item = {'key': f'{source}:many', 'source': source, 'source_thread_id': 'many', 'source_message_id': mid,
            'conversation_label': 'Many files', 'subject': 'Many files', 'sender': 'a@example.com',
            'created_at': '2026-11-11T13:06:18Z', 'text': 'Files attached.', 'attachments': attachments}
    page.route('**/api/workflows/studio/incoming?*', lambda route: route.fulfill(json={'items': [item], 'channels': {source: {'connected': True}}}))
    page.route('**/api/workflows/studio/incoming/mail/attachment?*', lambda route: route.fulfill(body=STAND_IN, headers={'Content-Type': 'text/html'}))
    page.set_viewport_size({'width': 1440, 'height': 900})
    page.goto(f'{fixture_base}/development/incoming/')
    page.get_by_role('tab', name=tab).click()
    panel = page.locator(f'#incoming-panel-{source}')
    panel.locator(f'[data-incoming-{source}-thread="{source}:many"]').click()
    chips = panel.locator('.incoming-mail-attach-chip')
    expect(chips).to_have_count(len(LONG_NAMES))
    kinds = chips.evaluate_all("n => n.map(c => c.querySelector('svg.file-type-icon')?.dataset.fileKind || (c.querySelector('img') ? 'thumb' : ''))")
    assert kinds == [('thumb' if k == 'image' else k) for k in KINDS]
    names = panel.locator('.incoming-mail-attach-name')
    assert names.all_inner_texts() == LONG_NAMES
    clipped = names.evaluate_all("n => n.filter(x => x.scrollWidth > x.clientWidth + 1 || getComputedStyle(x).textOverflow === 'ellipsis').length")
    assert clipped == 0
    track = panel.locator('.incoming-mail-attachment-bar-track')
    geometry = track.evaluate("t => ({sw: t.scrollWidth, cw: t.clientWidth, ox: getComputedStyle(t).overflowX, h: t.clientHeight,"
                              " rows: new Set([...t.children].map(c => Math.round(c.getBoundingClientRect().top))).size})")
    assert geometry['ox'] == 'auto' and geometry['sw'] > geometry['cw'], geometry
    assert geometry['rows'] == 3 and geometry['h'] <= 260, geometry
    chips.last.scroll_into_view_if_needed()
    expect(chips.last).to_be_in_viewport()
    chips.nth(1).click()
    modal = page.locator('#incoming-file-preview-modal')
    expect(modal.locator('header h3 .file-preview-title-name')).to_have_text(LONG_NAMES[1])
    expect(modal.locator('header h3 svg.file-type-icon')).to_have_attribute('data-file-kind', 'sheet')
    title = modal.locator('header h3 .file-preview-title-name')
    assert title.evaluate('x => x.scrollWidth <= x.clientWidth + 1')
    expect(modal.get_by_role('button', name='Close')).to_be_in_viewport()
