import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const root = new URL('../../', import.meta.url);
const source = path => fs.readFileSync(new URL(path, root), 'utf8');

test('Development progress, completion and reconnect messages refresh silently', async t => {
    const { createThreadLiveUpdates } = await import(`data:text/javascript;base64,${Buffer.from(source('distr/gui/web/static/development/threads/live.js')).toString('base64')}`);
    const saved = Object.fromEntries(['window', 'location', 'WebSocket', 'setTimeout', 'clearTimeout'].map(key => [key, globalThis[key]]));
    t.after(() => { for (const [key, value] of Object.entries(saved)) globalThis[key] = value; });
    let sounds = 0, refreshes = 0, socket, timerId = 0;
    const timers = new Map();
    globalThis.window = { DecisionsNotificationSound: { playCurrent() { sounds++; } } };
    globalThis.location = { protocol: 'http:', host: 'notification.test' };
    globalThis.WebSocket = class {
        static OPEN = 1;
        constructor() { socket = this; this.readyState = 1; }
        send() {}
        close() {}
    };
    globalThis.setTimeout = fn => { timers.set(++timerId, fn); return timerId; };
    globalThis.clearTimeout = id => timers.delete(id);
    const live = createThreadLiveUpdates({ token: '', isActive: id => id === 17, refresh: () => refreshes++ });
    live.connect(17);
    for (let n = 0; n < 1000; n++) socket.onmessage({ data: JSON.stringify({ type: 'turn_event', chat_id: 17, event_type: n % 2 ? 'tool_completed' : 'tool_started' }) });
    for (const type of ['chat_updated', 'chat_message_added', 'subscribed', 'turn_event']) {
        socket.onmessage({ data: JSON.stringify({ type, chat_id: 17, event_type: 'turn_completed', role: 'assistant' }) });
    }
    assert.equal(sounds, 0);
    assert.equal(timers.size, 1, 'progress still coalesces into one refresh');
    [...timers.values()][0](); timers.clear();
    assert.equal(refreshes, 1);
    socket.onclose();
    [...timers.values()][0](); timers.clear();
    socket.onmessage({ data: JSON.stringify({ type: 'turn_event', chat_id: 17, event_type: 'turn_completed' }) });
    assert.equal(sounds, 0, 'reconnect does not ring either');
    live.disconnect();
});

for (const hidden of [true, false]) test(`Chat replies render silently with document.hidden=${hidden}`, () => {
    const chat = source('distr/gui/web/static/chat/js/chat.js');
    const start = chat.indexOf('function handleChatEventMessageAdded(msg) {');
    const end = chat.indexOf('\nfunction findRenderedMessageByIdentity', start);
    assert.ok(start >= 0 && end > start);
    let sounds = 0;
    const rendered = [];
    const context = {
        currentChatId: 17, streamingChatId: null, _lastStreamFinalizedPlain: null,
        document: { hidden }, window: { DecisionsNotificationSound: { playCurrent() { sounds++; } } },
        findRenderedMessageByIdentity: () => null,
        _normalizeMsgPlain: value => String(value || '').trim(),
        hasRenderedMessagePlain: (_, value) => rendered.some(message => message.content === value),
        createMessageElement: message => message,
        insertMessageElementInOrder: element => rendered.push(element),
        flushPendingAssistantTools() {}, scrollToBottom() {}, scheduleRefreshChatHeaderStats() {},
    };
    vm.createContext(context);
    vm.runInContext(chat.slice(start, end), context);
    for (const content of ['First reply', 'First reply', 'Second reply']) context.handleChatEventMessageAdded({ chat_id: 17, role: 'assistant', content });
    assert.deepEqual(rendered.map(message => message.content), ['First reply', 'Second reply']);
    assert.equal(sounds, 0);
});

test('Settings can still explicitly preview the selected notification sound', async () => {
    const played = [];
    const context = { Date, Audio: class { constructor(url) { played.push(url); } play() { return Promise.resolve(); } } };
    context.window = context;
    vm.createContext(context);
    vm.runInContext(source('distr/gui/web/static/shared/js/notification_sound.js'), context);
    await context.DecisionsNotificationSound.play('bell');
    assert.deepEqual(played, ['/assets/sounds/notifications/bell.wav']);
});
