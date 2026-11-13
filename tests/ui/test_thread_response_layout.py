"""Thread response layout (reference: Merrypak voucher thread 1284).

- An instant notice ("Ticket created in Backlog.") saved after the run's reply row still lists at its real time.
- It renders as a compact event row, so the run's reply keeps the edited-files card and footer.
- A trailing "Status: completed" line becomes a footer chip.
- Zone-less server timestamps (turn_started_at) are UTC, not browser-local time.
"""
import pytest
from tests.ui.test_development_harness_playwright import URL

pytestmark = pytest.mark.e2e_playwright


@pytest.fixture
def browser_context_args(browser_context_args):
    return {**browser_context_args, "timezone_id": "Africa/Johannesburg", "locale": "en-US"}


def test_notice_orders_by_time_status_becomes_chip_and_time_is_utc(page):
    page.goto(URL.split('/development/')[0] + '/robots.txt')
    page.set_content('<div id="studio-empty"></div><main id="conversation"><div id="message-list"></div></main>')
    rows = page.evaluate("""async () => {
        const {createThreadsTranscript} = await import('/static/development/threads/transcript/index.js?v=' + Date.now());
        const messages = [
            {role:'user', chat_row_id:1285, timestamp:'2026-11-13T12:49:36.066473Z', content:'Investigate missing vouchers'},
            {role:'assistant', chat_row_id:1285, timestamp:'2026-11-13T12:55:19.658856Z', turn_started_at:'2026-11-13T12:49:36.075882',
             turn_duration_seconds:343, content:'Restored and verified.\\n\\nStatus: completed'},
            {role:'assistant', chat_row_id:1286, timestamp:'2026-11-13T12:49:36.102569Z', turn_started_at:'2026-11-13T12:49:36.102568Z',
             turn_duration_seconds:0, content:'Ticket created in Backlog.'},
        ];
        const context = {currentChat:{id:1284, messages, turns:[]}, currentRun:{}};
        const actions = {escapeHtml:v=>String(v||'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
                         bindConversationActions:()=>{}, isActiveAgentRun:()=>false, formatSeconds:(s)=>`${Math.floor(s/60)}m ${s%60}s`};
        createThreadsTranscript({context, actions, el:id=>document.getElementById(id)}).renderConversation(context.currentChat);
        return [...document.querySelectorAll('#message-list > *')].map(a => ({
            cls: a.className, text: (a.querySelector('.thread-status-event, .message-markdown, .user-message')?.innerText || '').trim(),
            chip: a.querySelector('.message-status-chip')?.innerText || '', time: a.querySelector('.message-timing time')?.innerText || ''}));
    }""")
    assert [r['cls'].split()[-1] for r in rows] == ['user', 'thread-event', 'assistant']
    assert rows[1]['text'].startswith('Ticket created in') and '14:49' in rows[1]['text']
    assert 'Status:' not in rows[2]['text']
    assert rows[2]['chip'].lower() == 'completed'
    assert rows[2]['time'] == '14:49'


def test_event_row_is_a_centered_divider(page):
    page.goto(URL.split('/development/')[0] + '/robots.txt')
    page.set_content('<link rel="stylesheet" href="/static/development/shell.css">'
                     '<link rel="stylesheet" href="/static/development/threads/styles.css">'
                     '<main id="conversation" style="width:800px"><div id="message-list" style="display:flex;flex-direction:column">'
                     '<article class="studio-message thread-event"><div class="thread-status-event">'
                     '<span class="thread-status-dot"></span>Ticket created in <strong>Backlog</strong><time>14:49</time>'
                     '</div></article></div></main>')
    page.wait_for_function("getComputedStyle(document.querySelector('.thread-status-event'),'::before').height === '1px'")
    m = page.evaluate("""() => {
        const e = document.querySelector('.thread-status-event'), r = e.getBoundingClientRect(), list = document.querySelector('#message-list').getBoundingClientRect();
        const items = [...e.children].map(c => c.getBoundingClientRect());
        const left = Math.min(...items.map(i => i.left)), right = Math.max(...items.map(i => i.right));
        const before = getComputedStyle(e, '::before'), after = getComputedStyle(e, '::after');
        return {rowWidth: r.width, listWidth: list.width, offCenter: (left + right) / 2 - (r.left + r.right) / 2,
                before: parseFloat(before.width), after: parseFloat(after.width), height: r.height};
    }""")
    assert abs(m['rowWidth'] - m['listWidth']) < 1          # rule spans the thread width
    assert abs(m['offCenter']) < 2                            # label + time centered together
    assert m['before'] > 40 and abs(m['before'] - m['after']) < 2  # line on both sides, gap around text
    assert m['height'] <= 30                                  # compact
