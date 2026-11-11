"""Real composer interactions with isolated, persistent queue API fixtures."""
import base64
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from tests.ui.test_incoming_dismiss_undo import fixture_base
from tests.ui.test_development_harness_playwright import _install_api

pytestmark = pytest.mark.e2e_playwright
SHOTS = Path('/tmp/queued-submissions-verify')
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


def install(page):
    requests = []
    _install_api(page, requests, direct_execution=True, run_status='running', current_board_linked=True)
    files = [{'name': name, 'path': f'/tmp/decisions/{name}', 'mime_type': mime, 'size': 50}
             for name, mime in [('reference.png', 'image/png'), ('voice.ogg', 'audio/ogg'), ('walkthrough.mp4', 'video/mp4'), ('notes.txt', 'text/plain')]]
    files[1]['transcript'] = 'Recording transcript retained with its audio file.'
    rows = [{'id': 61, 'chat_id': 17, 'command_type': 'followup', 'status': 'queued', 'content': 'Review these files\nFull transcription', 'modified_at': '1',
             'metadata': {'attachments': files, 'composer': {'text': 'Review these files', 'attachments': [
                 *[{'kind': 'file', 'key': file['path'], 'label': file['name'], 'reference': file['path'], 'mime_type': file['mime_type'], 'size': file['size'], 'transcript': file.get('transcript', '')} for file in files],
                 {'kind': 'context', 'key': 'transcript', 'label': 'Transcription', 'text': 'Full transcription of the request.'}], 'use_playwright': False}}}]
    state = {'rows': rows, 'runs': [], 'posts': [], 'fail': False, 'patches': []}

    def api(route):
        path, method = urlparse(route.request.url).path, route.request.method
        payload = None
        if path == '/api/workflows/studio/control-state':
            payload = {'commands': rows, 'interactions': [], 'channels': {}, 'controls': {}}
        elif path == '/api/chats/18':
            payload = {'id': 18, 'title': 'Forked queued request', 'project_id': 7, 'development': True,
                       'route_mode': 'auto', 'execution_profile': 'code', 'autonomy_level': 'full',
                       'messages': [{'role': 'user', 'content': 'Review these files'}]}
        elif path in ['/api/workflows/studio/tasks/18/artifacts', '/api/workflows/studio/tasks/18/plans']:
            payload = {'items': []}
        elif path == '/api/workflows/studio/tasks/18/execution':
            payload = {'direct': True, 'status': 'running', 'chat_id': 18}
        elif path == '/api/workflows/studio/tasks/18/time':
            payload = {'seconds': 0, 'running': True}
        elif path == '/api/workflows/studio/attachments':
            payload = {'name': 'replacement.md', 'path': '/tmp/decisions/replacement.md', 'mime_type': 'text/markdown', 'size': 17}
        elif path == '/api/workflows/studio/tasks/17/commands' and method == 'POST':
            body = route.request.post_data_json
            state['posts'].append(body)
            row = {**body, 'id': 62 + len(state['posts']), 'chat_id': 17, 'status': 'queued', 'modified_at': '1'}
            rows.append(row)
            payload = {'status': 'queued', 'artifacts': [row]}
        elif '/api/workflows/studio/commands/' in path:
            command_id = int(path.split('/')[5])
            row = next(item for item in rows if item['id'] == command_id)
            if '/attachments/' in path:
                route.fulfill(status=200, content_type='image/png', body=PNG)
                return
            body = route.request.post_data_json
            if state['fail']:
                route.fulfill(status=409, json={'detail': 'The worker is still stopping. Your item remains queued.'})
                return
            if path.endswith('/run'):
                state['runs'].append(body)
                if state.get('hold_run'):
                    state['pending_run'] = (route, row)
                    return
                row['status'] = 'delivered'
                payload = {'chat_id': state.get('new_thread_id', 17) if body.get('new_thread') else 17, 'started': True}
            else:
                state['patches'].append(body)
                row.update({key: body[key] for key in ['content', 'metadata'] if key in body})
                row['status'] = 'cancelled' if body.get('cancel') else 'editing' if body.get('editing') else 'queued'
                row['modified_at'] = str(int(row['modified_at']) + 1)
                payload = row
        if payload is None:
            route.fallback()
        else:
            route.fulfill(status=200, json=payload)
    page.route('**/api/**', api)
    return state, requests


def open_queue(page, fixture_base, width=1280):
    page.set_viewport_size({'width': width, 'height': 1000})
    state, requests = install(page)
    page.goto(f'{fixture_base}/development/threads/17/')
    expect(page.locator('#thread-command-queue')).to_be_visible()
    expect(page.locator('[data-queued-id="61"]')).to_contain_text('Review these files')
    return state, requests


def shot(page, name):
    SHOTS.mkdir(exist_ok=True)
    page.screenshot(path=str(SHOTS / f'{name}.png'), full_page=True)


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_stale_view_conflict_preserves_the_composer_draft(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 920})
    requests = []
    _install_api(page, requests, direct_execution=True, current_board_linked=True)
    submissions = []
    queued = []
    refreshed = []
    rows = []

    def reject_overlap(route):
        submissions.append(route.request.post_data_json['message'])
        route.fulfill(status=409, json={
            'code': 'development_execution_busy',
            'detail': 'Work is already running in this Development thread.',
        })

    def read_execution(route):
        if not submissions:
            return route.fallback()
        refreshed.append(True)
        route.fulfill(json={'id': 'original-worker', 'direct': True, 'status': 'running'})

    def queue_followup(route):
        body = route.request.post_data_json
        queued.append(body['content'])
        rows.append({**body, 'id': 62, 'chat_id': 17, 'status': 'queued', 'modified_at': '1'})
        route.fulfill(json={'status': 'queued', 'summary': 'Instruction queued.', 'artifacts': rows})

    page.route('**/api/workflows/studio/tasks/17/messages', reject_overlap)
    page.route('**/api/workflows/studio/tasks/17/execution', read_execution)
    page.route('**/api/workflows/studio/tasks/17/commands', queue_followup)
    page.route(re.compile(r'/api/workflows/studio/control-state(?:\?|$)'), lambda route: route.fulfill(
        json={'commands': rows, 'interactions': [], 'channels': {}, 'controls': {}},
    ))
    page.goto(f'{fixture_base}/development/threads/17/')
    prompt = page.locator('#task-prompt')
    prompt.fill('Check the remaining configuration values')
    page.locator('#send-button').click()
    toast = page.locator('#studio-toast')
    expect(toast).to_contain_text('Work is already running in this Development thread.')
    expect(prompt).to_have_value('Check the remaining configuration values')
    expect(page.locator('#send-button')).to_be_enabled()
    assert submissions == ['Check the remaining configuration values']
    assert refreshed
    assert toast.bounding_box()['y'] + toast.bounding_box()['height'] <= page.locator('#studio-composer').bounding_box()['y']
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    shot(page, f'{width}-stale-view-conflict')
    page.locator('#send-button').click()
    expect(toast).to_have_text('Instruction queued.')
    expect(page.locator('[data-queued-id="62"]')).to_contain_text(submissions[0])
    assert queued == submissions
    assert len(submissions) == 1
    shot(page, f'{width}-stale-view-queued')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_queue_stack_edit_restore_save_cancel_delete_and_submit(page, fixture_base, width):
    state, requests = open_queue(page, fixture_base, width)
    root, prompt = page.locator('#thread-command-queue'), page.locator('#task-prompt')
    expect(root.locator('.queued-files > span')).to_have_count(4)
    assert root.bounding_box()['y'] + root.bounding_box()['height'] <= page.locator('#studio-composer').bounding_box()['y'] + 1
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    shot(page, f'{width}-queue')
    prompt.fill('Unsent draft to preserve')
    root.get_by_role('button', name='Edit queued item').click()
    expect(prompt).to_have_value('Review these files')
    expect(page.locator('#queued-edit-status')).to_be_visible()
    assert state['rows'][0]['status'] == 'editing'
    expect(page.locator('.is-editing-queue [data-remove-attachment]')).to_have_count(5)
    page.locator('[data-remove-attachment="3"]').click()
    page.locator('#context-file-input').set_input_files({'name': 'replacement.md', 'mimeType': 'text/markdown', 'buffer': b'Replacement notes'})
    expect(page.locator('.is-editing-queue [data-remove-attachment]')).to_have_count(5)
    shot(page, f'{width}-edit')
    prompt.fill('Updated request')
    page.locator('#send-button').click()
    expect(root).to_contain_text('Updated request')
    expect(prompt).to_have_value('Unsent draft to preserve')
    assert len(state['rows'][0]['metadata']['attachments']) == 4
    assert [file['name'] for file in state['rows'][0]['metadata']['attachments']] == ['reference.png', 'voice.ogg', 'walkthrough.mp4', 'replacement.md']
    assert state['rows'][0]['metadata']['attachments'][1]['transcript'] == 'Recording transcript retained with its audio file.'
    assert any(item.get('text') == 'Full transcription of the request.' for item in state['rows'][0]['metadata']['composer']['attachments'])
    root.get_by_role('button', name='Edit queued item').click()
    expect(prompt).to_have_value('Updated request')
    prompt.fill('Cancelled change')
    page.locator('#queued-edit-cancel').click()
    expect(prompt).to_have_value('Unsent draft to preserve')
    expect(root).to_contain_text('Updated request')
    prompt.fill('Another queued instruction')
    page.locator('#send-button').click()
    expect(root.locator('.queued-item')).to_have_count(2)
    assert state['posts'][-1]['metadata']['composer']['text'] == 'Another queued instruction'
    assert not any(path.endswith('/commands/dispatch') for _, path in requests)
    shot(page, f'{width}-stack')
    root.locator('[data-queued-id="61"]').get_by_role('button', name='Delete queued item').click()
    expect(page.get_by_role('dialog')).to_be_visible()
    shot(page, f'{width}-delete')
    page.get_by_role('button', name='Delete item', exact=True).click()
    expect(root.locator('.queued-item')).to_have_count(1)
    assert state['rows'][0]['status'] == 'cancelled'


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_steer_fork_failure_and_reload_are_operable(page, fixture_base, width):
    state, _ = open_queue(page, fixture_base, width)
    root = page.locator('#thread-command-queue')
    state['fail'] = True
    root.get_by_role('button', name='Steer current thread').click()
    expect(root.get_by_role('alert')).to_contain_text('remains queued')
    expect(root.locator('.queued-item')).to_have_count(1)
    shot(page, f'{width}-error')
    state['fail'] = False
    state['hold_run'] = True
    root.get_by_role('button', name='Steer current thread').click()
    expect(root).to_have_attribute('aria-busy', 'true')
    expect(root).to_contain_text('Updating queue...')
    expect(root.get_by_role('button', name='Steer current thread')).to_be_disabled()
    shot(page, f'{width}-starting')
    route, row = state['pending_run']
    row['status'] = 'delivered'
    route.fulfill(status=200, json={'chat_id': 17, 'started': True})
    state['hold_run'] = False
    expect(root.locator('.queued-item')).to_have_count(0)
    assert state['runs'] == [{'new_thread': False}]
    page.locator('#task-prompt').fill('Fork this request')
    page.locator('#send-button').click()
    expect(root.locator('.queued-item')).to_have_count(1)
    page.reload()
    expect(root).to_contain_text('Fork this request')
    root.get_by_role('button', name='Start in a new thread').click()
    expect(root.locator('.queued-item')).to_have_count(0)
    assert state['runs'][-1] == {'new_thread': True}
    expect(page.get_by_role('tooltip')).to_have_count(0)
    shot(page, f'{width}-empty')


def test_reload_during_edit_can_resume_and_keyboard_controls_keep_focus(page, fixture_base):
    state, _ = open_queue(page, fixture_base)
    root = page.locator('#thread-command-queue')
    edit = root.get_by_role('button', name='Edit queued item')
    edit.focus()
    assert edit.evaluate('(element) => getComputedStyle(element).outlineStyle') != 'none'
    page.keyboard.press('Enter')
    expect(page.locator('#queued-edit-status')).to_be_visible()
    page.reload()
    expect(root).to_contain_text('Editing in composer')
    edit.click()
    expect(page.locator('#task-prompt')).to_have_value('Review these files')
    page.locator('#queued-edit-cancel').click()
    expect(root).not_to_contain_text('Editing in composer')
    steer = root.get_by_role('button', name='Steer current thread')
    steer.hover()
    expect(page.get_by_role('tooltip')).to_have_text('Steer current thread')
    steer.focus()
    page.wait_for_timeout(2100)
    expect(steer).to_be_focused()


def test_failed_edit_preserves_draft_and_attachments_for_retry(page, fixture_base):
    state, _ = open_queue(page, fixture_base)
    root = page.locator('#thread-command-queue')
    root.get_by_role('button', name='Edit queued item').click()
    expect(page.locator('#task-prompt')).to_have_value('Review these files')
    page.locator('#task-prompt').fill('Keep this unsaved edit')
    state['fail'] = True
    page.locator('#send-button').click()
    expect(page.locator('#studio-toast')).to_contain_text('remains queued')
    expect(page.locator('#task-prompt')).to_have_value('Keep this unsaved edit')
    expect(page.locator('[data-remove-attachment]')).to_have_count(5)
    state['fail'] = False
    page.locator('#send-button').click()
    expect(root).to_contain_text('Keep this unsaved edit')
    assert state['rows'][0]['status'] == 'queued'


def test_new_thread_action_opens_the_returned_thread(page, fixture_base):
    state, _ = open_queue(page, fixture_base)
    state['new_thread_id'] = 18
    page.locator('#thread-command-queue').get_by_role('button', name='Start in a new thread').click()
    expect(page).to_have_url(f'{fixture_base}/development/threads/18/')
    expect(page.locator('#message-list')).to_contain_text('Review these files')
    expect(page.locator('#thread-command-queue')).to_be_hidden()
