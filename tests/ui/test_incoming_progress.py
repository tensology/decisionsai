"""Visible Incoming operations against real UI with isolated APIs."""
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_incoming_dismiss_undo import fixture_base

pytestmark = pytest.mark.e2e_playwright
SHOTS = Path('/tmp/decisions-incoming-progress')


def install(page, base, *, failure=False):
    _install_api(page, [], current_board_linked=True, direct_execution=True, terminal_session=False)
    page.route('**/assets/img/bolt.png', lambda route: route.fulfill(
        content_type='image/png', body=(Path(__file__).resolve().parents[2] / 'assets/img/bolt.png').read_bytes()))
    rows = [{'id': f'local:whatsapp:request-{i}', 'origin': 'local', 'source': 'whatsapp',
             'project_id': 7, 'project_name': 'DecisionsAI', 'title': f'Review request {i}',
             'description': 'Keep the source files and transcripts.', 'message_ids': [f'm{i}']}
            for i in (1, 2)]
    state = {'calls': [], 'rows': rows, 'checks': {'whatsapp': {'source_state': 'queued', 'recalibration': {'state': 'queued'}}}, 'check_calls': [], 'check_failure': False}

    def check(route):
        state['check_calls'].append(route.request.method)
        if state['check_failure']:
            return route.fulfill(status=503, json={'error': 'Source check unavailable. Please retry.'})
        return route.fulfill(json={'success': True, 'checks': state['checks']})

    page.route('**/api/workflows/studio/incoming/check', check)

    def handler(route):
        path = urlsplit(route.request.url).path
        if path.endswith(('/proposals/merge', '/proposals/thread')):
            state['calls'].append((path, route.request.post_data_json))
            if path.endswith('/proposals/thread') and state.pop('lose_thread_response', False):
                return route.abort('connectionreset')
            if failure:
                return route.fulfill(status=503, json={'error': 'Preparation failed. Try again.'})
            state['rows'] = []
            return route.fulfill(json={'chat_id': 17, 'success': True})
        if path.endswith('/incoming/proposals'):
            return route.fulfill(json={'proposals': state['rows'], 'sync': {'state': 'complete'}})
        return route.fallback()

    page.route('**/api/workflows/studio/incoming/proposals**', handler)
    page.add_init_script("""const originalFetch = window.fetch.bind(window);
        window.fetch = async (...args) => {
            if (/proposals\\/(merge|thread)$/.test(String(args[0]))) await new Promise(resolve => setTimeout(resolve, 1200));
            return originalFetch(...args);
        };""")
    page.goto(base + '/development/incoming/')
    expect(page.locator('.proposal-card')).to_have_count(2)
    return state


def drop(page):
    page.locator('.proposal-card').first.evaluate("""source => {
        const transfer = new DataTransfer();
        source.dispatchEvent(new DragEvent('dragstart', {bubbles:true,dataTransfer:transfer}));
        document.querySelectorAll('.proposal-card')[1].dispatchEvent(new DragEvent('drop', {bubbles:true,dataTransfer:transfer}));
    }""")


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
@pytest.mark.parametrize('operation', ['merge', 'thread'])
def test_progress_precedes_navigation_and_check_action_stays_visible(page, fixture_base, width, operation):
    page.set_viewport_size({'width': width, 'height': 960})
    state = install(page, fixture_base)
    check = page.get_by_role('button', name='Check incoming', exact=True)
    expect(check).to_be_visible()
    assert check.locator('..').locator('#incoming-dismissed-toggle').count() == 1
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f'incoming-{width}.png'))
    if operation == 'merge':
        drop(page)
    else:
        page.locator('[data-incoming-proposal-thread]').first.click()
    modal = page.get_by_role('dialog').filter(has=page.locator('progress'))
    expect(modal).to_be_visible()
    expect(modal).to_contain_text('Merging proposals' if operation == 'merge' else 'Preparing thread')
    expect(modal.locator('progress')).not_to_have_attribute('value', '100')
    expect(page.get_by_role('button', name='Close progress')).to_be_focused()
    page.keyboard.press('Tab')
    expect(page.get_by_role('button', name='Close progress')).to_be_focused()
    bounds = modal.bounding_box()
    assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= width
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f'{operation}-{width}.png'))
    expect(page).to_have_url(fixture_base + '/development/threads/17/')
    if operation == 'merge':
        expect(page.locator('#studio-toast')).to_contain_text('Merged into one thread.')
        page.screenshot(path=str(SHOTS / f'merged-thread-{width}.png'))
    expect(modal).to_have_count(0)
    assert len(state['calls']) == 1
    assert page.evaluate('document.body.style.overflow') != 'hidden'


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_failed_merge_keeps_both_sources_and_retry_enabled(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 960})
    state = install(page, fixture_base, failure=True)
    drop(page)
    expect(page.get_by_role('dialog')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.get_by_role('dialog')).to_have_count(0)
    expect(page.locator('#incoming-operation-status')).to_contain_text('Merging')
    expect(page.locator('#studio-toast')).to_contain_text('Preparation failed')
    expect(page.locator('.proposal-card')).to_have_count(2)
    expect(page.locator('[data-incoming-proposal-thread]').first).to_be_enabled()
    expect(page.locator('#incoming-operation-status')).to_be_hidden()
    page.screenshot(path=str(SHOTS / f'merge-error-{width}.png'))
    drop(page)
    expect(page.get_by_role('dialog')).to_be_visible()
    expect(page.locator('#studio-toast')).to_contain_text('Preparation failed')
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert len(state['calls']) == 2


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_unconfirmed_remote_dismissal_keeps_same_merge_retry_available(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 960})
    install(page, fixture_base)
    message = 'Tensology could not confirm dismissal of the original proposal. Retry the same merge.'
    attempts = []

    def handle_merge(route):
        attempts.append(route.request.post_data_json)
        if len(attempts) == 1:
            return route.fulfill(status=409, json={'error': message})
        return route.fallback()

    page.route('**/api/workflows/studio/incoming/proposals/merge', handle_merge)
    drop(page)
    notice = page.locator('#studio-toast')
    expect(notice).to_have_text(message)
    expect(page.locator('.proposal-card')).to_have_count(2)
    expect(page).to_have_url(fixture_base + '/development/incoming/')
    expect(page.get_by_role('dialog')).to_have_count(0)
    bounds = notice.bounding_box()
    assert bounds and 0 <= bounds['x'] and bounds['x'] + bounds['width'] <= width
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f'merge-dismissal-unconfirmed-{width}.png'), animations='disabled')

    drop(page)
    expect(page).to_have_url(fixture_base + '/development/threads/17/')
    expect(page.locator('#studio-toast')).to_have_text('Merged into one thread.')
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    page.screenshot(path=str(SHOTS / f'merge-dismissal-retried-{width}.png'), animations='disabled')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
@pytest.mark.parametrize('local_recovery', [False, True], ids=['receipt', 'mixed-intent'])
@pytest.mark.browser_context_args(has_touch=True)
def test_resume_saved_merge_after_reload_does_not_start_work(page, fixture_base, width, local_recovery):
    page.set_viewport_size({'width': width, 'height': 960})
    install(page, fixture_base)
    merge_key = 'merge:' + 'a' * 64
    recovery = {'chat_id': 17, 'merge_key': merge_key, 'title': 'Merged delivery', 'project_name': 'DecisionsAI'}
    if local_recovery:
        del recovery['chat_id']
        recovery['local_recovery'] = True
    kind = 'mixed' if local_recovery else 'receipt'
    attempts = []
    notice = page.locator('#studio-toast')
    message = 'An original proposal changed after it was merged. Review the merge before resuming.'

    def proposals(route):
        path = urlsplit(route.request.url).path
        if route.request.method == 'GET' and path.endswith('/incoming/proposals'):
            return route.fulfill(json={'proposals': [], 'pending_merges': [recovery], 'sync': {'state': 'complete'}})
        if route.request.method == 'POST':
            attempts.append((path, route.request.post_data_json))
            if len(attempts) == 1:
                return route.fulfill(status=409, json={'error': message})
            return route.fulfill(json={'chat_id': 17, 'review_key': 'b' * 64, 'success': True,
                                       'failed_attachments': [{'name': 'scope.txt'}] if local_recovery else []})
        return route.fallback()

    page.route('**/api/workflows/studio/incoming/proposals**', proposals)
    unavailable = ['scope.txt'] if local_recovery else []
    telegram = {'state': 'unconfirmed', 'message': 'Telegram update unconfirmed. Check the original Telegram conversation before sending again.'} if local_recovery else {}
    file_url = '/api/workflows/studio/tasks/17/attachments/' + 'a' * 24

    def thread(route):
        file = {'name': 'scope.txt', 'mime_type': 'text/plain', 'url': file_url}
        if unavailable:
            file.update(url='', download_error='The saved attachment is missing or changed.')
        return route.fulfill(json={
            'id': 17, 'title': 'Merged delivery', 'project_id': 7, 'development': True,
            'development_board_key': 'decisions:70', 'unavailable_attachment_names': unavailable,
            'telegram_proposal_update_status': telegram,
            'messages': [{'role': 'user', 'content': 'Merged proposal scope.', 'attachments': [file]}],
        })

    page.route('**/api/chats/17?*', thread)
    page.reload()
    button = page.get_by_role('button', name='Resume merge', exact=True)
    expect(button).to_be_visible()
    expect(page.get_by_role('heading', name='Merged delivery')).to_be_visible()
    assert attempts == []
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SHOTS / f'resume-{kind}-saved-{width}.png', animations='disabled')
    button.tap()
    expect(notice).to_have_text(message)
    expect(button).to_be_focused()
    page.screenshot(path=SHOTS / f'resume-{kind}-held-{width}.png', animations='disabled')
    button.tap()
    expect(page).to_have_url(fixture_base + '/development/threads/17/')
    expect(page.locator('#header-task')).to_have_text('Merged delivery')
    expect(notice).to_have_text('Development thread opened. 1 file could not be fetched: scope.txt'
                                if local_recovery else 'Merged into one thread.')
    endpoint = '/api/workflows/studio/incoming/proposals/merge'
    expected = (endpoint, {'local_recovery': merge_key}) if local_recovery else (endpoint + '/17/resume', {'merge_key': merge_key})
    assert attempts == [expected] * 2
    page.screenshot(path=SHOTS / f'resume-{kind}-opened-{width}.png', animations='disabled')
    warning = page.locator('#thread-attachment-warning')
    delivery = page.locator('#thread-telegram-status')
    preview = page.get_by_role('button', name='Open file: scope.txt', exact=True)
    if local_recovery:
        page.reload()
        expect(warning).to_be_visible()
        expect(warning).to_have_text('Files unavailable: scope.txt')
        expect(delivery).to_have_text(telegram['message'])
        expect(page.locator('.thread-source-file-failed')).to_have_text('Unavailable')
        expect(preview).to_have_count(0)
        assert warning.evaluate("""banner => banner.getBoundingClientRect().bottom
            <= document.querySelector('#conversation').getBoundingClientRect().top""")
        assert attempts == [expected] * 2
        page.screenshot(path=SHOTS / f'attachment-warning-{width}.png', animations='disabled')
        unavailable.clear()
        telegram.update(state='sent', message='Last Telegram update sent.')
        page.reload()
        expect(page.locator('#header-task')).to_have_text('Merged delivery')
    expect(warning).to_be_hidden()
    expect(preview).to_be_visible()
    if local_recovery:
        expect(delivery).to_have_text(telegram['message'])
        page.screenshot(path=SHOTS / f'attachment-warning-cleared-{width}.png', animations='disabled')
    else:
        expect(delivery).to_be_hidden()


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_open_thread_refreshes_telegram_receipt_without_reloading_transcript(page, fixture_base, width):
    import time
    from distr.core.incoming.telegram_updates import ACK_TIMEOUT_SECONDS, public_proposal_update_status

    requests = []
    _install_api(page, requests, current_board_linked=True, direct_execution=True, terminal_session=False)
    page.set_viewport_size({'width': width, 'height': 960})
    previous_result = {'chat_id': 12345, 'job_id': 'original', 'outcome': {'stage': 'done', 'text': 'Original result'}}
    params = {'incoming_proposal_handoff': {
        'telegram_approval': {'chat_id': 12345, 'job_id': 'retry'},
        'telegram_previous_approvals': [previous_result],
    }}
    summary = {'id': 17, 'title': 'Merged delivery', 'project_id': 7, 'development': True,
               'development_board_key': 'decisions:70', 'archived': False,
               'telegram_proposal_update_status': public_proposal_update_status(params)}
    detail_reads = []

    def thread(route):
        detail_reads.append(route.request.url)
        route.fulfill(json={**summary, 'messages': [{'role': 'assistant', 'content': 'The work is ready.', 'chat_row_id': 28}]})

    page.route('**/api/chats?*', lambda route: route.fulfill(json={'chats': [summary]}))
    page.route('**/api/chats/17?*', thread)
    page.route_web_socket('**/api/ws?*', lambda socket: socket.on_message(lambda message: None))
    page.goto(fixture_base + '/development/threads/17/')
    banner = page.locator('#thread-telegram-status')
    expect(banner).to_have_text('Telegram update queued.')
    message = page.locator('#message-list').get_by_text('The work is ready.', exact=True)
    expect(message).to_be_visible()
    message.evaluate('(node) => { window.originalResult = node; }')
    draft = page.locator('#task-prompt')
    draft.fill('Keep this unsent follow-up.')
    draft.press('Home')
    reads = len(detail_reads)
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SHOTS / f'telegram-retained-result-{width}.png', animations='disabled')
    attempt = {'token': 'proposal-update:17:fixture', 'state': 'sending', 'end': 15, 'at': time.time()}
    params['telegram_proposal_update'] = {'recipient_id': 12345, 'pending': {
        'key': 'original:done', 'text': 'Original result', 'awaiting': attempt,
    }}
    summary['telegram_proposal_update_status'] = public_proposal_update_status(params)
    expect(banner).to_have_text('Telegram update sending.', timeout=12000)
    attempt['at'] -= ACK_TIMEOUT_SECONDS + 1
    summary['telegram_proposal_update_status'] = public_proposal_update_status(params)
    expect(banner).to_contain_text('Telegram update unconfirmed.', timeout=12000)
    expect(draft).to_be_focused()
    page.screenshot(path=SHOTS / f'telegram-offline-timeout-{width}.png', animations='disabled')
    params['telegram_proposal_update'] = {'recipient_id': 12345, 'sent': 'original:done',
                                          'pending': {'key': 'original:done', 'text': 'Already delivered'}}
    summary['telegram_proposal_update_status'] = public_proposal_update_status(params)
    expect(banner).to_have_text('Last Telegram update sent.', timeout=12000)
    expect(draft).to_have_value('Keep this unsent follow-up.')
    expect(draft).to_be_focused()
    assert draft.evaluate('(node) => node.selectionStart') == 0
    assert message.evaluate('(node) => node === window.originalResult')
    assert len(detail_reads) == reads
    assert ('POST', '/api/chats/17/message') not in requests
    page.screenshot(path=SHOTS / f'telegram-duplicate-confirmed-{width}.png', animations='disabled')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_development_merge_attachment_conflict_keeps_draft_for_retry(page, fixture_base, width):
    from distr.core.incoming.proposals import ReviewedMergeAttachmentUnavailable

    _install_api(page, [], current_board_linked=True, direct_execution=True, terminal_session=False)
    page.set_viewport_size({'width': width, 'height': 960})
    draft = 'Work from the reviewed attachment.'
    accepted = False
    submits = []
    detail = str(ReviewedMergeAttachmentUnavailable(['scope.txt']))

    def chat(route):
        messages = [{'role': 'user', 'content': draft, 'chat_row_id': 21}] if accepted else []
        route.fulfill(json={'id': 17, 'title': 'Merged delivery', 'project_id': 7, 'development': True,
                           'development_board_key': 'decisions:70', 'messages': messages,
                           'unavailable_attachment_names': [] if accepted else ['scope.txt']})

    def submit(route):
        nonlocal accepted
        submits.append(route.request.post_data_json['message'])
        if len(submits) == 1:
            return route.fulfill(status=409, json={'code': 'proposal_attachment_unavailable', 'detail': detail})
        accepted = True
        return route.fulfill(json={'success': True, 'started': True, 'turn_id': 21})

    page.route('**/api/chats/17?*', chat)
    page.route('**/api/workflows/studio/tasks/17/execution', lambda route: route.fulfill(json={}))
    page.route('**/api/workflows/studio/tasks/17/messages', submit)
    page.goto(f'{fixture_base}/development/threads/17/')
    warning = page.locator('#thread-attachment-warning')
    expect(warning).to_have_text('Files unavailable: scope.txt')
    composer = page.locator('#task-prompt')
    composer.fill(draft)
    page.locator('#send-button').click()
    expect(page.locator('#studio-toast')).to_have_text(detail)
    expect(composer).to_have_value(draft)
    expect(page.get_by_text(draft, exact=True)).to_have_count(0)
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SHOTS / f'thread-merge-attachment-held-{width}.png', animations='disabled')
    page.locator('#send-button').click()
    expect(warning).to_be_hidden()
    expect(page.locator('#studio-toast')).to_have_text('Work started.')
    expect(composer).to_have_value('')
    expect(page.locator('#message-list').get_by_text(draft, exact=True)).to_have_count(1)
    assert submits == [draft, draft]
    page.screenshot(path=SHOTS / f'thread-merge-attachment-restored-{width}.png', animations='disabled')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
@pytest.mark.parametrize('blocked_file', [False, True])
def test_blocked_merge_launch_remains_in_incoming(page, fixture_base, width, blocked_file):
    page.set_viewport_size({'width': width, 'height': 960})
    install(page, fixture_base)
    message = ('Work did not start. Restore the reviewed attachment files and retry: scope.txt' if blocked_file
               else 'Finish merging the original proposals before starting the replacement.')
    error = {'error': message}
    if blocked_file:
        error['code'] = 'proposal_attachment_unavailable'
    page.route('**/api/workflows/studio/incoming/proposals/thread',
               lambda route: route.fulfill(status=409, json=error))
    page.locator('[data-incoming-proposal-thread]').first.click()
    expect(page.locator('#studio-toast')).to_have_text(message)
    expect(page).to_have_url(fixture_base + '/development/incoming/')
    expect(page.locator('.proposal-card')).to_have_count(2)
    expect(page.get_by_role('dialog').filter(has=page.locator('progress'))).to_have_count(0)
    SHOTS.mkdir(parents=True, exist_ok=True)
    state = 'merge-attachment-blocked' if blocked_file else 'merge-handoff-pending'
    page.screenshot(path=str(SHOTS / f'{state}-{width}.png'), animations='disabled')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_retry_after_lost_proposal_response_opens_existing_thread(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 960})
    state = install(page, fixture_base)
    state['lose_thread_response'] = True
    button = page.locator('[data-incoming-proposal-thread]').first
    button.click()
    expect(page.get_by_role('dialog').filter(has=page.locator('progress'))).to_be_visible()
    expect(page.locator('#studio-toast')).to_contain_text('Connection interrupted. Retry to open the proposal thread.')
    expect(button).to_be_enabled()
    expect(page.locator('.proposal-card')).to_have_count(2)
    expect(page).to_have_url(fixture_base + '/development/incoming/')
    notice = page.locator('#studio-toast').bounding_box()
    assert 0 <= notice['x'] and notice['x'] + notice['width'] <= width
    assert 0 <= notice['y'] and notice['y'] + notice['height'] <= 960
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f'proposal-response-lost-{width}.png'))

    button.click()
    expect(page).to_have_url(fixture_base + '/development/threads/17/')
    toast = page.locator('#studio-toast')
    expect(toast).to_contain_text('Development thread opened.')
    expect(page.get_by_role('dialog').filter(has=page.locator('progress'))).to_have_count(0)
    assert len(state['calls']) == 2 and state['calls'][0] == state['calls'][1]
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    bounds = toast.bounding_box()
    composer = page.locator('#studio-composer').bounding_box()
    assert 0 <= bounds['x'] and bounds['x'] + bounds['width'] <= width
    assert bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= composer['y']
    page.screenshot(path=str(SHOTS / f'proposal-response-recovered-{width}.png'))


def test_opening_whatsapp_acknowledges_only_current_messages(page, fixture_base):
    install(page, fixture_base)
    now = datetime.now(timezone.utc).isoformat()
    items = [{'key': f'whatsapp:{i}', 'source': 'whatsapp', 'source_thread_id': f'contact-{i}',
              'source_message_id': f'message-{i}', 'created_at': now, 'sender': f'Contact {i}',
              'conversation_label': f'Contact {i}', 'text': 'Review this request', 'processed': False}
             for i in (1, 2)]
    page.route('**/api/workflows/studio/incoming?*', lambda route: route.fulfill(json={'items': items, 'links': [], 'channels': {}}))
    page.reload()
    page.locator('[data-incoming-filter="whatsapp"]').click()
    second = page.locator('[data-incoming-conversation="whatsapp:contact-2"]')
    expect(second.locator('.incoming-new-dot')).to_have_count(1)
    second.click()
    expect(second.locator('.incoming-new-dot')).to_have_count(0)
    page.reload()
    page.locator('[data-incoming-filter="whatsapp"]').click()
    expect(second.locator('.incoming-new-dot')).to_have_count(0)


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_explicit_check_queues_once_and_reports_ai_progress_separately(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 960})
    state = install(page, fixture_base)
    assert state['check_calls'] == []
    button = page.get_by_role('button', name='Check incoming', exact=True)
    button.click()
    status = page.locator('#incoming-check-status')
    expect(status).to_contain_text('Checking sources and proposal project assignments')
    page.screenshot(path=str(SHOTS / f'checking-{width}.png'))
    state['checks'] = {'whatsapp': {'source_state': 'complete', 'recalibration': {'state': 'complete', 'updated': 2}}}
    expect(status).to_have_text('Incoming checked. 2 proposals updated.', timeout=10_000)
    page.screenshot(path=str(SHOTS / f'checked-{width}.png'))
    assert state['check_calls'] == ['POST', 'GET']
    state['check_failure'] = True
    button.click()
    expect(status).to_contain_text('Source check unavailable')
    page.screenshot(path=str(SHOTS / f'check-error-{width}.png'))
    expect(page.locator('.proposal-card')).to_have_count(2)
