from pathlib import Path
import subprocess


def test_web_sidebar_seeds_local_pins_then_follows_shared_state():
    source = Path('distr/gui/web/static/development/sidebar/index.js').resolve().as_uri()
    script = '''
import assert from 'node:assert/strict';
const { createSidebar } = await import(SOURCE);
const saved = new Map([['decisions-development-pinned-boards', '["jira:ABC"]']]);
globalThis.window = { location: { search: '' }, localStorage: {
  getItem: key => saved.get(key) || null, setItem: (key, value) => saved.set(key, value)
}};
const writes = [];
const sidebar = createSidebar({
  context: { boards: [{key:'decisions:13', project_id:9}] }, el: () => null,
  actions: { api: async (path, options) => {
    writes.push({path, ...options}); return options.body;
  }}
});
// Hover-panel project pins are migrated server-side; the browser only seeds its old local pins.
await sidebar.applyPinnedBoards({board_keys:null});
assert.deepEqual(writes[0].body.board_keys, ['jira:ABC']);
assert.equal(writes[0].method, 'PUT');
await sidebar.applyPinnedBoards({board_keys:['trello:SHARED']});
assert.deepEqual(JSON.parse(saved.get('decisions-development-pinned-boards')), ['trello:SHARED']);
await sidebar.applyPinnedBoards({});
assert.deepEqual(JSON.parse(saved.get('decisions-development-pinned-boards')), ['trello:SHARED']);
assert.equal(writes.length,1);
'''.replace('SOURCE', repr(source))
    result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_desktop_menu_dispatch_reuses_only_available_canonical_web_actions():
    source = Path('distr/gui/web/static/development/sidebar/index.js').resolve().as_uri()
    script = '''
import assert from 'node:assert/strict';
const {createSidebar} = await import(SOURCE);
globalThis.window = {location:{search:''},localStorage:{getItem:()=>null}};
const nodes = new Map(); const clicked=[]; const messages=[];
function el(id) {
 if (!nodes.has(id)) {
  const classes=new Set();
  nodes.set(id,{disabled:false,dataset:{},offsetWidth:100,offsetHeight:100,style:{},setAttribute(){},
   classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle:(x,on)=>on?classes.add(x):classes.delete(x)},
   querySelector:()=>({textContent:'',innerHTML:''}),focus(){},click(){clicked.push(id);}});
 }
 return nodes.get(id);
}
const board={key:'decisions:18',provider:'decisions',project_id:9};
const sidebar=createSidebar({context:{boards:[board]},el,actions:{
 boardByKey:key=>key===board.key?board:null,projectById:()=>({folder_location:'/tmp'}),
 providerName:x=>x,systemFileManagerLabel:()=> 'Finder',getTerminalSessions:()=>[],
 loadProjectTerminalSessions:async()=>[],toast:x=>messages.push(x)
}});
await sidebar.runDesktopBoardAction({board_key:board.key,action:'edit'});
assert.deepEqual(clicked,['board-context-edit']);
await sidebar.runDesktopBoardAction({board_key:board.key,action:'arbitrary-script'});
assert.equal(clicked.length,1);
el('board-context-delete').disabled=true;
await sidebar.runDesktopBoardAction({board_key:board.key,action:'delete'});
assert.equal(clicked.length,1);
assert.equal(messages.length,1);
board.provider='jira';
await sidebar.runDesktopBoardAction({board_key:board.key,action:'archive'});
assert.equal(clicked.length,1);
await sidebar.runDesktopBoardAction({board_key:board.key,action:'terminal-toggle'});
assert.equal(clicked.at(-1),'board-context-terminal-toggle');
'''.replace('SOURCE', repr(source))
    result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_board_folder_icon_is_filled_with_threads_and_outline_without():
    source = Path('distr/gui/web/static/development/sidebar/index.js').resolve().as_uri()
    script = '''
import assert from 'node:assert/strict';
const {boardFolderIconHtml} = await import(SOURCE);
assert.match(boardFolderIconHtml(true), /fill="currentColor"/);
assert.match(boardFolderIconHtml(true), /folder-filled/);
assert.match(boardFolderIconHtml(false), /fill="none"/);
assert.match(boardFolderIconHtml(false), /folder-outline/);
'''.replace('SOURCE', repr(source))
    result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_board_row_terminal_slot_shows_play_stop_pencil_or_nothing():
    source = Path('distr/gui/web/static/development/sidebar/index.js').resolve().as_uri()
    script = '''
import assert from 'node:assert/strict';
const {createSidebar} = await import(SOURCE);
globalThis.window = {location:{search:''},localStorage:{getItem:()=>null}};
const sessions = {1: [], 2: [{process_id: 'p', purpose: 'startup'}], 3: [{process_id:'discovered:303', purpose:'project_server'}], 4: [{process_id:'shell-4',purpose:'cli_shell'}], 5: [{process_id:'pi-5',purpose:'pi_cli'}], 6: [{process_id:'unknown'}]};
const projects = {1: 'npm run dev', 2: 'npm run dev', 9: '', 10: '# only a comment'};
const hasCommands = id => String(projects[id] || '').split('\\n').some(l => l.trim() && !l.trim().startsWith('#'));
const sidebar = createSidebar({context:{boards:[]}, el:()=>null, actions:{
  escapeHtml: x => String(x),
  getTerminalSessions: id => sessions[id] || [],
  hasStartupTerminalCommands: hasCommands,
  terminalProjectActionIcon: (action) => `<svg data-icon="${action}"></svg>`
}});
const row = board => sidebar.boardTerminalActionHtml({provider:'decisions', name:'B', ...board});
// Startup command, not running: play.
const play = row({key:'decisions:1', project_id:1});
assert.match(play, /data-board-terminal="decisions:1"/);
assert.match(play, /data-icon="start"/);
assert.match(play, /data-running="false"/);
// Auxiliary sessions must never imply that startup terminals were launched.
for (const id of [3, 4, 5, 6]) {
  assert.match(row({key:`decisions:${id}`, project_id:id, startup_instructions:'npm run dev'}), /data-icon="start"/);
}
// Running: stop.
const stop = row({key:'decisions:2', project_id:2});
assert.match(stop, /data-icon="stop"/);
assert.match(stop, /terminal-project-action stop/);
// Project with empty startup_instructions (e.g. DecisionsAI, project 9): green pencil to edit the board.
for (const pencil of [row({key:'decisions:18', project_id:9, startup_instructions:''}), row({key:'decisions:19', project_id:10})]) {
  assert.match(pencil, /class="board-row-action board-edit-commands"/);
  assert.match(pencil, /data-board-edit-commands="decisions:1[89]"/);
  assert.doesNotMatch(pencil, /data-board-terminal/);
}
// Board's own startup_instructions also count as a command.
assert.match(row({key:'decisions:20', project_id:11, startup_instructions:'make run'}), /data-icon="start"/);
// Decisions board without a project: pencil (saving the dialog creates and links a project).
assert.match(row({key:'decisions:21', project_id:null}), /data-board-edit-commands="decisions:21"/);
// External board without commands: the dialog cannot save commands, so no icon.
assert.equal(row({key:'jira:X', provider:'jira', project_id:null}), '');
assert.equal(row({key:'trello:T', provider:'trello', project_id:9}), '');
// No bead markup anywhere.
for (const html of [play, stop, row({key:'decisions:18', project_id:9})]) assert.doesNotMatch(html, /board-link-bead/);
'''.replace('SOURCE', repr(source))
    result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_board_link_bead_is_removed_everywhere():
    for path in ('distr/gui/web/static/development/sidebar/index.js', 'distr/gui/web/static/development/shell.css'):
        assert 'board-link-bead' not in Path(path).read_text()


def test_pencil_opens_the_existing_board_edit_dialog():
    source = Path('distr/gui/web/static/development/sidebar/index.js').read_text()
    assert "openBoardLinkDialog(button.dataset.boardEditCommands)" in source


def test_board_rows_have_no_separate_kanban_button():
    source = Path('distr/gui/web/static/development/sidebar/index.js').read_text()
    assert 'data-board-kanban' not in source
    assert "actions.openBoardFromSidebar(button.dataset.boardSelect)" in source


def test_kanban_run_status_dot_sits_in_badges_row_in_both_layouts():
    source = Path('distr/gui/web/static/development/boards/index.js').resolve().as_uri()
    script = '''
import assert from 'node:assert/strict';
const {createBoards} = await import(SOURCE);
globalThis.window = {location:{search:''},localStorage:{getItem:()=>null}};
const boards = createBoards({context:{}, el:()=>null, actions:{
  escapeHtml: x => String(x), statusLabel: s => s[0].toUpperCase() + s.slice(1), providerName: x => x
}});
const board = {provider:'decisions'};
const cases = [['running','green',true],['completed','green',false],['waiting','amber',true],['paused','amber',true],
  ['queued','amber',true],['initializing','amber',true],['failed','red',false],['cancelled','grey',false]];
for (const layout of ['board','list']) {
  for (const [status, tone, active] of cases) {
    const html = boards.kanbanCardHtml(board, {}, {id:1, title:'T', priority:'High', complexity:'small', workflow_status:status}, layout);
    const badges = html.match(/<span class="development-kanban-card-badges">([\\s\\S]*?)<button type="button" class="development-kanban-ticket-menu"/)[1];
    const title = html.match(/<span class="development-kanban-card-title">([\\s\\S]*?)<\\/span>/)[1];
    assert.equal((html.match(/kanban-run-status/g) || []).length, 1, `${layout} ${status} not duplicated`);
    assert.ok(badges.startsWith(active ? '<button type="button" class="kanban-run-status' : '<span class="kanban-run-status'), `${layout} ${status} first badge`);
    assert.match(badges, new RegExp(`kanban-run-status[^"]*${tone}`));
    assert.ok(badges.indexOf('kanban-run-status') < badges.indexOf('kanban-complexity'));
    assert.ok(badges.indexOf('kanban-complexity') < badges.indexOf('kanban-priority'));
    assert.equal(active, badges.includes('data-ticket-run-status'));
    assert.doesNotMatch(title, /kanban-run-status/);
  }
  const none = boards.kanbanCardHtml(board, {}, {id:2, title:'N'}, layout);
  assert.doesNotMatch(none, /kanban-run-status/);
}
'''.replace('SOURCE', repr(source))
    result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_kanban_header_plan_icon_is_removed_and_board_binding_still_works():
    source = Path('distr/gui/web/static/development/boards/index.js').resolve().as_uri()
    script = r'''
import assert from 'node:assert/strict';
const {createBoards} = await import(SOURCE);
globalThis.window = {location:{search:''},localStorage:{getItem:()=>null}};
const nodes = new Map();
const el = id => {
  if (!nodes.has(id)) nodes.set(id, {handlers:{}, dataset:{}, classList:{add(){},remove(){},toggle(){},contains:()=>false},
    addEventListener(type, fn){ this.handlers[type] = fn; }, querySelector:()=>null, querySelectorAll:()=>[]});
  return nodes.get(id);
};
const boards = createBoards({context:{selectedBoardKey:'decisions:18'}, el, actions:{}});
boards.bindEvents();
assert.equal(nodes.has('development-kanban-plan'), false);
assert.equal(typeof el('development-kanban-refresh').handlers.click, 'function');
'''.replace('SOURCE', repr(source))
    result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_plan_row_removed_from_sidebar_and_kanban_plan_icon_gone():
    nav = Path('distr/gui/web/templates/development/sidebar/navigation.html').read_text()
    sidebar = Path('distr/gui/web/static/development/sidebar/index.js').read_text()
    header = Path('distr/gui/web/templates/development/boards/workspace.html').read_text()
    boards = Path('distr/gui/web/static/development/boards/index.js').read_text()
    app = Path('distr/gui/web/static/development/app.js').read_text()
    assert 'sidebar-plan-toggle' not in nav and 'sidebar-plan-toggle' not in sidebar
    # Plan is reached from the view switcher and the board right-click menu now.
    assert 'development-kanban-plan' not in header and 'development-kanban-plan' not in boards
    assert 'actions.openBoardPlan(state.contextBoardKey)' in sidebar
    assert header.index('id="development-kanban-refresh"') < header.index('development-kanban-view-toggle') < header.index('data-board-view-switch')
    # The plan route stays alive.
    assert "plan: '/development/plan/'" in app and 'async function openBoardPlan' in app
    assert "openBoardPlan: (...args) => openBoardPlan(...args)" in app  # sidebar actions (right-click Plan)
    assert "from './boards/index.js?v=" in app and "from './sidebar/index.js?v=" in app
