"""Edit board dialog Terminal commands | Notes tabs, and the run environment note in a thread.

Reference: Merrypak thread 1284 (voucher investigation worked off the local DB; load_latest_db was never run).
"""
from pathlib import Path

import pytest
from tests.ui.test_development_harness_playwright import URL

pytestmark = pytest.mark.e2e_playwright

ROOT = Path(__file__).resolve().parents[2]
DIALOG = (ROOT / "distr/gui/web/templates/development/sidebar/board-dialog.html").read_text()


@pytest.fixture
def browser_context_args(browser_context_args):
    return {**browser_context_args, "timezone_id": "Africa/Johannesburg", "locale": "en-US"}


def _blank(page):
    page.goto(URL.split('/development/')[0] + '/robots.txt')


def test_board_dialog_has_terminal_and_notes_tabs_and_loads_project_notes(page):
    _blank(page)
    page.set_content('<div id="board-context-menu"></div>' + DIALOG)
    result = page.evaluate("""async () => {
        const {createSidebar} = await import('/static/development/sidebar/index.js?v=' + Date.now());
        const calls = [];
        const board = {key:'decisions:2', id:2, local_id:2, provider:'decisions', name:'Merrypak', project_id:2,
                       folder_location:'/repo', startup_instructions:'npm run dev', notes:''};
        const actions = {boardByKey: k => k === board.key ? board : null, toast(){}, refreshShell: async () => {},
            api: async (path, options) => { calls.push([path, options?.method || 'GET', options?.body]);
                return path.endsWith('/notes') ? {notes: 'Refresh command: ./load_latest_db.sh'} : {success: true}; }};
        const el = id => document.getElementById(id);
        const sidebar = createSidebar({context:{boards:[board], chats:[], archivedChats:[]}, actions, el});
        el('board-link-form').addEventListener('submit', sidebar.saveBoardLink);
        sidebar.bindBoardEditTabs();
        sessionStorage.setItem('decisions.boardEditTab', 'notes');
        await sidebar.openBoardLinkDialog('decisions:2');
        await new Promise(r => setTimeout(r, 30));
        const state = {
            tabs: [...document.querySelectorAll('#board-edit-tabs [role=tab]')].map(t => [t.textContent.trim(), t.getAttribute('aria-selected')]),
            notesVisible: !el('board-edit-notes-panel').classList.contains('hidden'),
            terminalsVisible: !el('board-edit-terminals-panel').classList.contains('hidden'),
            notes: el('board-edit-notes').value, terminals: el('board-edit-terminals').value,
            nameAbove: el('board-edit-name').compareDocumentPosition(el('board-edit-tabs')) & Node.DOCUMENT_POSITION_FOLLOWING,
        };
        el('board-edit-terminals-tab').click();
        state.afterClick = [sessionStorage.getItem('decisions.boardEditTab'), el('board-edit-terminals-panel').classList.contains('hidden')];
        el('board-edit-notes').value = 'Project type: support';
        el('board-link-form').dispatchEvent(new Event('submit', {cancelable: true}));
        await new Promise(r => setTimeout(r, 30));
        return {state, calls};
    }""")
    state = result['state']
    assert state['tabs'] == [['Terminal commands', 'false'], ['Notes', 'true']]
    assert state['notesVisible'] and not state['terminalsVisible']
    assert state['notes'] == 'Refresh command: ./load_latest_db.sh' and state['terminals'] == 'npm run dev'
    assert state['nameAbove']
    assert state['afterClick'] == ['terminals', False]
    put = [c for c in result['calls'] if c[1] == 'PUT']
    assert put and put[0][0] == '/tickets/boards/2'
    assert put[0][2]['notes'] == 'Project type: support' and put[0][2]['startup_instructions'] == 'npm run dev'


def test_board_dialog_disables_notes_when_server_lacks_the_notes_api(page):
    _blank(page)
    page.set_content('<div id="board-context-menu"></div>' + DIALOG)
    result = page.evaluate("""async () => {
        const {createSidebar} = await import('/static/development/sidebar/index.js?v=' + Date.now());
        const calls = [];
        const board = {key:'decisions:2', id:2, local_id:2, provider:'decisions', name:'Merrypak', project_id:2, startup_instructions:''};
        const actions = {boardByKey: () => board, toast(){}, refreshShell: async () => {},
            api: async (path, options) => { calls.push([path, options?.method || 'GET', options?.body]);
                if (path.endsWith('/notes')) { const e = new Error('Not Found'); e.status = 404; throw e; } return {success: true}; }};
        const el = id => document.getElementById(id);
        const sidebar = createSidebar({context:{boards:[board], chats:[], archivedChats:[]}, actions, el});
        el('board-link-form').addEventListener('submit', sidebar.saveBoardLink);
        await sidebar.openBoardLinkDialog('decisions:2');
        await new Promise(r => setTimeout(r, 30));
        const disabled = el('board-edit-notes').disabled;
        el('board-link-form').dispatchEvent(new Event('submit', {cancelable: true}));
        await new Promise(r => setTimeout(r, 30));
        return {disabled, put: calls.filter(c => c[1] === 'PUT')};
    }""")
    assert result['disabled'] is True
    assert 'notes' not in result['put'][0][2]


def test_latest_reply_shows_environment_note(page):
    _blank(page)
    page.set_content('<div id="studio-empty"></div><main id="conversation"><div id="message-list"></div></main>')
    text = page.evaluate("""async () => {
        const {createThreadsTranscript} = await import('/static/development/threads/transcript/index.js?v=' + Date.now());
        const messages = [
            {role:'user', chat_row_id:1, timestamp:'2026-11-13T13:09:00Z', content:'Redo with load_latest_db'},
            {role:'assistant', chat_row_id:1, timestamp:'2026-11-13T13:14:00Z', turn_duration_seconds:300, content:'Done.'},
        ];
        const environment = {
            database: {settings_module:'core.settings', settings_file:'backend/webapp/core/settings_local.py', engine:'Postgres', name:'shopdb', host:'localhost', port:'5432', scope:'local'},
            refresh: {command:'./load_latest_db.sh', ran:true, at:'2026-11-13T13:09:46+00:00', status:'passed'},
            production_check: {checked:true, how:'ssh deploy@www.example.com', commands:1, at:'2026-11-13T13:13:37+00:00', result:'passed', read_only:true},
            support_project: true, summary: 'x'};
        const context = {currentChat:{id:1284, messages, turns:[]}, currentRun:{outcome:{environment}}};
        const actions = {escapeHtml:v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
                         bindConversationActions:()=>{}, isActiveAgentRun:()=>false, formatSeconds:(s)=>`${s}s`};
        createThreadsTranscript({context, actions, el:id=>document.getElementById(id)}).renderConversation(context.currentChat);
        const note = document.querySelector('.studio-message.assistant .turn-environment-note');
        const footer = document.querySelector('.studio-message.assistant .message-footer');
        return {text: note?.innerText || '', beforeFooter: !!(note && footer && (note.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING))};
    }""")
    assert 'Postgres shopdb @ localhost:5432' in text['text'] and 'local' in text['text']
    assert './load_latest_db.sh' in text['text'] and 'ran 15:09' in text['text']
    assert 'ssh deploy@www.example.com' in text['text'] and '15:13' in text['text']
    assert text['beforeFooter']
