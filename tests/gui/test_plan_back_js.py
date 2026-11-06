"""Plan browser history: a deep-linked plan has its board Kanban behind it (header Back button removed in Round 15)."""
import re
from pathlib import Path
import subprocess

STATIC = Path(__file__).resolve().parents[2] / 'distr/gui/web/static/development'


def _node(script: str) -> None:
    script = script.replace('SOURCE', repr((STATIC / 'planning/back.js').as_uri()))
    result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_deep_linked_plan_gets_kanban_behind_it_in_history():
    _node(r'''
import assert from 'node:assert/strict';
const {seedPlanHistory} = await import(SOURCE);
const entries = ['/elsewhere'];
let index = 0;
const history = {
  replaceState: (_s, _t, url) => { entries[index] = url; },
  pushState: (_s, _t, url) => { entries.splice(index + 1); entries.push(url); index += 1; },
};
seedPlanHistory(history, {pathname:'/development/boards/decisions/18/plan/', search:'?tab=x', hash:''},
  '/development/boards/decisions/18/kanban/');
assert.deepEqual(entries, ['/development/boards/decisions/18/kanban/', '/development/boards/decisions/18/plan/?tab=x']);
assert.equal(index, 1); // current entry is the plan; Back goes to the Kanban
''')


def test_header_back_button_removed_browser_history_kept():
    conversation = (STATIC / 'planning/conversation.js').read_text()
    app = (STATIC / 'app.js').read_text()
    back = (STATIC / 'planning/back.js').read_text()
    css = (STATIC / 'planning/styles.css').read_text()
    # Round 15: the in-page "<" Back button is gone; the view switcher's Kanban icon is the way back.
    for source in (conversation, app, back, css):
        assert 'plan-back' not in source and 'backFromBoardPlan' not in source and 'planBackTarget' not in source
    # Browser Back/Forward on a deep-linked plan still lands on the board Kanban.
    assert "import { seedPlanHistory } from './planning/back.js?v=20261102-verify';" in app
    assert "if (!options?.fromHistory) seedPlanHistory(window.history, window.location, developmentBoardPath(board.key, 'kanban'));" in app
    assert re.search(r"\./planning/index\.js\?v=[0-9]{8}-[\w-]+'", app)
    assert re.search(r"\./conversation\.js\?v=[0-9]{8}-[\w-]+'", (STATIC / 'planning/index.js').read_text())
