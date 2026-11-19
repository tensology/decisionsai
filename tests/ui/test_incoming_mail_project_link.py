"""Gmail and Mailshot link actions use the same project picker and proposal flow."""
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_incoming_dismiss_undo import fixture_base

pytestmark = pytest.mark.e2e_playwright
SHOTS = Path("/tmp/decisions-mail-project-link")


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_whatsapp_multiboard_unlink_uses_isolated_shell(page, fixture_base, monkeypatch, width):
    from tests.ui import test_incoming_unlink_confirmation as checks
    monkeypatch.setattr(checks, 'BASE', fixture_base)
    checks.test_unlink_updates_without_refresh_and_closes_after_last_link(page, width)


@pytest.mark.parametrize('width', [390, 768, 1440])
@pytest.mark.parametrize('source', ['gmail', 'mailshot'])
def test_mail_sender_unlink_retains_other_project_and_refreshes_all_threads(page, fixture_base, width, source):
    page.set_viewport_size({'width': width, 'height': 940})
    _install_api(page, [], current_board_linked=True)
    now = datetime.now(timezone.utc).isoformat()
    state = {'removed': False, 'writes': [], 'fail': width == 1440 and source == 'mailshot'}
    def api(route):
        path = urlsplit(route.request.url).path
        if path.endswith('/incoming/mail/project'):
            body = route.request.post_data_json
            assert body == {'source': source, 'thread_id': 'unlink-thread-1', 'message_id': 'unlink-mail-1', 'project_id': 7, 'remove': True}
            state['writes'].append(body)
            if state['fail']:
                return route.fulfill(status=409, json={'error': 'The sender link changed. Refresh before retrying.'})
            state['removed'] = True
            return route.fulfill(json={'project_id': 7, 'removed': True, 'synced': True, 'project_ids': [8]})
        if path.endswith('/incoming/proposals'):
            return route.fulfill(json={'proposals': []})
        if path.endswith('/incoming'):
            ids = [8] if state['removed'] else [7, 8]
            return route.fulfill(json={'items': [{'source': source, 'key': f'{source}:unlink-mail-{i}',
                'source_thread_id': f'unlink-thread-{i}', 'source_message_id': f'unlink-mail-{i}',
                'subject': f'Sender request {i}', 'conversation_label': f'Sender request {i}',
                'sender': 'customer@example.test', 'text': 'Follow-up conversation', 'created_at': now,
                'project_id': 8 if state['removed'] else None, 'project_ids': ids} for i in [1, 2]],
                'channels': {source: {'connected': True}}})
        return route.fallback()
    page.route('**/api/workflows/studio/incoming**', api)
    page.goto(f'{fixture_base}/development/incoming/')
    page.locator(f'#incoming-folder-{source}').click()
    row = page.locator(f'[data-incoming-{source}-thread]').filter(has_text='Sender request 1')
    if width == 390:
        row.click()
        page.get_by_role('button', name='Change project', exact=True).click()
    else:
        row.click(button='right')
    dialog = page.locator('#incoming-mail-project-dialog')
    expect(dialog.get_by_role('button', name='Unlink DecisionsAI from sender')).to_be_visible()
    expect(dialog.get_by_role('button', name='Unlink Empty project from sender')).to_be_visible()
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SHOTS / f'{source}-{width}-unlink-picker.png')
    dialog.get_by_role('button', name='Unlink DecisionsAI from sender').click()
    if state['fail']:
        expect(dialog.get_by_role('alert')).to_contain_text('sender link changed')
        expect(dialog.get_by_role('button', name='Unlink DecisionsAI from sender')).to_be_enabled()
        page.screenshot(path=SHOTS / f'{source}-{width}-unlink-retry.png')
        state['fail'] = False
        dialog.get_by_role('button', name='Unlink DecisionsAI from sender').click()
    expect(dialog).to_be_hidden()
    back = page.locator(f'#incoming-panel-{source} [data-incoming-back]')
    if back.is_visible():
        back.click()
    rows = page.locator(f'[data-incoming-{source}-thread]')
    expect(rows).to_have_count(2)
    for item in rows.all():
        expect(item).to_contain_text('Empty project')
        expect(item).not_to_contain_text('DecisionsAI')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=SHOTS / f'{source}-{width}-unlink-complete.png')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_missing_shared_mail_link_shows_actionable_partial_sync(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 940})
    _install_api(page, [], current_board_linked=True)
    reason = ('This email is linked to a project only in DecisionsAI. Its matching Tensology conversation link is missing or differs. '
              'Sync the sender/project link before importing the proposal.')
    page.route('**/api/workflows/studio/incoming/proposals?*', lambda route: route.fulfill(json={
        'proposals': [], 'sync': {'state': 'partial', 'conflicts': [{'proposal_id': 'local:mailshot:alert', 'reason': reason}]}}))
    page.goto(f'{fixture_base}/development/incoming/')
    status = page.locator('#incoming-proposal-sync-status')
    expect(status).to_contain_text('Synchronization incomplete.')
    expect(status).to_contain_text(reason)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SHOTS / f'partial-sync-{width}.png')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_followup_amends_existing_thread_after_acceptance(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 940})
    requests = []
    _install_api(page, requests, current_board_linked=True)
    proposal = {'id': 'local:whatsapp:followup-question', 'origin': 'local', 'source': 'whatsapp',
                'project_id': 7, 'project_name': 'DecisionsAI', 'title': 'Explain incorrect DELIVERY3 charge',
                'description': 'Any idea how this happened?',
                'followup_thread': {'chat_id': 17, 'status': 'completed', 'reason': 'same conversation and topic'}}
    page.route('**/api/workflows/studio/incoming/proposals?*',
               lambda route: route.fulfill(json={'proposals': [proposal]}))
    posts = []
    def accept(route):
        posts.append(route.request.post_data_json)
        route.fulfill(json={'chat_id': 17})
    page.route('**/api/workflows/studio/incoming/proposals/thread', accept)
    page.goto(f'{fixture_base}/development/incoming/')
    card = page.locator('.proposal-card').filter(has_text=proposal['title'])
    expect(card).to_contain_text('Thread #17 · completed')
    expect(card.get_by_role('button', name='Amend thread', exact=True)).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SHOTS / f'followup-{width}.png')
    card.locator('h3').click()
    dialog = page.get_by_role('dialog', name=proposal['title'])
    expect(dialog).to_contain_text('Follow-up to thread #17')
    expect(dialog.get_by_role('button', name='Amend thread', exact=True)).to_be_visible()
    page.screenshot(path=SHOTS / f'followup-detail-{width}.png')
    dialog.get_by_role('button', name='Amend thread', exact=True).click()
    expect(page).to_have_url(f'{fixture_base}/development/threads/17/')
    assert len(posts) == 1
    assert posts[0]['proposal']['id'] == proposal['id']


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_existing_proposal_opens_thread_without_dispatch(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 940})
    requests = []
    _install_api(page, requests, current_board_linked=True)
    proposal = {'id': 'local:gmail:existing-work', 'origin': 'local', 'source': 'gmail',
                'project_id': 7, 'project_name': 'DecisionsAI', 'title': 'Existing request',
                'description': 'The request is already linked to Development.',
                'linked_thread': {'chat_id': 17, 'status': 'blocked', 'ticket_key': 'PROJ-123'}}
    page.route('**/api/workflows/studio/incoming/proposals?*',
               lambda route: route.fulfill(json={'proposals': [proposal]}))
    page.goto(f'{fixture_base}/development/incoming/')
    card = page.locator('.proposal-card').filter(has_text='Existing request')
    expect(card).to_contain_text('Thread #17 · PROJ-123 · blocked')
    expect(card.get_by_role('button', name='Open thread', exact=True)).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SHOTS / f'existing-thread-{width}.png')
    card.get_by_role('button', name='Open thread', exact=True).click()
    expect(page).to_have_url(f'{fixture_base}/development/threads/17/')
    assert not any(method == 'POST' and ('proposals/thread' in path or '/commands' in path)
                   for method, path in requests)


@pytest.mark.parametrize('width', [390, 1440])
def test_proposal_project_conflict_stays_visible_and_cannot_start(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 940})
    requests = []
    _install_api(page, requests, current_board_linked=True)
    proposal = {'id': 'local:gmail:conflicting-work', 'origin': 'local', 'source': 'gmail',
                'project_id': 7, 'project_name': 'DecisionsAI', 'title': 'Conflicting request',
                'thread_link_error': 'The proposal thread belongs to another project.'}
    page.route('**/api/workflows/studio/incoming/proposals?*',
               lambda route: route.fulfill(json={'proposals': [proposal]}))
    page.goto(f'{fixture_base}/development/incoming/')
    card = page.locator('.proposal-card').filter(has_text='Conflicting request')
    expect(card.get_by_role('alert')).to_have_text(proposal['thread_link_error'])
    expect(card.get_by_role('button', name='Create Thread', exact=True)).to_be_disabled()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SHOTS / f'conflicting-thread-{width}.png')


@pytest.mark.parametrize("width", [390, 768, 1440])
@pytest.mark.parametrize("source", ["gmail", "mailshot"])
def test_mail_link_and_check_incoming(page, fixture_base, width, source):
    page.set_viewport_size({"width": width, "height": 940})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _install_api(page, [], current_board_linked=True)
    now = datetime.now(timezone.utc).isoformat()
    subject = "Device Action Task Failed: 2207"
    item = {"key": f"{source}:mail-1", "source": source, "source_thread_id": "thread-1",
            "source_message_id": "mail-1", "subject": subject, "conversation_label": subject, "sender": "alerts@example.test",
            "created_at": now, "text": "An operation failed. Check the original traceback for details."}
    proposal = {"id": f"local:{source}:thread-1", "origin": "local", "source": source,
                "source_thread_id": "thread-1", "message_ids": ["mail-1"], "project_id": 7,
                "project_name": "DecisionsAI", "title": f"Investigate {subject}",
                "description": "The linked source reports a failed device operation.", "created_at": now,
                "tasks": ["Confirm the affected workflow.", "Trace the exception.", "Verify recovery."]}
    state = {"checked": 0, "writes": [], "fail": width == 1440 and source == "mailshot"}

    def api(route):
        path = urlsplit(route.request.url).path
        if path.endswith("/incoming/mail/project"):
            body = route.request.post_data_json
            state["writes"].append(body)
            assert body == {"source": source, "thread_id": "thread-1", "message_id": "mail-1", "project_id": 7}
            if state["fail"]:
                return route.fulfill(status=503, json={"error": "Could not save the link. Try again."})
            item.update(project_id=7, project_ids=[7])
            return route.fulfill(json={"project_id": 7, "project_name": "DecisionsAI"})
        if path.endswith("/incoming/check"):
            assert route.request.method == "POST"
            state["checked"] += 1
            return route.fulfill(json={"success": True, "checks": {
                "local_mail": {"source_state": "complete", "recalibration": {"updated": 1}}}})
        if path.endswith("/incoming/proposals"):
            return route.fulfill(json={"proposals": [proposal] if state["checked"] else []})
        if path.endswith("/incoming"):
            return route.fulfill(json={"items": [item], "channels": {source: {"connected": True}}})
        return route.fallback()

    page.route("**/api/workflows/studio/incoming**", api)
    page.goto(f"{fixture_base}/development/incoming/")
    page.locator(f"#incoming-folder-{source}").click()
    row = page.locator(f"[data-incoming-{source}-thread]").filter(has_text=subject)
    expect(row).to_be_visible()
    if width == 390:
        row.click()
        page.get_by_role("button", name="Link project", exact=True).click()
    else:
        row.click(button="right")
    dialog = page.locator("#incoming-mail-project-dialog")
    assert dialog.evaluate("(element) => element.open"), errors
    select = dialog.get_by_label("Project", exact=True)
    expect(select).to_be_focused()
    expect(dialog).to_contain_text(subject)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SHOTS / f"{source}-{width}-picker.png")
    select.select_option("7")
    dialog.get_by_role("button", name="Save link").click()
    if state["fail"]:
        expect(dialog.get_by_role("alert")).to_have_text("Could not save the link. Try again.")
        expect(select).to_have_value("7")
        page.screenshot(path=SHOTS / f"{source}-{width}-retry.png")
        state["fail"] = False
        dialog.get_by_role("button", name="Save link").click()
    expect(dialog).to_be_hidden()
    if width != 390:
        expect(row).to_contain_text("DecisionsAI")
        row.click()
    expect(page.get_by_role("button", name="Change project", exact=True)).to_be_visible()
    page.screenshot(path=SHOTS / f"{source}-{width}-linked.png")
    back = page.locator(f"#incoming-panel-{source} [data-incoming-back]")
    if back.is_visible():
        back.click()
    page.locator("#incoming-folder-proposals").click()
    page.get_by_role("button", name="Check incoming", exact=True).click()
    expect(page.locator(".proposal-card")).to_have_count(1)
    expect(page.locator(".proposal-project")).to_have_text("DecisionsAI")
    expect(page.locator("#incoming-check-status")).to_contain_text("1 proposal updated")
    assert state["checked"] == 1
    page.screenshot(path=SHOTS / f"{source}-{width}-proposal.png")
