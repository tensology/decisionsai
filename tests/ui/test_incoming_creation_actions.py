"""Incoming creation controls: scope, pending state, failure and retry."""
import pytest
from playwright.sync_api import expect
from tests.ui.test_development_responsive import BASE, install_responsive_api, capture

pytestmark = pytest.mark.e2e_playwright


@pytest.mark.parametrize('width', [390, 1440])
@pytest.mark.parametrize('source', ['gmail', 'mailshot', 'proposal'])
def test_incoming_creation_failure_retry_and_double_click(page, source, width):
    install_responsive_api(page)
    requests = []
    failed = True
    proposal = {'id': 'local:test-action', 'origin': 'local', 'title': 'Review menu launch',
                'description': 'Review the launch copy.', 'project_id': 7,
                'project_name': 'DecisionsAI', 'source': 'gmail'}
    page.route('**/api/workflows/studio/incoming/proposals*', lambda route:
               route.fulfill(json={'proposals': [proposal]}) if route.request.method == 'GET' else route.fallback())
    endpoint = {'gmail': '/workflows/incoming/gmail/ingest',
                'mailshot': '/workflows/intake/ingest',
                'proposal': '/workflows/studio/incoming/proposals/thread'}[source]

    def action(route):
        requests.append(route.request.post_data_json)
        if failed:
            route.fulfill(status=503, json={'detail': 'Creation temporarily unavailable'})
        elif source == 'proposal':
            route.fulfill(json={'success': True, 'chat_id': 17})
        else:
            route.fulfill(json={'success': True, 'decision': {
                'ticket_id': 42, 'board_id': 70, 'status': 'ticket_created',
                'response_text': 'Ticket #42 created.'}})

    page.route('**/api'+endpoint, action)
    page.add_init_script('''const creationFetch = window.fetch.bind(window);
        window.fetch = async (...args) => {
            if (args[1]?.method === 'POST') await new Promise(resolve => setTimeout(resolve, 500));
            return creationFetch(...args);
        };''')
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(BASE+'/development/incoming/')
    if source == 'proposal':
        button = page.locator('[data-incoming-proposal-thread]')
    else:
        page.locator('#incoming-folder-'+source).click()
        if width == 390:
            page.locator('#incoming-'+source+'-list .incoming-gmail-thread').first.click()
        reader = page.locator('#incoming-'+source+'-reader')
        button = reader.locator('[data-incoming-create-ticket]')
        button.click()
        expect(page.locator('#studio-toast')).to_contain_text('Choose a board')
        assert not requests
        expect(reader.locator('[data-incoming-board]')).to_be_focused()
        reader.locator('[data-incoming-board]').select_option('70')
    button.evaluate('node => {node.click(); node.click();}')
    expect(button).to_be_disabled()
    capture(page, 'incoming-'+source+'-creation-busy', width)
    expect(page.locator('#studio-toast')).to_contain_text('Creation temporarily unavailable')
    expect(button).to_be_enabled()
    assert len(requests) == 1
    capture(page, 'incoming-'+source+'-creation-error', width)
    if source == 'gmail':
        assert requests[0]['force_ticket'] is True
        assert requests[0]['source_thread_id'] == 'gmail-thread-1'
    elif source == 'mailshot':
        assert requests[0]['source'] == 'mailshot'
        assert requests[0]['source_user_id'] == 'Ops <ops@tensology.com>'
        assert 'Tensology deployment' in requests[0]['user_text']
    failed = False
    button.click()
    if source == 'proposal':
        expect(page).to_have_url(BASE+'/development/threads/17/')
    else:
        expect(page.locator('#incoming-'+source+'-reader [data-incoming-open-ticket]')).to_be_visible()
    assert len(requests) == 2
    capture(page, 'incoming-'+source+'-creation-success', width)
