"""Scoped navigation regressions for terminal return, project clicks, and Plan CLI.

Items 6-8: returning to a project's terminals must reconnect the live views;
a project click in Kanban or Plan must stay in that section; entering Plan from
a CLI thread hides the Live PTY pane and Chat/CLI toggle without killing the
session. Thread-to-thread CLI navigation must not flash the chat transcript.
"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / 'distr/gui/web/static/development'
TERMINALS = (STATIC / 'terminals/index.js').as_uri()
VIEW_SWITCH = (STATIC / 'planning/view-switch.js').as_uri()
CLI_MODE = (STATIC / 'threads/cli-mode.js').as_uri()
BREADCRUMB = (STATIC / 'planning/breadcrumb.js').as_uri()


def _node(script: str) -> None:
    result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout


def test_returning_to_project_terminals_reconnects_without_extra_poll_dials():
    """Blank panels were a saved sessionKey early-return after closeTerminalViews."""
    _node(r'''
import assert from 'node:assert/strict';

const sockets = [];
class FakeSocket {
    constructor(url) { this.url = url; this.readyState = 0; this.closed = false; sockets.push(this); }
    addEventListener() {}
    send() {}
    close() { this.closed = true; this.readyState = 3; }
}
FakeSocket.OPEN = 1;
FakeSocket.CONNECTING = 0;
globalThis.WebSocket = FakeSocket;
globalThis.CSS = { escape: (value) => String(value) };
globalThis.window = globalThis;
window.location = { protocol: 'http:', host: 'localhost' };
window.getComputedStyle = () => ({ paddingLeft: '0', paddingRight: '0', paddingTop: '0', paddingBottom: '0' });
window.Terminal = class {
    constructor() { this.rows = 24; this.cols = 80; this.disposed = false; }
    open() {}
    registerLinkProvider() { return { dispose() {} }; }
    writeln() {}
    onData() {}
    resize() {}
    dispose() { this.disposed = true; }
    focus() {}
};

class ClassList {
    constructor() { this.names = new Set(); }
    toggle(name, force) {
        const on = force === undefined ? !this.names.has(name) : Boolean(force);
        if (on) this.names.add(name); else this.names.delete(name);
        return on;
    }
    add(name) { this.names.add(name); }
    remove(name) { this.names.delete(name); }
    contains(name) { return this.names.has(name); }
}
function makeNode(attrs = {}) {
    return {
        attrs,
        classList: new ClassList(),
        dataset: {},
        clientWidth: 800,
        clientHeight: 400,
        addEventListener() {},
        replaceChildren() {},
        querySelector() { return null; },
        getBoundingClientRect() { return { width: 0, height: 0 }; },
    };
}
const outputs = new Map();
const sessionList = {
    dataset: {},
    classList: new ClassList(),
    _nodes: [],
    set innerHTML(html) {
        const nodes = [];
        for (const match of String(html).matchAll(/<([a-z0-9-]+)([^>]*)>/gi)) {
            const attrs = {};
            for (const attr of match[2].matchAll(/([\w:-]+)="([^"]*)"/g)) attrs[attr[1]] = attr[2];
            const node = makeNode(attrs);
            nodes.push(node);
            if (attrs['data-terminal-output']) outputs.set(attrs['data-terminal-output'], node);
        }
        this._nodes = nodes;
    },
    querySelectorAll(sel) {
        const attr = sel.match(/\[([\w-]+)\]/)?.[1];
        return this._nodes.filter((node) => attr && node.attrs[attr] !== undefined);
    },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
};
const elements = {
    'terminal-session-list': sessionList,
    'terminal-start-all': makeNode(),
    'terminal-stop-all': makeNode(),
    'terminal-running-count': { ...makeNode(), textContent: '' },
};
globalThis.document = {
    querySelector(sel) {
        const id = sel.match(/\[data-terminal-output="([^"]+)"\]/)?.[1];
        return id ? outputs.get(id) || null : null;
    },
};
const context = { workspaceMode: 'terminals', boards: [], projects: [{ id: 7 }, { id: 8 }], selectedBoardKey: '' };
const sessionsByProject = {
    7: [
        { process_id: 'proc-a', pid: 11, status: 'running', command: 'npm run dev', purpose: 'startup' },
        { process_id: 'proc-b', pid: 12, status: 'running', command: 'npm run api', purpose: 'startup' },
        { process_id: 'starting:1', pid: 0, status: 'starting', command: 'npm run dev', purpose: 'startup' },
        { process_id: 'discovered:99', pid: 99, status: 'running', command: 'node', purpose: 'discovered' },
    ],
    8: [
        { process_id: 'proc-c', pid: 21, status: 'running', command: 'npm run dev', purpose: 'startup' },
    ],
};
const { createTerminals } = await import(TERMINALS);
const terminals = createTerminals({
    context,
    token: '',
    el: (id) => elements[id] || null,
    actions: {
        async api(path) {
            const id = Number(String(path).split('/')[2]);
            if (String(path).includes('/startup-sessions')) return { sessions: sessionsByProject[id] || [] };
            return { sessions: [] };
        },
        escapeHtml: (value) => String(value),
        boardByKey: () => null,
        projectById: (id) => ({ id, name: 'Project' }),
        toast() {},
        renderSidebar() {},
        setWorkspaceMode() {},
        setDevelopmentLocation() {},
    },
});

async function openProject(projectId) {
    context.workspaceMode = 'terminals';
    terminals.primeTerminalSelection({ projectId });
    await terminals.loadProjectTerminalSessions(projectId);
    terminals.renderTerminalWorkspace();
}

await openProject(7);
assert.equal(sockets.length, 2, 'first visit connects the two running terminals');
assert.deepEqual(sockets.map((socket) => socket.url.includes('proc-a') || socket.url.includes('proc-b')), [true, true]);
const firstKey = sessionList.dataset.sessionKey;
assert.ok(firstKey);
terminals.renderTerminalWorkspace();
assert.equal(sockets.length, 2, 'an unchanged poll must not dial again');

context.workspaceMode = 'chat';
terminals.leave();
assert.equal(sessionList.dataset.sessionKey, undefined, 'leaving must drop the render key');
assert.equal(sockets.filter((socket) => socket.closed).length, 2);

context.workspaceMode = 'terminals';
terminals.renderTerminalWorkspace();
assert.equal(sockets.length, 4, 'returning to the same project reconnects both terminals');
assert.equal(sockets.filter((socket) => !socket.closed).length, 2);
terminals.renderTerminalWorkspace();
assert.equal(sockets.length, 4, 'the reconnect is once per return, not on the following paint');

await openProject(8);
assert.equal(sockets.length, 5, 'another project connects only its own running terminal');
assert.ok(sockets[4].url.includes('proc-c'));
assert.equal(sockets.filter((socket) => socket.closed).length, 4);
'''.replace('TERMINALS', repr(TERMINALS)))


def test_project_click_keeps_kanban_and_plan_and_thread_cli():
    _node(r'''
import assert from 'node:assert/strict';
const { projectClickTarget } = await import(VIEW_SWITCH);

const base = {
    boardHasTerminalProject: true,
    boardSupportsKanban: true,
    boardHasPlanProject: true,
    lastSection: 'thread',
    lastThreadId: 42,
};

assert.deepEqual(
    projectClickTarget({ ...base, workspaceMode: 'kanban', cliOn: true, hasCurrentChat: true }),
    { action: 'kanban' },
);
assert.deepEqual(
    projectClickTarget({ ...base, workspaceMode: 'plan', planBoardView: true, cliOn: true, hasCurrentChat: true }),
    { action: 'plan' },
);
// Plans home is not a board plan, so the clicked project's own last section wins.
assert.deepEqual(
    projectClickTarget({ ...base, workspaceMode: 'plan', planBoardView: false, cliOn: true, lastSection: 'kanban' }),
    { action: 'kanban' },
);
// CLI priority stays inside an open thread: project-to-project keeps the thread load.
assert.deepEqual(
    projectClickTarget({ ...base, workspaceMode: 'chat', hasCurrentChat: true, cliOn: true, lastThreadId: 77 }),
    { action: 'cli-thread', chatId: 77 },
);
assert.deepEqual(
    projectClickTarget({ ...base, workspaceMode: 'chat', hasCurrentChat: true, cliOn: false, lastThreadId: 77 }),
    { action: 'thread', chatId: 77 },
);
// Already on terminals: stay there even when CLI mode is on.
assert.deepEqual(
    projectClickTarget({ ...base, workspaceMode: 'terminals', cliOn: true }),
    { action: 'terminals' },
);
assert.deepEqual(
    projectClickTarget({ ...base, workspaceMode: 'terminals_home', cliOn: true }),
    { action: 'terminals' },
);
// Reports is not a thread, so CLI mode must not steal the click.
assert.deepEqual(
    projectClickTarget({ ...base, workspaceMode: 'reports', cliOn: true, lastSection: 'plan' }),
    { action: 'plan' },
);
// A thread with CLI on but no surviving thread falls through to Kanban, same as before.
assert.deepEqual(
    projectClickTarget({ ...base, workspaceMode: 'chat', hasCurrentChat: true, cliOn: true, lastThreadId: 0 }),
    { action: 'kanban' },
);
// Plan without a linked project cannot stay on Plan.
assert.deepEqual(
    projectClickTarget({ ...base, workspaceMode: 'plan', planBoardView: true, cliOn: true, boardHasPlanProject: false }),
    { action: 'kanban' },
);
assert.deepEqual(
    projectClickTarget({ ...base, workspaceMode: 'kanban', cliOn: true, boardSupportsKanban: false, boardHasPlanProject: false, lastThreadId: 0 }),
    { action: 'draft' },
);
'''.replace('VIEW_SWITCH', repr(VIEW_SWITCH)))


def test_plan_hides_cli_without_killing_session_or_breadcrumbs():
    _node(r'''
import assert from 'node:assert/strict';
const { createThreadCliMode } = await import(CLI_MODE);
const { planCrumbs, renderPlanCrumbs } = await import(BREADCRUMB);

class ClassList {
    constructor(initial) { this.names = new Set(initial || []); }
    toggle(name, force) {
        const on = force === undefined ? !this.names.has(name) : Boolean(force);
        if (on) this.names.add(name); else this.names.delete(name);
        return on;
    }
    add(name) { this.names.add(name); }
    remove(name) { this.names.delete(name); }
    contains(name) { return this.names.has(name); }
}
function node(id, extra = {}) {
    return { id, classList: new ClassList(extra.hidden ? ['hidden'] : []), dataset: {}, textContent: extra.text || '', hidden: false,
        clientWidth: extra.clientWidth ?? 0, clientHeight: extra.clientHeight ?? 0, disabled: false, title: '',
        setAttribute() {}, addEventListener() {}, appendChild(child) { this.children = this.children || []; this.children.push(child); },
        replaceChildren() {}, querySelector() { return null; } };
}
const pane = node('thread-cli-pane', { clientWidth: 640, clientHeight: 480 });
const toggle = node('thread-cli-mode-toggle');
const status = node('thread-cli-status');
const composer = node('development-composer-layer', { hidden: true });
const conversation = node('conversation', { hidden: true });
const hosts = node('thread-cli-xterm-hosts');
function crumbNode(id, text) {
    return { id, textContent: text, dataset: {}, parent: null,
        after(...nodes) { const list = this.parent.children; list.splice(list.indexOf(this) + 1, 0, ...nodes); nodes.forEach((n) => { n.parent = this.parent; }); },
        remove() { const list = this.parent.children; list.splice(list.indexOf(this), 1); } };
}
const crumb = { children: [] };
for (const child of [crumbNode('header-project', 'DecisionsAI'), crumbNode(null, '/'), crumbNode('header-task', 'Some thread')]) {
    child.parent = crumb;
    crumb.children.push(child);
}
const elements = {
    'thread-cli-pane': pane,
    'thread-cli-mode-toggle': toggle,
    'thread-cli-status': status,
    'development-composer-layer': composer,
    'conversation': conversation,
    'thread-cli-xterm-hosts': hosts,
};
const doc = {
    getElementById: (id) => elements[id] || crumb.children.find((child) => child.id === id) || null,
    createElement() { return crumbNode(null, ''); },
    querySelectorAll: () => crumb.children.filter((child) => child.dataset && 'planCrumb' in child.dataset),
};
renderPlanCrumbs(doc, planCrumbs('DecisionsAI', 'Pages'));
const crumbText = () => crumb.children.map((child) => child.textContent).join(' ');
assert.equal(crumbText(), 'DecisionsAI / Plan / Pages');

const ctx = { workspaceMode: 'chat', chatId: 11, projectId: 7, backendId: 'pi' };
const apiCalls = [];
const socket = { readyState: 1, closed: false, close() { this.closed = true; }, send() {}, addEventListener() {} };
globalThis.window = globalThis;
window.location = { protocol: 'http:', host: 'localhost' };
window.getComputedStyle = () => ({ paddingLeft: '0', paddingRight: '0', paddingTop: '0', paddingBottom: '0' });
window.requestAnimationFrame = (fn) => setTimeout(fn, 0);
window.Terminal = class { open() {} registerLinkProvider() { return { dispose() {} }; } onData() {} resize() {} dispose() {} };
const cli = createThreadCliMode({
    el: (id) => elements[id] || null,
    token: '',
    toast() {},
    getContext: () => ({ ...ctx }),
    api: async (path, opts) => { apiCalls.push(path); return { tabs: [], sessions: [], ok: true, ...(opts || {}) }; },
});
const state = cli._state;
state.on = true;
state.chatId = 11;
state.projectId = 7;
state.backendId = 'pi';
state.recoveredProjectId = 7;
state.recoveredProjects.add(7);
state.sessions[11] = { chatId: 11, projectId: 7, processId: 'pty-11', command: 'pi', socket, host: node('host-11'), term: { registerLinkProvider() { return { dispose() {} }; }, onData() {}, dispose() {} } };

cli.syncFromShell();
assert.equal(pane.classList.contains('hidden'), false);
assert.equal(toggle.classList.contains('hidden'), false);

ctx.workspaceMode = 'plan';
cli.syncFromShell();
assert.equal(pane.classList.contains('hidden'), true, 'Live PTY pane hides on Plan');
assert.equal(toggle.classList.contains('hidden'), true, 'Chat/CLI toggle hides on Plan');
assert.equal(state.on, true, 'CLI mode stays on');
assert.equal(socket.closed, false, 'the PTY socket stays open');
assert.equal(state.sessions[11].socket, socket);
assert.equal(crumbText(), 'DecisionsAI / Plan / Pages');
assert.equal(apiCalls.some((path) => String(path).includes('/stop')), false);
'''.replace('CLI_MODE', repr(CLI_MODE)).replace('BREADCRUMB', repr(BREADCRUMB)))


def test_thread_to_thread_cli_navigation_does_not_flash_chat():
    _node(r'''
import assert from 'node:assert/strict';
const { createThreadCliMode } = await import(CLI_MODE);

class ClassList {
    constructor(initial) { this.names = new Set(initial || []); this.removes = []; }
    toggle(name, force) {
        const on = force === undefined ? !this.names.has(name) : Boolean(force);
        if (on) this.names.add(name); else this.remove(name);
        return on;
    }
    add(name) { this.names.add(name); }
    remove(name) { if (name === 'hidden') this.removes.push(name); this.names.delete(name); }
    contains(name) { return this.names.has(name); }
}
function node(extra = {}) {
    return { classList: new ClassList(extra.hidden ? ['hidden'] : []), dataset: {}, textContent: '', hidden: Boolean(extra.hostHidden),
        clientWidth: extra.clientWidth ?? 0, clientHeight: extra.clientHeight ?? 0, disabled: false,
        setAttribute() {}, addEventListener() {}, appendChild() {}, replaceChildren() {}, querySelector() { return null; } };
}
const pane = node({ clientWidth: 640, clientHeight: 400 });
const conversation = node({ hidden: true });
const composer = node({ hidden: true });
const elements = {
    'thread-cli-pane': pane,
    'thread-cli-mode-toggle': node(),
    'thread-cli-status': node(),
    'development-composer-layer': composer,
    'conversation': conversation,
    'thread-cli-xterm-hosts': node(),
};
function liveSocket() {
    return { readyState: 1, closed: false, close() { this.closed = true; }, send() {}, addEventListener() {} };
}
const socketA = liveSocket();
const socketB = liveSocket();
const constructed = [];
class FakeSocket {
    constructor(url) { this.url = url; this.readyState = 0; constructed.push(this); }
    addEventListener() {}
    send() {}
    close() { this.closed = true; }
}
FakeSocket.OPEN = 1;
FakeSocket.CONNECTING = 0;
globalThis.WebSocket = FakeSocket;
globalThis.window = globalThis;
window.location = { protocol: 'http:', host: 'localhost' };
window.getComputedStyle = () => ({ paddingLeft: '0', paddingRight: '0', paddingTop: '0', paddingBottom: '0' });
window.requestAnimationFrame = (fn) => setTimeout(fn, 0);
window.Terminal = class { open() {} registerLinkProvider() { return { dispose() {} }; } onData() {} resize() {} dispose() {} focus() {} scrollToBottom() {} };
const ctx = { workspaceMode: 'chat', chatId: 11, projectId: 7, backendId: 'pi' };
const cli = createThreadCliMode({
    el: (id) => elements[id] || null,
    token: '',
    toast() {},
    getContext: () => ({ ...ctx }),
    api: async (path) => {
        if (String(path).endsWith('/recover')) {
            return { tabs: [
                { tab_id: 'thread-11', process_id: 'pty-11', command: 'pi', chat_id: 11 },
                { tab_id: 'thread-22', process_id: 'pty-22', command: 'pi', chat_id: 22 },
            ], active_tab_id: 'thread-22' };
        }
        return { tabs: [], sessions: [], ok: true };
    },
});
const state = cli._state;
state.on = true;
state.chatId = 11;
state.projectId = 7;
state.backendId = 'pi';
state.recoveredProjectId = 7;
state.recoveredProjects.add(7);
const host = () => node({ hostHidden: true });
state.sessions[11] = { chatId: 11, projectId: 7, tabId: 'thread-11', processId: 'pty-11', command: 'pi', socket: socketA, host: host(), term: { cols: 80, rows: 24, registerLinkProvider() { return { dispose() {} }; }, onData() {}, dispose() {}, resize() {}, scrollToBottom() {}, focus() {} } };
state.sessions[22] = { chatId: 22, projectId: 8, tabId: 'thread-22', processId: 'pty-22', command: 'pi', socket: socketB, host: host(), term: state.sessions[11].term };

function assertNoFlash(label) {
    assert.equal(conversation.classList.removes.length, 0, label + ' removed conversation hidden');
    assert.equal(composer.classList.removes.length, 0, label + ' removed composer hidden');
    assert.equal(conversation.classList.contains('hidden'), true, label);
    assert.equal(composer.classList.contains('hidden'), true, label);
    assert.equal(pane.classList.contains('hidden'), false, label);
}

cli.syncFromShell();
assertNoFlash('same thread');

ctx.chatId = 12;
state.sessions[12] = { chatId: 12, projectId: 7, tabId: 'thread-12', processId: 'pty-12', command: 'pi', socket: liveSocket(), host: host(), term: state.sessions[11].term };
cli.syncFromShell();
await new Promise((resolve) => setTimeout(resolve, 40));
assertNoFlash('thread on the same project');
assert.equal(socketA.closed, false);
assert.equal(state.on, true);
assert.equal(constructed.length, 0, 'an open PTY is not redialed');

ctx.chatId = 22;
ctx.projectId = 8;
cli.syncFromShell();
await new Promise((resolve) => setTimeout(resolve, 40));
assertNoFlash('thread on another project');
assert.equal(socketA.closed, false);
assert.equal(socketB.closed, false);
assert.equal(state.on, true);
assert.equal(constructed.length, 0);
'''.replace('CLI_MODE', repr(CLI_MODE)))


def test_app_wires_scoped_navigation_without_resetting_plan_crumbs():
    app = (STATIC / 'app.js').read_text()
    terminals = (STATIC / 'terminals/index.js').read_text()
    header = app.split("el('development-header-context').classList.toggle('hidden', !planMode);", 1)[1]
    header = header.split('const cliOwnsChat', 1)[0]
    assert 'if (!planMode) {' in header
    assert header.count('clearPlanCrumbs(document);') == 1
    assert header.count('syncHeader();') == 1
    assert '} else {' in header
    else_arm = header.split('} else {', 1)[1]
    assert 'threadCliMode.syncFromShell();' in else_arm
    assert 'syncHeader()' not in else_arm
    assert 'clearPlanCrumbs' not in else_arm
    sidebar = app.split('async function openBoardFromSidebar', 1)[1].split('function startBoardDevelopment', 1)[0]
    assert 'projectClickTarget(' in sidebar
    assert 'if (threadCliMode.isOn())' not in sidebar
    assert "target.action === 'cli-thread'" in sidebar
    assert 'await loadChat(target.chatId);' in sidebar
    assert "target.action === 'plan'" in sidebar
    assert "target.action === 'kanban'" in sidebar
    assert "target.action === 'terminals'" in sidebar
    close = terminals.split('function closeTerminalViews()', 1)[1].split('function openTerminalUrl', 1)[0]
    assert 'delete sessionList.dataset.sessionKey' in close
