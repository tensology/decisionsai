"""Plan embedded under the thread header: shared breadcrumb, cached reopen, no extra waterfall."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / 'distr/gui/web/static/development'


def _node(script: str) -> None:
    script = script.replace('SOURCE', repr((STATIC / 'planning/breadcrumb.js').as_uri()))
    result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


FAKE_DOC = r'''
function node(tag, attrs = {}) {
  return { tag, textContent: '', dataset: {}, ...attrs,
    after(...nodes) { const list = this.parent.children; list.splice(list.indexOf(this) + 1, 0, ...nodes); nodes.forEach(n => n.parent = this.parent); },
    remove() { const list = this.parent.children; list.splice(list.indexOf(this), 1); } };
}
const crumb = { children: [] };
const project = node('span', { id: 'header-project', textContent: 'Board A' });
const slash = node('i', { textContent: '/' });
const task = node('strong', { id: 'header-task', textContent: 'Some thread' });
[project, slash, task].forEach(n => { n.parent = crumb; crumb.children.push(n); });
const doc = {
  getElementById: id => crumb.children.find(n => n.id === id),
  createElement: tag => node(tag),
  querySelectorAll: () => crumb.children.filter(n => 'planCrumb' in n.dataset),
};
const text = () => crumb.children.map(n => n.textContent).join(' ');
'''


def test_breadcrumb_is_project_slash_plan_slash_section_in_thread_header():
    _node(r'''
import assert from 'node:assert/strict';
const {planCrumbs, renderPlanCrumbs, clearPlanCrumbs} = await import(SOURCE);
''' + FAKE_DOC + r'''
assert.deepEqual(planCrumbs('DecisionsAI', 'Pages'), ['DecisionsAI', 'Plan', 'Pages']);
assert.deepEqual(planCrumbs('', undefined), ['Development', 'Plan']);
renderPlanCrumbs(doc, planCrumbs('DecisionsAI', 'Pages'));
assert.equal(text(), 'DecisionsAI / Plan / Pages');
assert.equal(crumb.children[3].tag, 'i'); assert.equal(crumb.children[4].tag, 'strong');
renderPlanCrumbs(doc, planCrumbs('DecisionsAI', 'Data'));   // tab switch replaces, never stacks
assert.equal(text(), 'DecisionsAI / Plan / Data');
clearPlanCrumbs(doc);
assert.equal(crumb.children.length, 3);
''')


def test_plan_mode_keeps_thread_header_and_embeds_under_it():
    app = (STATIC / 'app.js').read_text()
    css = (STATIC / 'planning/styles.css').read_text()
    assert "el('development-task-header').classList.toggle('hidden', alternateMode && !planMode);" in app
    assert "el('development-task-header').classList.toggle('plan-mode', planMode);" in app
    assert "renderPlanCrumbs(document, planCrumbs(name, ...rest));" in app
    assert "clearPlanCrumbs(document);" in app
    assert "#development-task-header.plan-mode > :not(.task-breadcrumb):not(.header-context):not(.board-view-switch) { display: none !important; }" in css
    assert ".development-workspace.plan-workspace { position: relative; inset: auto; grid-row: 2;" in css
    html = (ROOT / 'distr/gui/web/templates/development/index.html').read_text()
    assert html.count('class="task-breadcrumb"') == 1  # one breadcrumb component, reused not copied


def test_reopen_uses_cache_and_providers_load_once_in_parallel():
    index = (STATIC / 'planning/index.js').read_text()
    conv = (STATIC / 'planning/conversation.js').read_text()
    assert 'const cached = state.details.get(board.key);' in index
    assert 'if (cached) conversation.refreshWorkspace(detail);' in index
    assert 'planningProviders ||= api(' in conv
    # Conversation (of the active chat, multi-chat) and providers still load in parallel.
    assert "await Promise.all([api(conversationPath(session))" in conv and "}), planningProviders]);" in conv
    assert "(await api('/llms/available-providers?type=planning'))" not in conv
    # Round 15: the active tab is not repeated in the breadcrumb.
    assert 'host.setPlanCrumbs?.(TABS' not in conv and conv.count('host.setPlanCrumbs?.();') == 1
    assert './planning/breadcrumb.js?v=20261102-verify' in (STATIC / 'app.js').read_text()
