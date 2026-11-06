"""Frontend: scanning hint and refresh when the background scan settles."""
import re
from pathlib import Path
import subprocess

STATIC = Path(__file__).resolve().parents[2] / 'distr/gui/web/static/development'


def _node(script: str) -> None:
    script = script.replace('SOURCE', repr((STATIC / 'planning/scan-watch.js').as_uri()))
    result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_hint_text_per_state():
    _node(r'''
import assert from 'node:assert/strict';
const m = await import(SOURCE);
assert.deepEqual(m.scanHint({state: 'scanning'}), {text: 'Scanning project…', kind: 'scanning'});
assert.deepEqual(m.scanHint({state: 'error', error: 'folder gone'}), {text: 'Project scan failed: folder gone', kind: 'error'});
assert.equal(m.scanHint({state: 'done'}), null);
assert.equal(m.scanHint(undefined), null);
''')


def test_polls_until_done_then_refreshes_workspace():
    _node(r'''
import assert from 'node:assert/strict';
const m = await import(SOURCE);
const states = ['scanning', 'scanning', 'done'];
const calls = [];
const api = async (path) => { calls.push(path); return path.endsWith('/scan-status') ? {state: states.shift()} : {id: 7, items: ['scanned']}; };
let settled;
const result = await m.watchPlanScan({api, workspaceId: 7, isCurrent: () => true, sleep: async () => {}, onSettled: (s, w) => { settled = [s, w]; }});
assert.equal(result, 'done');
assert.deepEqual(calls, ['/workflows/studio/plan-workspaces/7/scan-status', '/workflows/studio/plan-workspaces/7/scan-status', '/workflows/studio/plan-workspaces/7/scan-status', '/workflows/studio/plan-workspaces/7']);
assert.deepEqual(settled, [{state: 'done'}, {id: 7, items: ['scanned']}]);
''')


def test_error_settles_without_refetch_and_leaving_abandons():
    _node(r'''
import assert from 'node:assert/strict';
const m = await import(SOURCE);
const calls = [];
let settled;
let r = await m.watchPlanScan({api: async (p) => { calls.push(p); return {state: 'error', error: 'x'}; }, workspaceId: 3, isCurrent: () => true, sleep: async () => {}, onSettled: (s, w) => { settled = [s, w]; }});
assert.equal(r, 'error'); assert.equal(calls.length, 1); assert.deepEqual(settled, [{state: 'error', error: 'x'}, null]);
let fired = false;
r = await m.watchPlanScan({api: async () => ({state: 'done'}), workspaceId: 3, isCurrent: () => false, sleep: async () => {}, onSettled: () => { fired = true; }});
assert.equal(r, 'abandoned'); assert.equal(fired, false);
''')


def test_index_wires_hint_and_refresh_and_models_parallel():
    index = (STATIC / 'planning/index.js').read_text()
    conv = (STATIC / 'planning/conversation.js').read_text()
    assert 'conversation.setScanHint(scanHint(detail.scan));' in index
    assert "if (detail.scan?.state === 'scanning') {" in index
    assert 'conversation.refreshWorkspace(workspace);' in index
    assert 'await Promise.all([refreshWorkspace ? reload(session) : null, loadModels(session)]);' in conv
    assert re.search(r"\./planning/index\.js\?v=[0-9]{8}-[\w-]+'", (STATIC / 'app.js').read_text())
