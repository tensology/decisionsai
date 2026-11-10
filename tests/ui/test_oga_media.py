"""OGA with legacy generic metadata must open an audio player and transcribe."""
from pathlib import Path
import subprocess

import pytest
from playwright.sync_api import expect

from tests.ui.test_incoming_dismiss_undo import fixture_base
from tests.ui.test_thread_media_navigation import install_media, media_bytes

pytestmark = pytest.mark.e2e_playwright


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_oga_opens_audio_player_generates_transcript_and_plays(page, fixture_base, media_bytes, tmp_path, width):
    oga = tmp_path / 'voice.oga'
    subprocess.run(['ffmpeg', '-nostdin', '-y', '-loglevel', 'error', '-f', 'lavfi',
                    '-i', 'sine=frequency=440:sample_rate=48000', '-t', '2', '-c:a', 'libopus',
                    str(oga)], check=True, timeout=20)
    chat, calls, _ = install_media(page, media_bytes)
    audio = chat['messages'][0]['attachments'][2]
    audio.update(name='WHATSAPP-VOICE.OGA', mime_type='application/octet-stream', kind='document')
    chat['messages'][0]['attachments'] = [audio]
    page.route(f'**{audio["url"]}', lambda route: route.fulfill(content_type='audio/ogg', body=oga.read_bytes()))
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(f'{fixture_base}/development/threads/17/')
    tile = page.get_by_role('button', name='Open audio: WHATSAPP-VOICE.OGA', exact=True)
    expect(tile).to_have_text('Audio')
    shots = Path('/tmp/oga-media-verify')
    shots.mkdir(exist_ok=True)
    page.screenshot(path=str(shots / f'{width}-tile.png'), full_page=True)
    tile.click()
    modal = page.locator('#incoming-file-preview-modal')
    expect(modal.locator('audio')).to_have_js_property('paused', False)
    expect(modal.locator('audio')).to_have_js_property('readyState', 4)
    expect(modal.locator('[data-media-transcript]')).to_contain_text('vehicle specifications')
    assert len(calls) == 1
    page.screenshot(path=str(shots / f'{width}-player.png'), full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.keyboard.press('Escape')
    expect(tile).to_be_focused()
    tile.click()
    expect(modal.locator('[data-media-transcript]')).to_contain_text('vehicle specifications')
    assert len(calls) == 1
