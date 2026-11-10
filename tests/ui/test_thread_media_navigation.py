"""Media players and proposal-to-thread navigation against the real UI, isolated APIs."""
import io
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit
import wave

import pytest
from playwright.sync_api import Page, expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.core.test_incoming_media import PNG

pytestmark = pytest.mark.e2e_playwright
BASE = os.environ.get('DEVELOPMENT_TEST_URL', 'http://127.0.0.1:8765')


@pytest.fixture(scope='module')
def media_bytes(tmp_path_factory):
    root = tmp_path_factory.mktemp('thread-preview-media')
    video = root / 'preview.mp4'
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'color=c=navy:s=320x180:r=12', '-f', 'lavfi', '-i', 'anullsrc=r=16000:cl=mono',
                    '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                    '-movflags', '+faststart', str(video)], check=True, timeout=30)
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b'\x00\x00' * 32000)
    image = root / 'portrait.png'
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=480x720:rate=1', '-frames:v', '1', str(image)], check=True, timeout=30)
    return {'video': video.read_bytes(), 'audio': audio.getvalue(), 'image': image.read_bytes(),
            'file': b'Source notes for the client request.'}


def install_media(page, media_bytes, *, running=False):
    _install_api(page, [], current_board_linked=True, direct_execution=True,
                 run_status='running' if running else 'completed', terminal_session=False)
    items = []
    for index, (kind, mime, suffix) in enumerate([('video', 'video/mp4', 'mp4'), ('image', 'image/png', 'png'),
                                                ('audio', 'audio/wav', 'wav'), ('file', 'text/plain', 'txt')], 1):
        items.append({'id': f'{index:024x}', 'name': f'AC53A63C850FCDEB7384C53778A68ABD-{kind}.{suffix}',
                      'mime_type': mime, 'kind': kind, 'url': f'/api/workflows/studio/tasks/17/attachments/{index:024x}'})
    chat = {'id': 17, 'title': 'Review supplied media', 'project_id': 7, 'development': True,
            'development_board_key': 'decisions:70', 'development_ticket_id': 42,
            'route_mode': 'auto', 'execution_profile': 'code', 'archived': False, 'pinned': False,
            'created_date': '2026-11-10T07:00:00Z', 'modified_date': '2026-11-10T07:00:00Z',
            'development_execution': {'id': 'direct-91', 'status': 'running' if running else 'completed'},
            'messages': [{'role': 'user', 'content': 'Review these client files and the recorded request.',
                          'attachments': items}]}
    rows = [{**chat, 'id': index, 'title': f'Previous thread {index}',
             'created_date': f'2026-11-{index - 10:02d}T07:00:00Z', 'development_execution': {}}
            for index in range(11, 17)] + [chat]
    calls = []
    state = {'fail': False}

    page.route('**/api/chats/17', lambda route: route.fulfill(json=chat))
    page.route('**/api/chats/17?*', lambda route: route.fulfill(json=chat))
    page.route('**/api/chats', lambda route: route.fulfill(json={'chats': rows, 'last_chat_id': 17}))
    page.route('**/api/chats?*', lambda route: route.fulfill(json={'chats': rows, 'last_chat_id': 17}))

    def source(route):
        path = urlsplit(route.request.url).path
        if path.endswith('/transcript'):
            calls.append(path)
            if state['fail']:
                route.fulfill(status=503, json={'detail': 'Transcription unavailable'})
            else:
                route.fulfill(json={'transcript': 'The client asks where to find the vehicle specifications.', 'status': 'ready'})
            return
        item = next(item for item in items if item['url'] == path)
        route.fulfill(content_type=item['mime_type'], body=media_bytes[item['kind']])

    page.route('**/api/workflows/studio/tasks/17/attachments/**', source)

    def stop(route):
        chat['development_execution']['status'] = 'cancelled'
        route.fallback()

    page.route('**/api/workflows/studio/tasks/17/execution/stop', stop)
    return chat, calls, state


def test_video_and_audio_play_with_transcripts_and_images_use_the_same_modal(page: Page, media_bytes):
    chat, _, _ = install_media(page, media_bytes)
    page.goto(BASE + '/development/threads/17/')
    tiles = page.locator('[data-thread-file-preview]')
    expect(tiles).to_have_count(4)
    expect(tiles.nth(0)).to_have_text('Video')
    expect(tiles.nth(1)).to_have_text('Image')
    expect(tiles.nth(2)).to_have_text('Audio')
    labels = [tiles.nth(index).locator(':scope > span').bounding_box()['y'] for index in range(4)]
    assert max(labels) - min(labels) <= 1
    tiles.nth(0).click()
    modal = page.locator('#incoming-file-preview-modal')
    expect(modal.locator('video')).to_have_js_property('paused', False)
    expect(modal.locator('[data-media-transcript]')).to_contain_text('vehicle specifications')
    page.keyboard.press('Shift+Tab')
    expect(modal.get_by_role('link', name='Download')).to_be_focused()
    page.keyboard.press('Tab')
    expect(modal.locator('[data-file-preview-close]')).to_be_focused()
    page.keyboard.press('Escape')
    expect(modal).to_have_count(0)
    expect(tiles.nth(0)).to_be_focused()
    tiles.nth(0).hover()
    expect(page.get_by_role('tooltip')).to_contain_text('AC53A63')
    expect(page.get_by_role('tooltip')).to_contain_text('vehicle specifications')
    tiles.nth(1).click()
    expect(modal.locator('img')).to_have_js_property('complete', True)
    modal.locator('[data-file-preview-close]').click()
    tiles.nth(2).click()
    expect(modal.locator('audio')).to_have_js_property('paused', False)
    expect(modal.locator('[data-media-transcript]')).to_contain_text('vehicle specifications')
    page.keyboard.press('Escape')
    tiles.nth(3).click()
    expect(modal.locator('[data-file-preview-text]')).to_contain_text('Source notes')
    with page.expect_download() as downloaded:
        modal.get_by_role('link', name='Download').click()
    assert downloaded.value.suggested_filename == chat['messages'][0]['attachments'][3]['name']
    modal.locator('[data-file-preview-close]').click()
    tiles.nth(1).click()
    modal.click(position={'x': 2, 'y': 2})
    expect(modal).to_have_count(0)


def test_transcription_failure_retries_and_reopening_reuses_success(page: Page, media_bytes):
    _, calls, state = install_media(page, media_bytes)
    state['fail'] = True
    page.goto(BASE + '/development/threads/17/')
    page.locator('.thread-media-audio').click()
    modal = page.locator('#incoming-file-preview-modal')
    expect(modal.locator('[data-transcript-retry]')).to_be_visible()
    state['fail'] = False
    modal.locator('[data-transcript-retry]').click()
    expect(modal.locator('[data-media-transcript]')).to_contain_text('vehicle specifications')
    page.keyboard.press('Escape')
    page.locator('.thread-media-audio').click()
    expect(modal.locator('[data-media-transcript]')).to_contain_text('vehicle specifications')
    assert len(calls) == 2


def test_project_defaults_to_four_newest_and_collapsed_running_folder_glows(page: Page, media_bytes):
    install_media(page, media_bytes, running=True)
    page.goto(BASE + '/development/threads/17/')
    group = page.locator('[data-board-group="decisions:70"]')
    expect(group.locator('.task-row')).to_have_count(4)
    expect(group.locator('.task-row').first).to_contain_text('Review supplied media')
    expect(group.locator('.task-row').last).to_contain_text('Previous thread 14')
    group.locator('[data-project-threads-more]').click()
    expect(group.locator('.task-row')).to_have_count(7)
    group.locator('[data-board-toggle]').click()
    expect(group.locator('.task-list')).to_be_hidden()
    glow = group.locator('.folder-running-glow')
    expect(glow).to_be_visible()
    assert glow.evaluate('el => getComputedStyle(el).animationName') == 'project-folder-working'
    page.emulate_media(reduced_motion='reduce')
    assert glow.evaluate('el => getComputedStyle(el).animationName') == 'none'
    group.locator('[data-board-toggle]').click()
    expect(group.locator('.task-row')).to_have_count(4)
    group.locator('[data-board-toggle]').click()
    page.get_by_role('button', name='Stop', exact=True).click()
    page.get_by_role('button', name='Stop agent', exact=True).click()
    expect(group.locator('.folder-running-glow')).to_have_count(0)


def test_incoming_creation_reveals_collapsed_project_and_new_thread_spinner(page: Page, media_bytes):
    install_media(page, media_bytes, running=True)
    page.add_init_script("localStorage.setItem('decisions-development-collapsed-boards', JSON.stringify(['decisions:70']));")
    proposal = {'id': 'media-proposal', 'title': 'Review supplied media', 'project_id': 7,
                'project_name': 'DecisionsAI', 'source': 'gmail', 'description': 'Review the supplied files.'}

    def proposals(route):
        if urlsplit(route.request.url).path.endswith('/thread'):
            route.fulfill(json={'chat_id': 17, 'success': True})
        else:
            route.fulfill(json={'proposals': [proposal]})

    page.route('**/api/workflows/studio/incoming/proposals**', proposals)
    page.goto(BASE + '/development/incoming/')
    page.locator('[data-incoming-proposal-thread]').click()
    expect(page).to_have_url(BASE + '/development/threads/17/')
    group = page.locator('[data-board-group="decisions:70"]')
    expect(group.locator('.task-list')).to_be_visible()
    expect(group.locator('.task-row')).to_have_count(4)
    expect(group.locator('[data-chat-id="17"] .task-run-state')).to_be_visible()


def test_failed_attachment_stays_aligned_and_is_not_a_dead_button(page: Page, media_bytes):
    chat, _, _ = install_media(page, media_bytes)
    chat['messages'][0]['attachments'].append({'name': 'opaque-unavailable.pdf', 'mime_type': 'application/pdf',
                                              'download_error': 'The source file is unavailable.'})
    page.goto(BASE + '/development/threads/17/')
    failed = page.locator('.thread-source-file-failed')
    expect(failed).to_have_text('Unavailable')
    expect(failed).to_have_attribute('role', 'status')
    assert failed.bounding_box()['width'] == 96
    assert failed.bounding_box()['height'] == 96
    expect(failed.locator('button')).to_have_count(0)
