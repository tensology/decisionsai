"""Per-board view switcher (Kanban / Plan / Last thread) and the contextual plan controls."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / 'distr/gui/web/static/development'
TEMPLATES = ROOT / 'distr/gui/web/templates/development'

PRELUDE = r'''
import assert from 'node:assert/strict';
const m = await import(SOURCE);
function store() { const d = {}; return { getItem: k => d[k] ?? null, setItem: (k, v) => { d[k] = String(v); }, d }; }
const boardOf = chat => chat.board;
'''


def _node(script: str) -> None:
    script = (PRELUDE + script).replace('SOURCE', repr((STATIC / 'planning/view-switch.js').as_uri()))
    result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_options_icons_active_state_and_labels():
    _node(r'''
for (const current of ['kanban', 'plan', 'thread', 'terminals']) {
  const opts = m.boardViewOptions(current, 7);
  assert.deepEqual(opts.map(o => o.view), ['kanban', 'plan', 'thread', 'terminals']);
  assert.deepEqual(opts.filter(o => o.active).map(o => o.view), [current]);
}
const html = m.boardViewSwitchHtml(m.boardViewOptions('plan', 7));
assert.match(html, /data-board-view="plan" title="Planning" aria-label="Planning" aria-pressed="true"/);
assert.match(html, /data-board-view="kanban" title="Kanban" aria-label="Kanban" aria-pressed="false"/);
assert.match(html, /data-board-view="thread" title="Thread" aria-label="Thread"/);
assert.match(html, /data-board-view="terminals" title="Terminal" aria-label="Terminal" aria-pressed="false"/);
assert.equal((html.match(/<svg/g) || []).length, 4);   // icons, not text
assert.doesNotMatch(html, /aria-disabled/);
''')


def test_thread_option_disabled_with_tooltip_when_board_has_no_threads():
    _node(r'''
const html = m.boardViewSwitchHtml(m.boardViewOptions('kanban', 0));
assert.match(html, /data-board-view="thread" title="No threads on this board yet" aria-label="No threads on this board yet" aria-pressed="false" aria-disabled="true"/);
assert.doesNotMatch(html, / disabled[ >]/);   // aria-disabled keeps the tooltip working
''')


def test_last_thread_store_update_and_per_board_isolation():
    _node(r'''
const s = store();
const chats = [{id: 5, board: 'decisions:1'}, {id: 4, board: 'decisions:1'}, {id: 9, board: 'jira:2'}];
m.rememberBoardThread(s, 'decisions:1', 4);
m.rememberBoardThread(s, 'jira:2', 9);
assert.equal(m.boardLastThread(s, 'decisions:1', chats, boardOf), 4);
assert.equal(m.boardLastThread(s, 'jira:2', chats, boardOf), 9);
m.rememberBoardThread(s, 'decisions:1', 5);   // opening another thread updates only that board
assert.equal(m.boardLastThread(s, 'decisions:1', chats, boardOf), 5);
assert.equal(m.boardLastThread(s, 'jira:2', chats, boardOf), 9);
assert.deepEqual(JSON.parse(s.d[m.LAST_THREAD_STORE]), {'decisions:1': 5, 'jira:2': 9});
m.rememberBoardThread(s, '', 3); m.rememberBoardThread(s, 'x:1', 0);
assert.deepEqual(Object.keys(JSON.parse(s.d[m.LAST_THREAD_STORE])), ['decisions:1', 'jira:2']);
''')


def test_deleted_or_archived_thread_falls_back_or_disables():
    _node(r'''
const s = store();
m.rememberBoardThread(s, 'decisions:1', 4);
// 4 deleted (gone from list): newest remaining active thread on the board
assert.equal(m.boardLastThread(s, 'decisions:1', [{id: 6, board: 'decisions:1'}], boardOf), 6);
// 4 archived and no other: nothing valid
assert.equal(m.boardLastThread(s, 'decisions:1', [{id: 4, board: 'decisions:1', archived: true}], boardOf), 0);
// thread moved to another board does not count
assert.equal(m.boardLastThread(s, 'decisions:1', [{id: 4, board: 'jira:2'}], boardOf), 0);
assert.equal(m.boardLastThread({ getItem: () => '{bad json' }, 'decisions:1', [], boardOf), 0);
assert.equal(m.boardLastThread(s, '', [{id: 4, board: ''}], boardOf), 0);
''')


def test_last_section_store_is_per_board_and_rejects_other_views():
    _node(r'''
const s = store();
m.rememberBoardSection(s, 'decisions:1', 'plan');
m.rememberBoardSection(s, 'jira:2', 'kanban');
assert.equal(m.boardLastSection(s, 'decisions:1'), 'plan');
assert.equal(m.boardLastSection(s, 'jira:2'), 'kanban');
m.rememberBoardSection(s, 'decisions:1', 'thread');
assert.equal(m.boardLastSection(s, 'decisions:1'), 'thread');
assert.equal(m.boardLastSection(s, 'jira:2'), 'kanban');
m.rememberBoardSection(s, 'decisions:1', 'terminals');
m.rememberBoardSection(s, '', 'plan');
assert.equal(m.boardLastSection(s, 'decisions:1'), 'thread');
assert.equal(m.boardLastSection(s, ''), '');
assert.equal(m.boardLastSection({ getItem: () => '{bad' }, 'decisions:1'), '');
assert.deepEqual(JSON.parse(s.d[m.LAST_SECTION_STORE]), {'decisions:1': 'thread', 'jira:2': 'kanban'});
assert.equal(s.d[m.LAST_THREAD_STORE], undefined);
''')


def test_switcher_and_contextual_controls_are_wired():
    app = (STATIC / 'app.js').read_text()
    conv = (STATIC / 'planning/conversation.js').read_text()
    index = (TEMPLATES / 'index.html').read_text()
    kanban = (TEMPLATES / 'boards/workspace.html').read_text()
    # one switcher at the far right of each header bar, outside the breadcrumb text
    assert index.count('data-board-view-switch') == 1 and kanban.count('data-board-view-switch') == 1
    assert kanban.index('data-board-view-switch') > kanban.index('development-kanban-view-toggle')
    assert 'id="development-header-context"' in index
    assert "rememberBoardThread(window.localStorage, chatBoardKey(chat), chat.id);" in app
    assert "rememberBoardSection(window.localStorage, chatBoardKey(chat), 'thread');" in app
    assert "rememberBoardSection(window.localStorage, board.key, 'plan');" in app
    assert "rememberBoardSection(window.localStorage, board.key, 'kanban');" in app
    assert "projectClickTarget(" in app
    assert "lastSection: boardLastSection(window.localStorage, boardKey)" in app
    switch = (STATIC / 'planning/view-switch.js').read_text()
    assert "let section = current || lastSection;" in switch
    assert "planControlsSlot: () => el('development-header-context')," in app
    assert "if (button && button.getAttribute('aria-disabled') !== 'true') switchBoardView(button.dataset.boardView);" in app
    # plan toolbar (tabs, Build tickets) mounts in the contextual area, beside the view switch
    assert 'slot.replaceChildren(header);' in conv
    assert 'plan-back' not in conv  # header Back button removed (Round 15)
    assert "planBar().querySelector('#plan-build').onclick" in conv
    assert "className = 'plan-header-actions'" in conv
    assert "planBar().querySelectorAll('[data-plan-tab]')" in conv
    assert './planning/view-switch.js?v=20261107-nav68' in (STATIC / 'app.js').read_text()
    assert "./shared/components.css?v=20261102-verify" in (STATIC / 'styles.css').read_text()
