"""Linked multi-project requests remain visible and assignable in Incoming."""
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_incoming_dismiss_undo import fixture_base

pytestmark = pytest.mark.e2e_playwright
SHOTS = Path('/tmp/decisions-incoming-project-confirmation')


def install_review(page):
    _install_api(page, [], current_board_linked=True, direct_execution=True)
    proposal = {'id': 'tensology:101', 'origin': 'tensology', 'source': 'whatsapp',
                'source_thread_id': 'linked-shared-contact', 'message_ids': ['voice-1'],
                'project_id': None, 'project_name': '', 'needs_project': True,
                'candidate_projects': [{'id': 7, 'name': 'DecisionsAI'}, {'id': 10, 'name': 'Spoortrack'}],
                'title': 'Support the new billing type', 'description': 'The request is work for one of the linked projects.',
                'updated_at': '2026-11-10T10:00:00Z',
                'evidence': [{'id': 'voice-1', 'body': 'Please allow the new billing type on the front end.',
                              'sender_name': 'Client', 'direction': 'inbound', 'sent_at': '2026-11-10T08:00:00Z'}],
                'attachments': [{'id': 'voice-file', 'name': 'voice.oga', 'mime_type': 'audio/ogg',
                                 'url': '/confirmation-fixture/audio.ogg', 'source_message_id': 'voice-1',
                                 'transcript': 'Please allow the new billing type on the front end.'}]}
    state = {'rows': [proposal], 'writes': [], 'fail': True, 'files': deepcopy(proposal['attachments'])}

    def api(route):
        path = urlsplit(route.request.url).path
        if path.endswith('/incoming/proposals'):
            return route.fulfill(json={'proposals': state['rows'], 'sync': {'state': 'complete'}})
        if path.endswith('/proposals/files/ensure'):
            return route.fulfill(json={'attachments': state['files'], 'failures': []})
        if path.endswith('/proposals/project'):
            body = route.request.post_data_json
            state['writes'].append(body)
            if state['fail']:
                return route.fulfill(status=503, json={'error': 'Project assignment failed. Try again.'})
            assert body['project_id'] in [p['id'] for p in proposal['candidate_projects']]
            assert body['message_ids'] == ['voice-1']
            project = next(p for p in proposal['candidate_projects'] if p['id'] == body['project_id'])
            state['rows'][0] = {**proposal, 'project_id': project['id'], 'project_name': project['name'],
                                'needs_project': False, 'updated_at': '2026-11-10T10:01:00Z'}
            return route.fulfill(json={'success': True, 'proposal': state['rows'][0]})
        if path.endswith('/proposals/dismiss'):
            state['rows'] = []
            return route.fulfill(json={'success': True, 'dismissed': True})
        if path.endswith(('/proposals/thread', '/proposals/merge')):
            raise AssertionError('Unconfirmed request must not start work or merge')
        route.fallback()
    page.route('**/api/workflows/studio/incoming/proposals**', api)
    page.route('**/confirmation-fixture/audio.ogg', lambda route: route.fulfill(content_type='audio/ogg', body=b''))
    return state


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_confirm_project_keeps_evidence_and_only_linked_choices_through_error_save_reload(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 960})
    state = install_review(page)
    page.goto(fixture_base + '/development/incoming/')
    card = page.locator('.proposal-card')
    expect(card).to_have_count(1)
    expect(card.locator('.proposal-project')).to_have_text('Confirm project')
    expect(card.get_by_role('button', name='Create Thread')).to_have_count(0)
    expect(card.locator('.proposal-drag')).to_have_count(0)
    expect(card).to_have_attribute('draggable', 'false')
    SHOTS.mkdir(exist_ok=True)
    page.screenshot(path=str(SHOTS / f'{width}-review.png'), full_page=True)
    card.get_by_role('button', name='Confirm project', exact=True).click()
    modal = page.locator('#incoming-proposal-modal')
    select = modal.get_by_label('Project', exact=True)
    thread = modal.get_by_role('button', name='Create Thread', exact=True)
    expect(select).to_have_value('')
    expect(thread).to_be_disabled()
    assert select.locator('option').all_text_contents() == ['Choose linked project', 'DecisionsAI', 'Spoortrack']
    # Even a stale or injected option must not bypass the advertised linked candidates.
    select.evaluate("el => {el.add(new Option('Unlinked project', '999')); el.value='999'; el.dispatchEvent(new Event('change'));}")
    expect(modal.locator('#proposal-project-status')).to_contain_text('linked projects')
    assert state['writes'] == []
    select.locator('option[value="999"]').evaluate('el => el.remove()')
    modal.get_by_role('tab', name='Source', exact=False).click()
    expect(modal.locator('[data-proposal-pane="source"]')).to_contain_text('Please allow the new billing type')
    modal.get_by_role('tab', name='Files', exact=False).click()
    expect(modal.locator('audio')).to_have_count(1)
    modal.get_by_text('Transcription', exact=True).click()
    expect(modal.locator('.proposal-file-transcript')).to_contain_text('Please allow the new billing type')
    page.screenshot(path=str(SHOTS / f'{width}-evidence.png'), full_page=True)
    modal.get_by_role('tab', name='Details', exact=True).click()
    select.select_option('10')
    expect(modal.locator('#proposal-project-status')).to_have_text('Project assignment failed. Try again.')
    expect(select).to_have_value('')
    expect(thread).to_be_disabled()
    expect(card.locator('.proposal-project')).to_have_text('Confirm project')
    page.screenshot(path=str(SHOTS / f'{width}-error.png'), full_page=True)
    state['fail'] = False
    select.select_option('10')
    expect(modal.locator('#proposal-project-status')).to_have_text('Saved')
    expect(thread).to_be_enabled()
    expect(card.locator('.proposal-project')).to_have_text('Spoortrack')
    page.screenshot(path=str(SHOTS / f'{width}-assigned.png'), full_page=True)
    page.keyboard.press('Escape')
    expect(modal).to_have_count(0)
    page.reload()
    expect(card.locator('.proposal-project')).to_have_text('Spoortrack')
    expect(card.get_by_role('button', name='Create Thread', exact=True)).to_be_enabled()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert len(state['writes']) == 2
    assert state['rows'][0]['attachments'] == state['files']
    page.screenshot(path=str(SHOTS / f'{width}-reloaded.png'), full_page=True)


def test_unconfirmed_proposal_can_be_dismissed_without_assigning_project(page, fixture_base):
    state = install_review(page)
    page.goto(fixture_base + '/development/incoming/')
    page.locator('.proposal-card').get_by_role('button', name='Dismiss', exact=True).click()
    expect(page.locator('.proposal-card')).to_have_count(0)
    assert state['writes'] == []
    assert state['files'][0]['transcript'].startswith('Please allow')
