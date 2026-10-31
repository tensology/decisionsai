"""Responsive Development checks using the real shell and isolated API fixtures."""
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.ui.test_development_modules_playwright import BASE, install_plan

pytestmark = pytest.mark.e2e_playwright
OUTPUT = Path('/tmp/decisions-development-responsive')


def install_responsive_api(page):
    page.set_default_timeout(5000)
    workspace, requests = install_plan(page)
    page.route('**/api/events/stream', lambda route: route.fulfill(body='', content_type='text/event-stream'))
    page.route('**/api/workflows/studio/sidebar/pins', lambda route: route.fulfill(json={}))
    page.route('**/api/workflows/studio/plan-workspaces/101/conversation', lambda route: route.fulfill(json={
        'status': 'idle', 'messages': [{'role': 'assistant', 'content': 'The project brief and requirements are ready to review.'}],
        'provider': 'openai', 'model_name': 'gpt-test',
    }))
    page.route('**/api/automations', lambda route: route.fulfill(json={'automations': [{
        'id': 'auto_1', 'name': 'Weekday visual verification with a long descriptive title',
        'instruction': 'Review the project and report meaningful changes', 'status': 'active',
        'schedule': {'kind': 'daily', 'time': '08:00', 'timezone': 'Africa/Johannesburg'},
        'project_name': 'DecisionsAI', 'next_run_at': '2026-10-29T08:00:00Z',
    }]}))
    page.route('**/api/workflows/studio/reports?*', lambda route: route.fulfill(json={'items': [{
        'id': 'workflow:1', 'kind': 'workflow', 'name': 'Release verification with a descriptive workflow title',
        'status': 'completed', 'project_name': 'DecisionsAI', 'started_at': '2026-09-05T09:00:00', 'duration_seconds': 48,
    }]}))
    page.route('**/api/workflows/studio/reports/time?*', lambda route: route.fulfill(json={'items': [{
        'id': 'time:1', 'chat_id': 17, 'thread_title': 'Timed implementation review', 'ticket_title': 'DEV-42',
        'board_key': 'decisions:70', 'started_at': '2026-09-05T09:00:00', 'ended_at': '2026-09-05T09:05:00',
        'seconds': 300, 'source': 'play',
    }]}))
    page.route('**/api/workflows/studio/reports/costs?*', lambda route: route.fulfill(json={
        'display': 'explicit' if 'display=explicit' in route.request.url else 'blended',
        'items': [{'run_id': 99, 'provider': 'openai', 'model': 'gpt-test', 'source': 'workflow',
                   'client_key': 'decisions-review', 'day_sast': '2026-09-05', 'recorded_at': 1788598800,
                   'tokens_in': 100, 'tokens_out': 50, 'tokens_total': 150, 'provider_cost_usd': .02,
                   'resource_cost_usd': .01, 'cost_usd': .03, 'resource_notes': 'Local resource estimate'}],
        'rollups': {'total_cost_usd': .03, 'by_day': [{'day_sast': '2026-09-05'}]},
    }))
    return workspace, requests


def capture(page, name, width):
    OUTPUT.mkdir(exist_ok=True)
    page.screenshot(path=str(OUTPUT / f'{name}-{width}.png'), full_page=True, animations='disabled')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_incoming_list_detail_back_and_channel_switch(page: Page, width):
    install_responsive_api(page)
    page.route('**/api/workflows/studio/incoming/proposals*', lambda route: route.fulfill(json={'proposals': [{'id': 'test-proposal', 'title': 'Review the menu launch', 'project_name': 'DecisionsAI', 'project_id': 7, 'source': 'whatsapp', 'source_label': 'WhatsApp', 'description': 'Review the launch copy and vegetarian menu filters before publishing.'}]}))
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(BASE + '/development/incoming/')
    expect(page.locator('[data-incoming-filter]')).to_have_count(4)
    expect(page.locator('#incoming-folder-telegram')).to_have_count(0)
    expect(page.locator('.proposal-card')).to_be_visible()
    capture(page, 'incoming-proposals', width)
    page.locator('[data-incoming-filter="whatsapp"]').click()
    expect(page.locator('[data-incoming-conversation]').first).to_be_visible()
    compact = width <= 980
    if compact:
        expect(page.locator('#incoming-whatsapp-reader')).to_be_hidden()
        labels = page.locator('[data-incoming-filter] > strong')
        assert labels.evaluate_all('nodes => nodes.every(node => getComputedStyle(node).position !== "absolute" && getComputedStyle(node).fontSize === "12px" && node.scrollWidth <= node.clientWidth)')
    capture(page, 'incoming', width)
    page.locator('[data-incoming-conversation]').first.click()
    expect(page.locator('#incoming-whatsapp-reader')).to_be_visible()
    if compact:
        expect(page.locator('#incoming-list')).to_be_hidden()
        page.locator('#incoming-panel-whatsapp [data-incoming-back]').click()
        expect(page.locator('#incoming-list')).to_be_visible()
        expect(page.locator('#incoming-list .selected')).to_be_focused()
        page.locator('#incoming-list .selected').click()
    assert page.locator('#incoming-whatsapp-reader > footer').evaluate('node => node.getBoundingClientRect().bottom <= node.closest(".incoming-folder-containers").getBoundingClientRect().bottom')
    capture(page, 'incoming-detail', width)
    for channel in ['gmail', 'mailshot']:
        page.locator(f'[data-incoming-filter="{channel}"]').click()
        panel = page.locator(f'#incoming-panel-{channel}')
        expect(panel).to_be_visible()
        if compact:
            expect(page.locator(f'#incoming-{channel}-reader')).to_be_hidden()
        row = panel.locator('.incoming-gmail-thread').first
        if row.count():
            row.click()
            expect(page.locator(f'#incoming-{channel}-reader')).to_be_visible()
            assert page.locator(f'#incoming-{channel}-reader > footer').evaluate('node => node.getBoundingClientRect().bottom <= node.closest(".incoming-folder-containers").getBoundingClientRect().bottom')
            capture(page, f'incoming-{channel}-detail', width)
            if compact:
                panel.locator('[data-incoming-back]').click()
                expect(row).to_be_visible()
        else:
            capture(page, f'incoming-{channel}-empty', width)
    assert page.locator('#incoming-resync').evaluate('(node) => node.getBoundingClientRect().right <= innerWidth')


@pytest.mark.parametrize('width', [390, 1440])
def test_incoming_resync_busy_error_and_recovery(page: Page, width):
    install_responsive_api(page)
    page.set_viewport_size({'width': width, 'height': 900})
    page.add_init_script('''const incomingFetch = window.fetch.bind(window);
        window.fetch = async (...args) => {
            if (String(args[0]).includes('/studio/incoming')) await new Promise(resolve => setTimeout(resolve, 400));
            return incomingFetch(...args);
        };''')
    page.route('**/api/workflows/studio/incoming/proposals*', lambda route: route.fulfill(json={'proposals': []}))
    page.goto(BASE + '/development/incoming/')
    resync = page.locator('#incoming-resync')
    expect(resync).to_be_enabled()
    page.locator('#incoming-folder-gmail').click()
    page.route('**/api/workflows/studio/incoming?*', lambda route: route.fulfill(status=503, json={'detail': 'Temporary inbox outage'}))
    resync.click()
    expect(resync).to_be_disabled()
    expect(resync).to_have_attribute('aria-busy', 'true')
    capture(page, 'incoming-resync-busy', width)
    expect(page.locator('#incoming-gmail-list')).to_contain_text('Incoming is temporarily unavailable')
    expect(resync).to_be_enabled()
    capture(page, 'incoming-error', width)
    page.unroute('**/api/workflows/studio/incoming?*')
    resync.click()
    expect(page.locator('#incoming-gmail-list .incoming-gmail-thread')).to_have_count(1)
    expect(resync).to_be_enabled()
    page.locator('#incoming-folder-proposals').click()
    expect(page.locator('#incoming-proposals-list')).to_contain_text('No proposals right now.')
    capture(page, 'incoming-proposals-empty', width)


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_proposal_status_centered_loading_and_refresh(page: Page, width):
    install_responsive_api(page)
    page.set_viewport_size({'width': width, 'height': 900})
    response = {'proposals': []}
    page.route('**/api/workflows/studio/incoming/proposals*', lambda route: route.fulfill(json=response))
    page.add_init_script('''
        window.holdProposals = true;
        const originalFetch = window.fetch.bind(window);
        window.fetch = async (...args) => {
            if (window.holdProposals && String(args[0]).includes('/studio/incoming/proposals'))
                await new Promise(resolve => { window.releaseProposals = resolve; });
            return originalFetch(...args);
        };
    ''')
    page.goto(BASE + '/development/incoming/')
    status = page.locator('.proposal-list-status')
    listing = page.locator('#incoming-proposals-list')
    resync = page.locator('#incoming-resync')

    def centered():
        assert status.evaluate('''node => {
            const text = node.getBoundingClientRect();
            const panel = node.closest('.incoming-folder-panel').getBoundingClientRect();
            return Math.abs(text.x + text.width / 2 - panel.x - panel.width / 2) < 2
                && Math.abs(text.y + text.height / 2 - panel.y - panel.height / 2) < 2;
        }''')

    def release():
        page.wait_for_function('typeof window.releaseProposals === "function"')
        page.evaluate('() => { window.holdProposals = false; window.releaseProposals(); delete window.releaseProposals; }')

    expect(status).to_have_text('Loading proposals...')
    expect(listing).to_have_attribute('aria-busy', 'true')
    expect(status.locator('.proposal-loading-spinner')).to_be_visible()
    centered()
    capture(page, 'proposal-status-loading', width)
    release()
    expect(status).to_have_text('No proposals right now.')
    expect(listing).to_have_attribute('aria-busy', 'false')
    expect(status.locator('.proposal-loading-spinner')).to_have_count(0)
    centered()
    capture(page, 'proposal-status-empty', width)
    response['error'] = 'Proposals are temporarily unavailable.'
    resync.click()
    expect(status).to_have_text(response['error'])
    centered()
    capture(page, 'proposal-status-error', width)
    response.clear()
    response['proposals'] = [{'id': 'status-check', 'title': 'Review the menu launch', 'project_name': 'DecisionsAI', 'project_id': 7, 'source': 'whatsapp', 'source_label': 'WhatsApp'}]
    resync.click()
    expect(page.locator('.proposal-card')).to_be_visible()
    expect(resync).to_be_enabled()
    page.evaluate('window.holdProposals = true')
    resync.click()
    expect(listing).to_have_attribute('aria-busy', 'true')
    expect(page.locator('.proposal-card')).to_be_visible()
    expect(status).to_have_count(0)
    release()
    expect(resync).to_be_enabled()
    page.emulate_media(reduced_motion='reduce')
    response['proposals'] = []
    resync.click()
    expect(status).to_have_text('No proposals right now.')
    expect(resync).to_be_enabled()
    page.evaluate('window.holdProposals = true')
    resync.click()
    expect(status.locator('.proposal-loading-spinner')).to_be_visible()
    assert status.locator('.proposal-loading-spinner').evaluate('node => getComputedStyle(node).animationName') == 'none'
    release()


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_report_views_preserve_every_column_and_filters(page: Page, width):
    install_responsive_api(page)
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(BASE + '/development/reports/')
    for tab in ['runs', 'time', 'costs']:
        page.locator(f'#reports-tab-{tab}').click()
        expect(page.locator('#reports-results tbody tr')).to_have_count(1)
        expect(page.locator('#reports-run-filters')).to_be_visible() if tab == 'runs' else expect(page.locator('#reports-run-filters')).to_be_hidden()
        expect(page.locator('#reports-cost-filters')).to_be_visible() if tab == 'costs' else expect(page.locator('#reports-cost-filters')).to_be_hidden()
        assert page.locator('#reports-results').evaluate('''root => [...root.querySelectorAll('th')].every(th => th.scope === 'col') && [...root.querySelectorAll('tbody tr')].every(row => [...row.cells].every((cell, i) => cell.dataset.label === root.querySelectorAll('th')[i].textContent))''')
        if width <= 980:
            assert page.locator('.reports-table-scroll').evaluate('node => node.scrollWidth <= node.clientWidth')
        capture(page, f'reports-{tab}', width)
    page.locator('#reports-cost-display').select_option('explicit')
    expect(page.locator('#reports-results th')).to_have_count(8)
    capture(page, 'reports-explicit', width)
    page.route('**/api/workflows/studio/reports?*', lambda route: route.fulfill(status=503, json={'detail': 'Temporary report outage'}))
    page.locator('#reports-refresh').click()
    expect(page.locator('#reports-results')).to_contain_text('Use Refresh to retry')
    capture(page, 'reports-error', width)


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_automation_list_calendar_and_dialogs_fit(page: Page, width):
    install_responsive_api(page)
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(BASE + '/development/automations/')
    expect(page.locator('[data-scheduled-row="auto_1"]')).to_be_visible()
    capture(page, 'automations', width)
    assert page.locator('.scheduled-table').evaluate('node => node.scrollWidth <= node.clientWidth')
    for action in ['schedule', 'settings', 'history']:
        page.locator(f'[data-row-action="{action}"]').click()
        dialog = page.get_by_role('dialog').filter(visible=True)
        expect(dialog).to_be_visible()
        assert dialog.evaluate('node => node.getBoundingClientRect().left >= 0 && node.getBoundingClientRect().right <= innerWidth')
        capture(page, f'automation-{action}', width)
        dialog.locator('.scheduled-history-close').click()
    page.locator('[data-scheduled-view="calendar"]').click()
    for mode in ['day', 'week', 'month']:
        page.locator(f'[data-scheduled-calendar-mode="{mode}"]').click()
        expect(page.locator('#scheduled-calendar')).to_be_visible()
        if width <= 980:
            assert page.locator('#scheduled-calendar').evaluate('node => node.getBoundingClientRect().bottom <= innerHeight')
            assert page.locator('#scheduled-calendar-toolbar').evaluate('node => node.scrollWidth <= node.clientWidth')
        capture(page, f'automation-calendar-{mode}', width)
        if mode in ['week', 'month'] and width <= 980:
            calendar = page.locator('#scheduled-calendar')
            assert calendar.evaluate('node => getComputedStyle(node).overflowX === "auto"')
            calendar.evaluate('node => node.scrollLeft = node.scrollWidth')
            assert calendar.evaluate('node => node.scrollLeft > 0')
            capture(page, f'automation-calendar-{mode}-end', width)
            calendar.evaluate('node => node.scrollLeft = 0')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_development_primary_views_and_editors_fit(page: Page, width):
    install_responsive_api(page)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.set_viewport_size({'width': width, 'height': 900})
    for name, route, selector in [
        ('new', 'new/', '#task-prompt'), ('thread', 'threads/17/', '#task-prompt'),
        ('plan-home', 'plan/', '#plan-workspace'), ('plan', 'boards/jira/jira-1/plan/', '#plan-messages'),
        ('terminals', 'terminals/', '[data-terminal-project="7"]'),
        ('terminal-detail', 'terminals/7/', '.terminal-detail-shell'),
        ('board', 'boards/decisions/70/kanban/', '#development-kanban-lanes'),
    ]:
        page.goto(BASE + '/development/' + route)
        expect(page.locator(selector)).to_be_visible()
        capture(page, name, width)
        if name == 'plan':
            page.locator('[data-plan-tab="requirements"]').click()
            capture(page, 'plan-artifacts', width)
        if name == 'board':
            page.locator('#development-kanban-view-list').click()
            expect(page.locator('.development-kanban-card.list-row')).to_be_visible()
            capture(page, 'board-list', width)
            page.locator('.development-kanban-card.list-row').dblclick()
            expect(page.locator('#kanban-ticket-dialog')).to_be_visible()
            capture(page, 'ticket-dialog', width)
    page.goto(BASE + '/development/workflows/')
    expect(page.locator('[data-workflow-card="44"]')).to_be_visible()
    capture(page, 'workflows', width)
    page.locator('[data-workflow-card-action="open"]').first.click()
    expect(page.locator('.workflow-loop-map')).to_be_visible()
    capture(page, 'workflow-editor', width)
    page.locator('[data-workflow-view="list"]').click()
    expect(page.locator('.workflow-editor-step')).to_be_visible()
    capture(page, 'workflow-list', width)
    page.locator('[data-workflow-step-edit]').first.click()
    expect(page.locator('.workflow-step-drawer')).to_be_visible()
    capture(page, 'workflow-step-dialog', width)
    page.locator('[data-workflow-drawer-close]').click()
    page.locator('[data-workflow-settings-open]').click()
    expect(page.locator('[data-workflow-settings-modal]')).to_be_visible()
    page.locator('[data-workflow-settings-modal] .workflow-advanced-section > summary').first.click()
    capture(page, 'workflow-settings', width)
    assert errors == []
