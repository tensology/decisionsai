from pathlib import Path

import pytest
from playwright.sync_api import expect
from tests.ui.test_development_harness_playwright import _install_api, URL

pytestmark = pytest.mark.e2e_playwright
SCREENSHOTS = Path('/tmp/decisions-activity-disclosure')


def events(failed=False):
    return [
        {'event_id': 'edit', 'event_type': 'tool_completed', 'status': 'completed',
         'title': 'Edit', 'summary': 'Edit finished.', 'detail': 'Updated scripts/sync_database.py',
         'metadata': {'tool_name': 'edit'}},
        {'event_id': 'command', 'event_type': 'tool_failed' if failed else 'tool_completed',
         'status': 'failed' if failed else 'completed', 'title': 'Bash',
         'summary': 'Database restore failed.' if failed else 'Database restore completed.',
         'detail': 'ERROR: missing pg_dump' if failed else 'Restored local database.\nExit code: 0',
         'metadata': {'tool_name': 'bash', 'command': '/Users/paul/.virtualenvs/relightsa/bin/python scripts/sync_database.py'}},
    ]


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
@pytest.mark.parametrize('state', ['live', 'completed', 'failed'])
def test_activity_is_quiet_expandable_and_readable(page, width, state):
    running = state == 'live'
    rows = events(state == 'failed')
    _install_api(page, [], live_activity=running, direct_execution=True,
                 assistant_markdown=not running, completed_activity=not running,
                 activity_events=rows)
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(URL)
    page.locator('#studio-sidebar').evaluate("node => node.classList.remove('open')")
    disclosure = page.locator('.live-activity' if running else '.turn-activity-timeline')
    expect(disclosure).to_be_visible()
    expect(disclosure).not_to_have_attribute('open', '')
    expect(page.get_by_text('Updated files', exact=True)).not_to_be_visible()
    if not running:
        expect(page.locator('.message-markdown h3')).to_have_text('Result')
    SCREENSHOTS.mkdir(exist_ok=True)
    page.screenshot(path=str(SCREENSHOTS / f'{state}-closed-{width}.png'))
    summary = disclosure.locator(':scope > summary')
    summary.focus()
    page.keyboard.press('Enter')
    expect(disclosure).to_have_attribute('open', '')
    expect(disclosure).to_contain_text('scripts/sync_database.py')
    command = disclosure.locator('details.event-commands')
    expect(command).to_be_visible()
    command.locator('summary').click()
    expect(command.locator('pre')).to_contain_text('ERROR: missing pg_dump' if state == 'failed' else 'Exit code: 0')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(SCREENSHOTS / f'{state}-open-{width}.png'))
    summary.focus()
    page.keyboard.press('Enter')
    expect(disclosure).not_to_have_attribute('open', '')


def test_live_activity_stays_open_and_keeps_keyboard_focus_when_events_arrive(page):
    page.goto(URL.split('/development/')[0] + '/robots.txt')
    page.set_content('<div id="studio-empty"></div><main id="conversation"><div id="message-list"></div></main>')
    page.evaluate("""async () => {
        const {createThreadsTranscript} = await import('/static/development/threads/transcript/index.js?v=20261030-activity-disclosure');
        const context = {currentChat: {id: 17, messages: [], active_turn: {turn_id: 19, events: []}}, currentRun: {status: 'running', direct: true}};
        context.currentChat.active_turn.events = [{event_id:'cmd', event_type:'tool_completed', title:'Bash', summary:'Restore complete', detail:'Exit code: 0', metadata:{tool_name:'bash', command:'restore-local-db'}}];
        const actions = {escapeHtml: value => String(value || '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'), bindConversationActions:()=>{}, isActiveAgentRun:()=>false};
        const transcript = createThreadsTranscript({context, actions, el:id=>document.getElementById(id)});
        transcript.renderConversation(context.currentChat);
        window.activityRefreshTest = {context, transcript};
    }""")
    disclosure = page.locator('.live-activity')
    summary = disclosure.locator(':scope > summary')
    summary.click()
    command = disclosure.locator('details.event-commands')
    command.locator('summary').click()
    expect(command).to_have_attribute('open', '')
    summary.focus()
    page.evaluate("""() => {
        const {context, transcript} = window.activityRefreshTest;
        context.currentChat.active_turn.events.push({event_id:'new-read', event_type:'tool_completed', title:'Read', summary:'Checked import results.', metadata:{tool_name:'read'}});
        transcript.syncDurableTurnActivity();
    }""")
    expect(disclosure).to_contain_text('Checked import results.', timeout=15000)
    expect(disclosure).to_have_attribute('open', '')
    expect(command).to_have_attribute('open', '')
    expect(summary).to_be_focused()
