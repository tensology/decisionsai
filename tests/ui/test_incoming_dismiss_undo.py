"""Undo snackbar + Recently dismissed for Incoming proposals (mocked APIs, fixture port)."""
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api

pytestmark = pytest.mark.e2e_playwright

SHOTS = Path('/tmp/planning-verify')


@pytest.fixture(scope='module')
def fixture_base():
    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates

    web = Path(__file__).resolve().parents[2] / 'distr' / 'gui' / 'web'
    templates = Jinja2Templates(directory=str(web / 'templates'))
    app = FastAPI()
    app.mount('/static', StaticFiles(directory=str(web / 'static')), name='static')
    app.mount('/assets', StaticFiles(directory=str(web.parents[2] / 'assets')), name='assets')

    @app.get('/development/{path:path}', response_class=HTMLResponse)
    async def page(request: Request, path: str):
        from distr.core.workflow.development_control import DEVELOPMENT_TIMER_MENU
        return templates.TemplateResponse(request, 'workflows/studio.html', {
            'base_path': '/development', 'internal_api_token': '', 'app_version': 'test',
            'chat_js_mtime': '0', 'development_timer_menu': DEVELOPMENT_TIMER_MENU})

    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        url = f'http://127.0.0.1:{listener.getsockname()[1]}'
        server = uvicorn.Server(uvicorn.Config(app, log_level='warning'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            else:
                pytest.fail('Isolated Development fixture did not become ready')
            yield url
        finally:
            server.should_exit = True
            thread.join(timeout=5)


def _proposal(n):
    return {'id': f'local:gmail:undo-{n}', 'origin': 'local', 'source': 'gmail',
            'dedupe_key': f'gmail:undo-{n}', 'title': f'Undo proposal {n}',
            'project_id': 7, 'project_name': 'DecisionsAI', 'message_ids': [f'm{n}']}


def _mock(page, rows):
    state = {'rows': list(rows), 'dismissed': [], 'calls': [], 'dismiss_ids': [], 'failure_ids': set()}

    def handler(route):
        req = route.request
        path = urlparse(req.url).path.removeprefix('/api')
        state['calls'].append((req.method, path))
        if path.endswith('/proposals/dismiss'):
            proposal = req.post_data_json['proposal']
            state['dismiss_ids'].append(proposal['id'])
            if proposal['id'] in state['failure_ids']:
                return route.fulfill(status=503, json={'success': False, 'error': 'Synthetic dismissal failure'})
            key = f"{proposal['id']}:digest"
            state['rows'] = [r for r in state['rows'] if r['id'] != proposal['id']]
            state['dismissed'].insert(0, {'key': key, 'proposal': proposal})
            return route.fulfill(json={'success': True, 'dismissed': True, 'key': key,
                                       'receipt_keys': [key], 'upstream_dismissed': False})
        if path.endswith('/proposals/restore'):
            key = req.post_data_json['key']
            hit = next((d for d in state['dismissed'] if d['key'] == key), None)
            if hit:
                state['dismissed'].remove(hit)
                state['rows'].append(hit['proposal'])
            return route.fulfill(json={'success': True, 'restored': bool(hit), 'key': key,
                                       'removed_keys': [key] if hit else [], 'upstream_dismissed': False,
                                       'proposal': hit['proposal'] if hit else None})
        if path.endswith('/proposals/dismissed'):
            return route.fulfill(json={'success': True, 'dismissed': [
                {'key': d['key'], 'conversation_key': d['proposal']['dedupe_key'],
                 'created_at': '2026-11-03T07:18:06Z', 'title': d['proposal']['title'],
                 'title_recovered': True, 'upstream_dismissed': False, 'source': 'gmail'}
                for d in state['dismissed']]})
        if path.endswith('/incoming/proposals'):
            return route.fulfill(json={'proposals': state['rows'], 'counts': {'gmail': len(state['rows'])}})
        route.fallback()

    page.route('**/api/workflows/studio/incoming/proposals**', handler)
    return state


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
@pytest.mark.parametrize(('case', 'message'), [
    ('changed', 'A proposal or its evidence changed. Refresh Incoming before merging.'),
    ('reserved', 'A proposal is already part of a Decisions merge. Retry the original pair in Incoming to finish it.'),
])
def test_merge_conflict_keeps_cards_and_shows_recovery_message(page, fixture_base, width, case, message):
    first, second = _proposal(1), _proposal(2)
    first['merge_revision'], second['merge_revision'] = 'a' * 64, 'b' * 64
    _install_api(page, [])
    _mock(page, [first, second])
    page.route('**/api/workflows/studio/incoming/proposals/merge', lambda route:
               route.fulfill(status=409, json={'success': False, 'error': message}))
    page.set_viewport_size({'width': width, 'height': 850})
    page.goto(f'{fixture_base}/development/incoming/')
    cards = [page.locator(f'[data-incoming-proposal-open="{row["id"]}"][draggable="true"]') for row in (first, second)]
    with page.expect_request('**/incoming/proposals/merge') as request:
        cards[0].drag_to(cards[1])
    body = request.value.post_data_json
    assert {body[key]['id']: body[key]['merge_revision'] for key in ('first', 'second')} == {
        row['id']: row['merge_revision'] for row in (first, second)
    }
    expect(page.get_by_text(message, exact=True)).to_be_visible()
    for card in cards:
        expect(card).to_be_visible()
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SHOTS / f'merge-conflict-{case}-{width}.png', animations='disabled')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
@pytest.mark.parametrize(('source', 'message'), [
    ('local', 'This proposal is part of an unfinished merge. Refresh Incoming, then select Resume merge.'),
    ('upstream', 'A proposal is already part of a Decisions merge. Retry the original pair in Incoming to finish it.'),
    ('replacement', 'This merge has already started dismissing originals. Finish the merge in Decisions Incoming.'),
])
def test_reserved_merge_dismissal_keeps_the_card_and_explains_how_to_recover(page, fixture_base, width, source, message):
    first, second = _proposal(1), _proposal(2)
    _install_api(page, [])
    _mock(page, [first, second])

    def dismiss(route):
        if route.request.post_data_json['proposal']['id'] == first['id']:
            return route.fulfill(status=409, json={'success': False, 'error': message})
        return route.fallback()

    page.route('**/api/workflows/studio/incoming/proposals/dismiss', dismiss)
    page.set_viewport_size({'width': width, 'height': 850})
    page.goto(f'{fixture_base}/development/incoming/')
    reserved = page.locator(f'[data-incoming-proposal-dismiss="{first["id"]}"]')
    reserved.click()
    expect(page.get_by_text(message, exact=True)).to_be_visible()
    expect(reserved).to_be_enabled()
    expect(page.locator('#incoming-undo-snackbar')).to_be_hidden()
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SHOTS / f'dismiss-merge-reservation-{source}-{width}.png', animations='disabled')

    page.locator(f'[data-incoming-proposal-dismiss="{second["id"]}"]').click()
    expect(page.locator('#incoming-undo-snackbar')).to_contain_text(second['title'])
    expect(reserved).to_be_visible()


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
@pytest.mark.browser_context_args(has_touch=True)
def test_reconnect_reopens_a_proposal_reserved_by_an_upstream_merge(page, fixture_base, monkeypatch, width):
    from distr.core.incoming import proposals

    reserved_id = 986000 + width
    retry = {'replacement_id': reserved_id + 2, 'original_ids': [reserved_id, reserved_id + 1]}
    remote = {
        'id': reserved_id, 'status': 'pending', 'source': 'gmail',
        'source_conversation': {'thread_id': f'merge-reconnect-{width}'},
        'source_message_ids': ['merge-evidence'], 'title': 'Original proposal for merge',
        'project': {'id': 70, 'decisions_project_id': 7, 'name': 'DecisionsAI'},
        'candidate_projects': [{'decisions_project_id': 7, 'name': 'DecisionsAI'}],
        'merge_source_reserved': True,
        'merge_retry': retry,
    }
    proposals._persist_proposal_dismissal(proposals._from_tensology(remote))
    visible = _proposal(width)
    connected = False
    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests',
                        lambda **kwargs: [remote] if connected else [])
    monkeypatch.setattr('distr.core.incoming.project_links.proposal_in_scope', lambda *args, **kwargs: True)
    monkeypatch.setattr(proposals, '_accepted_proposal_keys', lambda **kwargs: set())
    monkeypatch.setattr(proposals, '_saved_local_proposals', lambda: [])
    monkeypatch.setattr(proposals, '_with_restored_snapshots', lambda rows: rows)
    monkeypatch.setitem(proposals._TRIAGE_CACHE, 'proposals', [])
    _install_api(page, [])

    def list_proposals(route):
        proposals.invalidate_proposals_list_cache()
        payload = proposals.list_incoming_proposals(refresh_local=False)
        return route.fulfill(json={**payload, 'proposals': [visible, *payload['proposals']]})

    page.route('**/api/workflows/studio/incoming/proposals?*', list_proposals)
    attempts = []

    def retry_merge(route):
        attempts.append(route.request.post_data_json)
        if len(attempts) == 1:
            return route.fulfill(status=409, json={'error': 'The saved merge sources changed. Refresh Incoming before retrying.'})
        return route.fulfill(json={'chat_id': 17, 'review_key': 'a' * 64})

    page.route('**/api/workflows/studio/incoming/proposals/merge', retry_merge)
    page.route('**/api/chats/17?*', lambda route: route.fulfill(json={
        'id': 17, 'title': 'Recovered proposal', 'project_id': 7, 'development': True,
        'development_board_key': 'decisions:70', 'messages': [],
    }))
    page.set_viewport_size({'width': width, 'height': 850})
    page.goto(f'{fixture_base}/development/incoming/')
    expect(page.get_by_role('heading', name=visible['title'])).to_be_visible()
    card = page.locator(f'[data-incoming-proposal-open="tensology:{reserved_id}"][draggable="true"]')
    expect(card).to_have_count(0)

    connected = True
    page.reload()
    expect(card).to_contain_text('Original proposal for merge')
    expect(card).to_contain_text('Reserved for a merge. Retry with its original partner.')
    expect(card.locator('[data-incoming-proposal-thread],[data-incoming-proposal-dismiss]')).to_have_count(0)
    expect(page.get_by_role('heading', name=visible['title'])).to_be_visible()
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SHOTS / f'merge-reconnect-restored-{width}.png', animations='disabled')
    card.get_by_role('heading').click()
    review = page.get_by_role('dialog', name='Original proposal for merge')
    expect(review).to_contain_text('Reserved for a merge. Retry with its original partner.')
    expect(review.get_by_role('button', name='Create Thread')).to_be_disabled()
    expect(review.get_by_label('Project')).to_be_disabled()
    expect(review.get_by_label('Project')).to_have_value('7')
    page.screenshot(path=SHOTS / f'merge-reconnect-review-{width}.png', animations='disabled')
    review.get_by_role('button', name='Close', exact=True).last.click()
    retry_button = card.get_by_role('button', name='Retry merge')
    retry_button.tap()
    expect(page.locator('#studio-toast')).to_contain_text('The saved merge sources changed.')
    expect(retry_button).to_be_focused()
    page.screenshot(path=SHOTS / f'merge-reconnect-conflict-{width}.png', animations='disabled')
    retry_button.tap()
    expect(page).to_have_url(f'{fixture_base}/development/threads/17/')
    expect(page.locator('#header-task')).to_have_text('Recovered proposal')
    assert attempts == [{'retry': retry}, {'retry': retry}]
    page.screenshot(path=SHOTS / f'merge-reconnect-opened-{width}.png', animations='disabled')


def test_undo_snackbar_restores_row_in_place_and_times_out(page, fixture_base):
    SHOTS.mkdir(parents=True, exist_ok=True)
    _install_api(page, [])
    _mock(page, [_proposal(1), _proposal(2), _proposal(3)])
    page.set_viewport_size({'width': 1280, 'height': 900})
    page.goto(f'{fixture_base}/development/incoming/')
    cards = page.locator('[data-incoming-proposal-dismiss]')
    expect(cards).to_have_count(3)
    page.locator('[data-incoming-proposal-dismiss="local:gmail:undo-2"]').click()
    page.mouse.move(5, 5)
    snack = page.locator('#incoming-undo-snackbar')
    expect(snack).to_be_visible()
    expect(snack).to_contain_text('Undo proposal 2')
    expect(cards).to_have_count(2)
    page.screenshot(path=str(SHOTS / 'undo-snackbar.png'))
    undo = snack.get_by_role('button', name='Undo')
    undo.focus()
    page.keyboard.press('Enter')
    expect(cards).to_have_count(3)
    expect(snack).to_be_hidden()
    ids = [b.get_attribute('data-incoming-proposal-dismiss') for b in cards.all()]
    assert ids == ['local:gmail:undo-1', 'local:gmail:undo-2', 'local:gmail:undo-3']
    page.screenshot(path=str(SHOTS / 'undo-restored.png'))
    # Second dismiss replaces nothing visible yet; then let it time out (~10s).
    page.locator('[data-incoming-proposal-dismiss="local:gmail:undo-3"]').click()
    page.mouse.move(5, 5)
    expect(snack).to_be_visible()
    expect(snack).to_be_hidden(timeout=13_000)
    page.screenshot(path=str(SHOTS / 'undo-timeout.png'))


def test_second_dismiss_replaces_snackbar_and_list_restores_earlier(page, fixture_base):
    SHOTS.mkdir(parents=True, exist_ok=True)
    _install_api(page, [])
    state = _mock(page, [_proposal(1), _proposal(2)])
    page.set_viewport_size({'width': 1280, 'height': 900})
    page.goto(f'{fixture_base}/development/incoming/')
    cards = page.locator('[data-incoming-proposal-dismiss]')
    expect(cards).to_have_count(2)
    page.locator('[data-incoming-proposal-dismiss="local:gmail:undo-1"]').click()
    page.locator('[data-incoming-proposal-dismiss="local:gmail:undo-2"]').click()
    page.mouse.move(5, 5)
    expect(page.locator('#incoming-undo-snackbar')).to_have_count(1)
    expect(page.locator('#incoming-undo-snackbar')).to_contain_text('Undo proposal 2')
    expect(cards).to_have_count(0)
    toggle = page.locator('#incoming-dismissed-toggle')
    toggle.click()
    expect(toggle).to_have_attribute('aria-expanded', 'true')
    panel = page.locator('#incoming-dismissed-panel')
    expect(panel.locator('[data-incoming-restore]')).to_have_count(2)
    page.screenshot(path=str(SHOTS / 'undo-recently-dismissed.png'))
    panel.locator('[data-incoming-restore="local:gmail:undo-1:digest"]').click()
    expect(page.locator('[data-incoming-proposal-dismiss="local:gmail:undo-1"]')).to_have_count(1)
    expect(panel.locator('[data-incoming-restore]')).to_have_count(1)
    assert ('POST', '/workflows/studio/incoming/proposals/restore') in state['calls']
    page.screenshot(path=str(SHOTS / 'undo-list-restored.png'))


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_legacy_restore_and_delete_history_use_real_handlers(page, fixture_base, monkeypatch, width):
    import json
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core.db import Chat, IncomingProposalDismissal, IncomingProposalRestoration, WhatsAppPhoneLink, WhatsAppMessage, engine, get_session
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.projects import Project
    from distr.core.db.migrations import ensure_incoming_dismissal_snapshot_columns
    from distr.core.incoming import proposals as p
    from distr.gui.web.routes.development.incoming import register_routes

    ensure_incoming_dismissal_snapshot_columns(engine)
    monkeypatch.setattr(p, '_RESTORED_SNAPSHOTS', {})
    monkeypatch.setattr(p, '_TRIAGE_CACHE', {'at':0.0,'running':False,'proposals':[]})
    monkeypatch.setattr(p, '_local_proposals', lambda *a: [])
    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests', lambda **kwargs: [])
    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change', lambda **kw: None)
    legacy = {'id':'local:whatsapp:restore-fixture@g.us','source':'whatsapp',
              'dedupe_key':'whatsapp:restore-fixture@g.us','message_ids':['restore-fixture-1'],
              'title':'Create or use a family organisation to test GPS, Wellness, and RPE together',
              'summary':'Recovered source scope for the family organisation testing request.'}
    hidden = {'id':'local:whatsapp:hide-fixture@g.us','source':'whatsapp',
              'dedupe_key':'whatsapp:hide-fixture@g.us','message_ids':['hide-fixture-1'],
              'title':'Keep the original source dismissed after deleting this history entry',
              'summary':'Deleting history must retain suppression.'}
    keys = [p._proposal_handoff_key(r) for r in (legacy, hidden)]
    merged_original = {
        'id': f'local:gmail:merge-fixture-{width}', 'source': 'gmail',
        'source_thread_id': f'merge-fixture-{width}', 'message_ids': ['merge-evidence'],
        'title': 'Already merged into Development',
    }
    merged_key = p._proposal_handoff_key(merged_original)
    with get_session() as db:
        db.query(IncomingProposalDismissal).delete()
        db.query(IncomingProposalRestoration).delete()
        if db.get(Project, 700077) is None:
            db.add(Project(id=700077,name='Player1Sport fixture'))
            db.add(KanbanBoard(id=700077,name='Source-linked fixture',default_project_id=700077))
            db.add(WhatsAppPhoneLink(board_id=700077,phone_jid='restore-fixture@g.us'))
            db.add(WhatsAppMessage(message_id='restore-fixture-1',jid='restore-fixture@g.us',text='Use the family organisation for combined testing.'))
        for row,key in zip((legacy,hidden),keys):
            db.add(IncomingProposalDismissal(key=key, conversation_key=row['dedupe_key'],
                message_ids=json.dumps(row['message_ids']),title=row['title'],summary=row['summary']))
        db.add(Chat(project_id=700077, title='Merged Development thread', params=json.dumps({
            'incoming_merge': {'original_keys': [merged_key], 'pending_originals': []},
        })))
        db.commit()
    p._persist_proposal_dismissal(merged_original)
    p.invalidate_proposals_list_cache()
    app,router = FastAPI(),APIRouter()
    register_routes(router,None)
    app.include_router(router)
    client = TestClient(app)
    calls=[]
    fail_delete=[False]
    _install_api(page, [])
    def handler(route):
        req=route.request
        path=urlparse(req.url).path.removeprefix('/api')
        calls.append((req.method,path))
        if path.endswith('/proposals/restore'):
            response=client.post(path,json=req.post_data_json)
        elif path.endswith('/proposals/dismissed'):
            if req.method == 'DELETE':
                if fail_delete[0]:
                    return route.fulfill(status=500,json={'error':'Fixture delete failed'})
                response=client.request('DELETE',path,json=req.post_data_json)
            else:
                response=client.get(path)
        elif path.endswith('/incoming/proposals'):
            return route.fulfill(json=p.list_incoming_proposals(refresh_local=False))
        else:
            return route.fallback()
        route.fulfill(status=response.status_code,json=response.json())
    page.route('**/api/workflows/studio/incoming/proposals**',handler)
    page.set_viewport_size({'width':width,'height':1000})
    page.goto(f'{fixture_base}/development/incoming/')
    page.locator('#incoming-dismissed-toggle').click()
    panel=page.locator('#incoming-dismissed-panel')
    expect(panel.locator('[data-incoming-restore]')).to_have_count(2)
    expect(page.get_by_text('Synchronization incomplete.', exact=False)).to_have_count(0)
    expect(panel).not_to_contain_text(merged_original['title'])
    shots = SHOTS / 'merge-history'
    shots.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(shots/f'actions-{width}.png'),full_page=True)
    for i,row in enumerate(panel.locator('.incoming-dismissed-row').all()):
        row.screenshot(path=str(shots/f'actions-row-{width}-{i}.png'))
    panel.locator(f'[data-incoming-restore="{keys[0]}"]').click()
    card=page.locator('[data-incoming-proposal-open]').filter(has_text=legacy['title']).first
    expect(card).to_be_visible()
    expect(card.locator('[data-incoming-proposal-thread]')).to_be_enabled()
    expect(panel).not_to_contain_text('pending re-triage')
    expect(panel.locator('[data-incoming-restore]')).to_have_count(1)
    page.screenshot(path=str(shots/f'restored-{width}.png'),full_page=True)
    card.click()
    dialog=page.get_by_role('dialog',name=legacy['title'],exact=True)
    dialog.get_by_role('tab',name='Source').click()
    expect(dialog.get_by_role('tab',name='Source')).to_have_attribute('aria-selected','true')
    expect(dialog.get_by_role('tab',name='Details',exact=True)).to_have_attribute('aria-selected','false')
    expect(dialog).to_contain_text('Use the family organisation for combined testing.')
    page.screenshot(path=str(shots/f'restored-source-{width}.png'),full_page=True)
    dialog.locator('[data-proposal-close]').first.click()
    # A failed Delete keeps the entry, reenables its control and returns focus.
    fail_delete[0]=True
    delete=panel.locator('[data-incoming-hide-dismissed]')
    delete.click()
    expect(delete).to_be_enabled()
    expect(delete).to_be_focused()
    page.screenshot(path=str(shots/f'delete-error-{width}.png'),full_page=True)
    fail_delete[0]=False
    delete.click()
    expect(panel).to_contain_text('Nothing dismissed recently.')
    expect(page.locator('#incoming-dismissed-toggle')).to_be_focused()
    assert p._undismissed_proposals([hidden]) == []
    assert sum(method=='POST' and path.endswith('/restore') for method,path in calls)==1
    page.reload()
    expect(page.locator('[data-incoming-proposal-open]').filter(has_text=legacy['title']).first).to_be_visible()
    page.locator('#incoming-dismissed-toggle').click()
    expect(panel).to_contain_text('Nothing dismissed recently.')
    page.screenshot(path=str(shots/f'reloaded-{width}.png'),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def _dated_proposal(n, *, status='pending', expired=True, expires_at=None):
    return {**_proposal(n), 'status': status, 'stored_status': status,
            'is_expired': expired, 'source_message_at': '2020-01-01T12:00:00Z' if expired else None,
            'expires_at': expires_at or ('2020-01-05T12:00:00Z' if expired else '2099-01-01T00:00:00Z')}


def _bulk_fixture(page, fixture_base, rows, *, width=1280):
    _install_api(page, [])
    state = _mock(page, rows)
    page.set_viewport_size({'width': width, 'height': 1000})
    page.goto(f'{fixture_base}/development/incoming/')
    expect(page.locator('[data-incoming-proposal-dismiss]')).to_have_count(len(rows))
    return state, page.locator('#incoming-dismiss-expired')


@pytest.mark.parametrize('width', [1280, 390])
def test_bulk_dismiss_targets_only_expired_pending_candidates_and_supports_history_restore(page, fixture_base, width):
    old_pending = _dated_proposal('bulk-pending')
    # This one crossed the clock boundary after its metadata was computed.
    old_candidate = _dated_proposal('bulk-candidate', status='candidate', expired=False,
                                    expires_at='2020-01-05T12:00:00Z')
    fresh = _dated_proposal('bulk-fresh', expired=False)
    terminal = _dated_proposal('bulk-terminal', status='approved')
    unknown = {**_proposal('bulk-unknown'), 'status': 'pending', 'is_expired': False,
               'expires_at': None, 'source_message_at': None}
    rows = [old_pending, old_candidate, fresh, terminal, unknown]
    state, button = _bulk_fixture(page, fixture_base, rows, width=width)
    expect(button).to_be_enabled()
    assert button.evaluate('node => node.parentElement.contains(document.getElementById("incoming-dismissed-toggle"))')
    button.click()
    expect(page.locator('#studio-toast')).to_contain_text('Dismissed 2 expired proposals.')
    expect(page.locator('[data-incoming-proposal-dismiss]')).to_have_count(3)
    expect(button).to_be_disabled()
    assert state['dismiss_ids'] == [old_pending['id'], old_candidate['id']]
    assert {row['id'] for row in state['rows']} == {fresh['id'], terminal['id'], unknown['id']}
    page.locator('#incoming-dismissed-toggle').click()
    panel = page.locator('#incoming-dismissed-panel')
    expect(panel.locator('[data-incoming-restore]')).to_have_count(2)
    panel.locator(f'[data-incoming-restore="{old_pending["id"]}:digest"]').click()
    restored = page.locator(f'[data-incoming-proposal-open="{old_pending["id"]}"]').first
    expect(restored).to_be_visible()
    expect(restored.locator('.proposal-expired-ribbon')).to_have_text('Expired')
    expect(button).to_be_enabled()
    expect(panel.locator('[data-incoming-restore]')).to_have_count(1)
    assert state['dismiss_ids'] == [old_pending['id'], old_candidate['id']], 'Restore must not issue a fresh dismissal'
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f'bulk-expired-history-restored-{width}.png'), full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_bulk_dismiss_is_disabled_for_fresh_terminal_and_unknown_dates(page, fixture_base):
    rows = [_dated_proposal('no-expired-fresh', expired=False),
            _dated_proposal('no-expired-dismissed', status='dismissed'),
            _dated_proposal('no-expired-queued', status='queued'),
            {**_proposal('no-expired-unknown'), 'status': 'candidate', 'is_expired': False,
             'expires_at': None, 'source_message_at': None}]
    state, button = _bulk_fixture(page, fixture_base, rows)
    expect(button).to_be_disabled()
    button.evaluate('node => node.click()')
    assert state['dismiss_ids'] == []
    expect(page.locator('[data-incoming-proposal-dismiss]')).to_have_count(4)


def test_bulk_dismiss_partial_failure_preserves_failed_row_and_retries_only_it(page, fixture_base):
    first, failed = _dated_proposal('partial-first'), _dated_proposal('partial-failed')
    fresh = _dated_proposal('partial-fresh', expired=False)
    state, button = _bulk_fixture(page, fixture_base, [first, failed, fresh])
    state['failure_ids'].add(failed['id'])
    button.click()
    expect(page.locator('#studio-toast')).to_contain_text('Dismissed 1 expired proposal. 1 could not be dismissed.')
    expect(page.locator(f'[data-incoming-proposal-dismiss="{failed["id"]}"]')).to_be_visible()
    expect(page.locator(f'[data-incoming-proposal-dismiss="{first["id"]}"]')).to_have_count(0)
    expect(button).to_be_enabled()
    assert state['dismiss_ids'] == [first['id'], failed['id']]
    state['failure_ids'].clear()
    button.click()
    expect(page.locator('#studio-toast')).to_have_text('Dismissed 1 expired proposal.')
    expect(button).to_be_disabled()
    expect(page.locator('[data-incoming-proposal-dismiss]')).to_have_count(1)
    assert state['dismiss_ids'] == [first['id'], failed['id'], failed['id']]
    page.locator('#incoming-dismissed-toggle').click()
    expect(page.locator('#incoming-dismissed-panel [data-incoming-restore]')).to_have_count(2)


def test_bulk_dismiss_snapshots_click_time_targets_and_blocks_duplicate_competing_actions(page, fixture_base):
    # Freeze Date only; ordinary timers still run and can expose the busy state.
    page.clock.set_fixed_time(datetime(2030, 1, 1, tzinfo=timezone.utc))
    page.add_init_script('''const originalBulkFetch = window.fetch.bind(window);
        window.fetch = async (input, init) => {
            const url = input instanceof Request ? input.url : String(input);
            if (url.includes('/proposals/dismiss') && init?.method === 'POST') {
                await new Promise(resolve => window.setTimeout(resolve, 350));
            }
            return originalBulkFetch(input, init);
        };''')
    first, second = _dated_proposal('busy-first'), _dated_proposal('busy-second')
    soon = _dated_proposal('busy-soon', expired=False, expires_at='2030-01-02T00:00:00Z')
    state, button = _bulk_fixture(page, fixture_base, [first, second, soon])
    button.evaluate('node => { node.click(); node.click(); }')
    expect(button).to_have_text('Dismissing...')
    expect(button).to_be_disabled()
    controls = page.locator('[data-incoming-proposal-thread],[data-incoming-proposal-dismiss]')
    assert controls.evaluate_all('nodes => nodes.every(node => node.disabled)')
    # Even synthetic direct events must not bypass the mutation guard.
    page.evaluate('''() => {
        document.querySelectorAll('[data-incoming-proposal-thread],[data-incoming-proposal-dismiss]').forEach(node => node.click());
        const cards = [...document.querySelectorAll('.proposal-card[data-proposal-project]')];
        const transfer = new DataTransfer();
        cards[0].dispatchEvent(new DragEvent('dragstart', {bubbles:true,dataTransfer:transfer}));
        cards[2].dispatchEvent(new DragEvent('dragover', {bubbles:true,cancelable:true,dataTransfer:transfer}));
        cards[2].dispatchEvent(new DragEvent('drop', {bubbles:true,cancelable:true,dataTransfer:transfer}));
    }''')
    # The previously fresh card is now expired, but was not in the clicked batch.
    page.clock.set_fixed_time(datetime(2030, 1, 3, tzinfo=timezone.utc))
    expect(page.locator('#studio-toast')).to_contain_text('Dismissed 2 expired proposals.')
    expect(page.locator('[data-incoming-proposal-dismiss]')).to_have_count(1)
    expect(page.locator(f'[data-incoming-proposal-dismiss="{soon["id"]}"]')).to_be_visible()
    expect(button).to_be_enabled()
    assert state['dismiss_ids'] == [first['id'], second['id']]
    assert not any(path.endswith('/proposals/thread') or path.endswith('/proposals/merge')
                   for method, path in state['calls'] if method == 'POST')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_many_expired_and_fresh_proposal_cards_keep_content_height_and_scroll(page, fixture_base, width):
    rows = [{**_dated_proposal(f'noncollapse-{i}', expired=i % 2 == 0),
             'source_started_at': f'2026-10-{28 - i:02d}T12:00:00Z',
             'title': f'Preserve full proposal card {i + 1}',
             'description': 'Review the referenced customer evidence and keep every proposal readable. '
                        'The description retains its normal preview while the source and actions stay available.'}
            for i in range(14)]
    state, _ = _bulk_fixture(page, fixture_base, rows, width=width)
    cards = page.locator('.proposal-card[data-proposal-project]')
    expect(cards).to_have_count(14)
    expect(cards.locator('.proposal-expired-ribbon')).to_have_count(7)
    metrics = cards.evaluate_all('''nodes => nodes.map(card => {
        const bounds = card.getBoundingClientRect();
        const selectors = ['.proposal-title', '.proposal-source', '.proposal-desc', '.proposal-actions',
            '[data-incoming-proposal-thread]', '[data-incoming-proposal-dismiss]'];
        return {height: bounds.height, parts: selectors.map(selector => {
            const node = card.querySelector(selector), rect = node.getBoundingClientRect();
            return {selector, width: rect.width, height: rect.height,
                inside: rect.left >= bounds.left && rect.right <= bounds.right + 1 &&
                    rect.top >= bounds.top && rect.bottom <= bounds.bottom + 1,
                enabled: !node.disabled};
        })};
    })''')
    for index, card in enumerate(metrics):
        assert card['height'] >= 120, (width, index, card)
        assert all(part['inside'] and part['width'] > 0 and part['height'] > 0 and part['enabled']
                   for part in card['parts']), (width, index, card)
    region = page.locator('.proposal-cards')
    assert region.evaluate('node => node.scrollHeight > node.clientHeight + 100')
    assert region.evaluate('node => { node.scrollTop = node.scrollHeight; return node.scrollTop > 0; }')
    expect(cards.last.locator('[data-incoming-proposal-dismiss]')).to_be_in_viewport()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f'proposal-noncollapse-{width}-bottom.png'))
    region.evaluate('node => { node.scrollTop = 0; }')
    expect(cards.first.locator('[data-incoming-proposal-thread]')).to_be_in_viewport()
    page.screenshot(path=str(SHOTS / f'proposal-noncollapse-{width}-top.png'))
    assert state['dismiss_ids'] == []


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_proposals_sort_by_earliest_referenced_source_and_restore_chronologically(page, fixture_base, width):
    may = {**_dated_proposal('chronology-may'), 'title': 'May 15 source, generated today',
           'created_at': '2026-11-06T12:00:00Z', 'message_ids': ['may-first', 'may-followup'],
           'evidence': [{'id': 'may-followup', 'sent_at': '2026-11-05T12:00:00Z'},
                        {'id': 'may-first', 'sent_at': '2026-05-15T12:00:00Z'}]}
    june = {**_dated_proposal('chronology-june'), 'title': 'June 1 source, generated earlier',
            'created_at': '2026-06-02T12:00:00Z', 'source_message_ids': ['june-first'],
            'message_ids': [], 'evidence': [{'id': 'june-first', 'sent_at': '2026-06-01T12:00:00Z'}]}
    november = {**_dated_proposal('chronology-november', expired=False), 'title': 'November 1 referenced source',
                'message_ids': ['november-first'],
                'evidence': [{'id': 'unrelated-may', 'sent_at': '2026-05-01T12:00:00Z'},
                             {'id': 'november-first', 'sent_at': '2026-11-01T12:00:00Z'}]}
    unknown = {**_proposal('chronology-unknown'), 'title': 'Unknown original source date',
               'created_at': '2099-01-01T00:00:00Z', 'source_message_at': '2099-01-01T00:00:00Z'}
    state, _ = _bulk_fixture(page, fixture_base, [may, unknown, june, november], width=width)
    cards = page.locator('.proposal-card[data-proposal-project]')
    expected = [row['id'] for row in [november, june, may, unknown]]
    assert cards.evaluate_all('nodes => nodes.map(node => node.dataset.incomingProposalOpen)') == expected
    page.locator(f'[data-incoming-proposal-dismiss="{may["id"]}"]').click()
    expect(cards).to_have_count(3)
    page.locator('#incoming-undo-snackbar').get_by_role('button', name='Undo', exact=True).click()
    expect(cards).to_have_count(4)
    assert cards.evaluate_all('nodes => nodes.map(node => node.dataset.incomingProposalOpen)') == expected
    assert state['dismiss_ids'] == [may['id']]
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f'proposal-source-chronology-{width}.png'))


def test_proposal_source_order_uses_actual_timezone_instants_and_stable_string_id_ties(page, fixture_base):
    def row(name, date):
        return {**_proposal(name), 'message_ids': [name],
                'evidence': [{'id': name, 'sent_at': date}]}
    tie_z = row('tie-z', '2026-11-01T01:00:00Z')
    earlier = row('timezone-earlier', '2026-11-01T02:30:00+02:00')
    tie_a = row('tie-a', '2026-10-31T21:00:00-04:00')
    latest = row('timezone-latest', '2026-10-31T23:00:00-03:00')
    _bulk_fixture(page, fixture_base, [tie_z, earlier, tie_a, latest])
    expected = [row['id'] for row in [latest, tie_a, tie_z, earlier]]
    cards = page.locator('.proposal-card[data-proposal-project]')
    assert cards.evaluate_all('nodes => nodes.map(node => node.dataset.incomingProposalOpen)') == expected
    page.locator('#incoming-dismissed-toggle').click()
    assert cards.evaluate_all('nodes => nodes.map(node => node.dataset.incomingProposalOpen)') == expected
