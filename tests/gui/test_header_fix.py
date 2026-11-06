"""Round 14: one shared header for thread/plan/Kanban, right-aligned cluster, non-blocking plan load."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / 'distr/gui/web/static/development'
TEMPLATES = ROOT / 'distr/gui/web/templates/development'


def _node(script: str, module: str) -> None:
    script = script.replace('SOURCE', repr((STATIC / module).as_uri()))
    result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_kanban_header_uses_shared_task_header_styles():
    kanban = (TEMPLATES / 'boards/workspace.html').read_text()
    boards_css = (STATIC / 'boards/styles.css').read_text()
    assert '<header class="task-header development-kanban-header">' in kanban
    rule = boards_css[boards_css.index('.development-kanban-header {'):]
    rule = rule[:rule.index('}')]
    for prop in ('background', 'border', 'padding', 'gap'):
        assert prop not in rule, prop  # no own banner colour/border/spacing
    assert '<header class="task-header" id="development-task-header">' in (TEMPLATES / 'index.html').read_text()


def test_header_cluster_is_right_aligned_with_a_single_spacer():
    comp = (STATIC / 'shared/components.css').read_text()
    switch = comp[comp.index('.board-view-switch {'):]
    switch = switch[:switch.index('}')]
    assert 'margin-left: 0' in switch and 'margin-left: auto' not in switch
    assert '.task-header > .task-breadcrumb { flex: 1 1 auto; }' in comp
    assert '#development-task-header.plan-mode > .task-breadcrumb { flex: 0 1 auto; }' in comp
    index = (TEMPLATES / 'index.html').read_text()
    order = [index.index(k) for k in ('class="task-breadcrumb"', 'id="development-header-context"', 'id="task-state"',
                                       'id="stop-run-button"', 'id="thread-terminal-button"', 'id="thread-more-button"', 'data-board-view-switch')]
    # Ready dot, Stop, start/stop terminal, then ⋯ menu, then the view switcher (Paul's order).
    assert order == sorted(order)


def test_load_guard_cancels_and_ignores_stale_results():
    _node(r'''
import assert from 'node:assert/strict';
const {createLoadGuard, isAbort} = await import(SOURCE);
class Ctl { constructor(){ this.signal = {aborted:false}; } abort(){ this.signal.aborted = true; } }
const g = createLoadGuard(Ctl);
const a = g.begin();
assert.equal(a.isCurrent(), true);
const b = g.begin();                    // a newer load supersedes the first
assert.equal(a.signal.aborted, true); assert.equal(a.isCurrent(), false); assert.equal(b.isCurrent(), true);
g.cancel();                              // navigating away
assert.equal(b.signal.aborted, true); assert.equal(b.isCurrent(), false);
assert.equal(isAbort({name:'AbortError'}), true); assert.equal(isAbort(new Error('x')), false);
''', 'planning/load-guard.js')


def test_plan_load_never_holds_navigation():
    app = (STATIC / 'app.js').read_text()
    index = (STATIC / 'planning/index.js').read_text()
    # deep-link restore and the view switcher do not await the plan fetch
    assert "openBoardPlan(board.key, { updateUrl: false }).catch(() => {});" in app
    assert "await openBoardPlan(board.key, { updateUrl: false });" not in app
    assert "openBoardPlan(boardKey).catch(() => {});" in app
    # URL pushed before the fetch so a late result can't overwrite another view's URL
    plan = app[app.index('async function openBoardPlan'):]
    plan = plan[:plan.index('\n}\n')]
    assert plan.index('setDevelopmentLocation') < plan.index('await planning.openBoard')
    assert 'const planToken = ++state.planOpenToken;' in plan
    # in-flight POST aborted on leave; late results and scan polling ignored
    assert 'signal: load.signal,' in index
    assert 'loads.cancel();' in index
    assert "isCurrent: () => navigation === state.navigation && load.isCurrent()," in index
    assert 'if (isAbort(error) || !load.isCurrent()) return false;' in index
    # cache-bust token only needs to exist (other work re-bumps it); never pin an exact value
    assert re.search(r'app\.js\?v=[0-9]{8}-[\w-]+"', (TEMPLATES / 'index.html').read_text())


def test_plan_header_buttons_tabs_and_breadcrumb_are_uniform():
    conv = (STATIC / 'planning/conversation.js').read_text()
    css = (STATIC / 'planning/styles.css').read_text()
    shell = (STATIC / 'shell.css').read_text()
    # Round 16: actions live in a ⋯ menu (thread-menu popover); no divider, no back button
    assert 'plan-header-divider' not in conv and 'plan-header-divider' not in css
    assert 'plan-back' not in conv
    actions = conv[conv.index('ifaceActions.innerHTML'):conv.index("ifaceActions.querySelector('#plan-rebuild-sitemap').onclick")]
    assert 'id="plan-build"' not in actions and '>Rebuild</button>' in actions
    assert conv.index('class="plan-tabs"') < conv.index('id="plan-rebuild-sitemap"') < conv.index('id="plan-build"')
    assert '.header-button.primary { border-color: transparent; background: var(--studio-accent); color: #fff; }' in shell
    # mounted bar styled where it actually lives (outside .plan-detail), no wrap, actions right
    assert '.header-context .plan-context-controls {' in css and 'flex-wrap: nowrap;' in css
    assert '.plan-context-controls .plan-header-actions { display: flex; flex: 0 0 auto; align-items: center; gap: 6px; width: auto; margin-left: auto; }' in css
    # breadcrumb: single emphasised segment, earlier ones muted, truncates
    assert '#development-task-header [data-plan-crumb-parent] { color: var(--studio-muted); font-weight: 400; }' in css
    assert '#development-task-header.plan-mode > .task-breadcrumb { min-width: 0; max-width: 34%; }' in css


def test_parent_crumbs_render_muted_only_the_last_emphasised():
    _node(r'''
import assert from 'node:assert/strict';
const {renderPlanCrumbs, clearPlanCrumbs} = await import(SOURCE);
const kids = [];
const mk = (tag, id) => ({ tag, id, textContent: '', dataset: {}, parent: null,
  after(...n) { kids.splice(kids.indexOf(this) + 1, 0, ...n); }, remove() { kids.splice(kids.indexOf(this), 1); } });
const project = mk('span', 'header-project'), task = mk('strong', 'header-task');
kids.push(project, task);
const doc = { getElementById: id => kids.find(k => k.id === id), createElement: t => mk(t),
  querySelectorAll: () => kids.filter(k => 'planCrumb' in k.dataset) };
renderPlanCrumbs(doc, ['Merrypak', 'Plan']);
assert.equal('planCrumbParent' in task.dataset, false);
renderPlanCrumbs(doc, ['Merrypak', 'Plan', 'Home page']);
assert.equal('planCrumbParent' in task.dataset, true);
clearPlanCrumbs(doc);
assert.equal('planCrumbParent' in task.dataset, false);
''', 'planning/breadcrumb.js')


def test_plan_header_tabs_are_a_segmented_control_like_the_view_switcher():
    comp = (STATIC / 'shared/components.css').read_text()
    css = (STATIC / 'planning/styles.css').read_text()
    for token in ('--segment-border:', '--segment-radius:', '--segment-item-height:', '--segment-active-bg:', '--segment-active-color:'):
        assert token in comp
    switch = comp[comp.index('.board-view-switch { display'):]
    switch = switch[:switch.index('}')]
    tabs = css[css.index('.plan-context-controls .plan-tabs { display'):]
    tabs = tabs[:tabs.index('}')]
    for token in ('var(--segment-gap)', 'var(--segment-pad)', 'var(--segment-border)', 'var(--segment-radius)'):
        assert token in switch and token in tabs, token
    assert '.board-view-switch [aria-pressed="true"] { background: var(--segment-active-bg); color: var(--segment-active-color); }' in comp
    assert '.plan-context-controls .plan-tabs button[aria-pressed="true"] { border-bottom-color: transparent; background: var(--segment-active-bg); color: var(--segment-active-color); }' in css
    assert '.plan-context-controls .plan-tabs button + button::before {' in css  # divider between segments
    assert 'height: var(--segment-item-height)' in css[css.index('.plan-context-controls .plan-tabs button { position'):]


def test_breadcrumb_label_is_capitalised_plan():
    crumb = (STATIC / 'planning/breadcrumb.js').read_text()
    assert "'Plan', ...rest" in crumb and "'plan', ...rest" not in crumb


def test_view_switcher_shows_on_direct_plan_load():
    # Root cause (Round 15 bug): the switcher hid itself while planning.currentView() was still
    # 'home' and nothing re-synced after the board plan opened, so a direct /plan/ load lost it.
    app = (STATIC / 'app.js').read_text()
    sync = app[app.index('function syncBoardViewSwitch()'):]
    sync = sync[:sync.index('\n}\n')]
    assert 'planning.currentView() !==' not in sync
    assert "const planHome = mode === 'plan' && !state.planBoardView;" in sync
    assert "state.planBoardView = planMode && Boolean(options?.boardPlan);" in app
    # both plan entry paths pass the board-plan intent
    assert app.count("setWorkspaceMode('plan', { focus: false, updateUrl: false, boardPlan: true });") == 1
    assert 'boardPlan: true' in app[app.index("if (route.kind === 'board' && route.view === 'plan') {"):][:300]


def test_round16_plan_header_interfaces_menu_and_centring():
    conv = (STATIC / 'planning/conversation.js').read_text()
    css = (STATIC / 'planning/styles.css').read_text()
    html = (TEMPLATES / 'index.html').read_text()
    # label "Interfaces" (ids/data unchanged)
    assert "wireframes: { label: 'Interfaces', help:" in conv
    assert "label: 'Pages'" not in conv and 'Pages/UI' not in conv and "label: 'Interface'," not in conv
    # Hide artifacts removed entirely, artifacts always visible, nothing persisted to get stuck on
    for gone in ('plan-canvas-toggle', 'canvasClosed', 'Hide artifacts', 'Show artifacts', 'plan-chat-only'):
        assert gone not in conv and gone not in css, gone
    assert 'panel.hidden = false;' in conv
    assert 'localStorage' not in conv
    # Rebuild is a canvas button. Reset zoom is the 100 magnifier on the zoom dock.
    assert '<button type="button" class="header-button primary" id="plan-build">Build tickets</button>' in conv
    assert '<button type="button" class="header-button" id="plan-rebuild-sitemap">Rebuild</button>' in conv
    assert 'role="menuitem">Build tickets' not in conv
    assert 'data-iface="reset-zoom"' in (STATIC / 'planning/iface-canvas.js').read_text()
    assert 'canvas.resetZoom()' in conv
    assert "path(session, '/rebuild-interfaces')" in conv
    assert 'class="plan-rebuild-status"' in conv and 'Rebuilding interface' in conv
    assert 'Rebuilding interfaces' not in conv
    assert 'plan-rebuild-pct' in conv and 'aria-valuenow' in conv
    assert '.plan-rebuild-bar' in css and '.plan-rebuild-pct' in css
    assert 'iface-shot-eye' in conv
    assert 'iface-part' in (STATIC / 'planning/iface-canvas.js').read_text()
    assert 'session.rebuildingInterfaces = true' in conv
    assert "model_name: session.model" in conv
    assert "path(session, '/scan')" not in conv
    assert 'Build tasks' not in conv and 'build tasks' not in conv
    assert "closePlanMenu(); build(session);" in conv and "closePlanMenu(); rebuildSitemap(session);" in conv
    assert 'id="plan-more-button"' not in conv and 'id="plan-header-menu"' not in conv
    for ident in ('plan-build', 'plan-rebuild-sitemap'):
        assert conv.count(f'id="{ident}"') == 1 and f'id="{ident}"' not in html
    assert 'id="thread-more-button"' in html and 'id="thread-header-menu"' in html
    # tabs centred on the header (absolute, containing block = position:relative .task-header)
    block = css[css.index('@media (min-width: 1400px) {'):]
    block = block[:block.index('\n}\n')]
    assert 'position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);' in block
    assert 'max-width: calc(50% - var(--plan-tabs-half) - 22px)' in block
