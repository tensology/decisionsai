"""A served profile image replaces initials; unavailable photos retain initials."""
import io
from urllib.parse import parse_qs, urlparse
import pytest
from PIL import Image
from playwright.sync_api import expect
from tests.ui.test_development_responsive import BASE, install_responsive_api, capture

pytestmark = pytest.mark.e2e_playwright


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_contact_group_photo_and_private_fallback(page, width):
    install_responsive_api(page)
    # Deliberately synthetic checkerboard to test rendering, not a claimed live photo.
    image = Image.new('RGB', (80, 80), '#f2b66d')
    for x in range(40):
        for y in range(40): image.putpixel((x, y), (36, 133, 124))
    data = io.BytesIO(); image.save(data, format='PNG')
    jids = ['27820000000@s.whatsapp.net', '120363000000@g.us', '27820000001@s.whatsapp.net']
    labels = ['Contact photo test', 'Group photo test', 'Private photo test']
    page.route('**/api/workflows/studio/incoming?*', lambda r: r.fulfill(json={'items': [
        {'source': 'whatsapp', 'source_thread_id': jid, 'key': f'whatsapp:{jid}',
         'conversation_label': label, 'sender': label, 'text': 'Synthetic profile picture verification',
         'chat_type': 'group' if '@g.us' in jid else 'contact', 'created_at': '2026-10-30T10:00:00Z'}
        for jid, label in zip(jids, labels)
    ], 'links': [], 'channels': {}, 'counts': {'whatsapp': 3}}))
    page.route('**/api/workflows/studio/incoming/proposals*', lambda r: r.fulfill(json={'proposals': []}))
    seen = []
    def photo(route):
        jid = parse_qs(urlparse(route.request.url).query)['jid'][0]; seen.append(jid)
        if jid == jids[2]: route.fulfill(status=404)
        else: route.fulfill(body=data.getvalue(), content_type='image/png')
    page.route('**/api/tickets/whatsapp/profile-picture?*', photo)
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(BASE+'/development/incoming/')
    page.locator('#incoming-folder-whatsapp').click()
    expect(page.locator('#incoming-list img.loaded')).to_have_count(2)
    private = page.locator(f'[data-incoming-conversation="whatsapp:{jids[2]}"] .incoming-avatar')
    expect(private).to_have_text('PP')
    expect(private.locator('img')).to_have_count(0)
    capture(page, 'incoming-profile-list', width)
    for number, jid in enumerate(jids):
        page.locator(f'[data-incoming-conversation="whatsapp:{jid}"]').click()
        header = page.locator('.incoming-whatsapp-reader-header .incoming-avatar')
        if number < 2:
            expect(header.locator('img.loaded')).to_have_count(1)
            assert header.locator('img').evaluate('(img)=>img.naturalWidth') == 80
        else:
            expect(header.locator('img')).to_have_count(0)
            expect(header).to_have_text('PP')
        capture(page, f'incoming-profile-reader-{number}', width)
        if width <= 980: page.locator('[data-incoming-back]').filter(visible=True).first.click()
    assert set(jids) <= set(seen)
