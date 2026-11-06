// Plan chat header helpers (chat build part 1): active chat choice, image validation, labels, memory.
import assert from 'node:assert/strict';
import { pickActiveChat, validateImageFiles, rejectionMessage, imageLabel, isImageAsset, rememberChat, rememberedChat, chatHeaderHtml, CHAT_IMAGE_MAX_BYTES } from '../../distr/gui/web/static/development/planning/chat-header.js';

const chats = [{ id: 3, title: 'Plan: Shop', archived: false, revision: 0, messages: 4 }, { id: 9, title: 'Chat 2', archived: true }, { id: 12, title: 'Chat 3', archived: false }];
assert.equal(pickActiveChat(chats, 3, 12), 3);            // remembered live chat wins
assert.equal(pickActiveChat(chats, 9, 12), 12);           // remembered but archived -> server default
assert.equal(pickActiveChat(chats, 99, null), 12);        // unknown -> newest live
assert.equal(pickActiveChat([], null, null), null);

const file = (type, size, name = 'f') => ({ type, size, name });
const { accepted, rejected } = validateImageFiles([file('image/png', 10), file('image/gif', 10), file('application/pdf', 10), file('image/jpeg', CHAT_IMAGE_MAX_BYTES + 1), file('image/webp', 0)], 0);
assert.equal(accepted.length, 1);
assert.deepEqual(rejected.map((r) => r.reason), ['type', 'type', 'size', 'size']);
assert.match(rejectionMessage(rejected), /PNG, JPEG or WebP/);
const full = validateImageFiles([file('image/png', 5), file('image/png', 5)], 7);
assert.deepEqual([full.accepted.length, full.rejected[0].reason], [1, 'count']);
assert.match(rejectionMessage(full.rejected), /at most 8/);
assert.equal(rejectionMessage([]), '');

assert.equal(imageLabel({ id: 41, name: 'x.png' }), 'Image 41');      // real asset id, never an index
assert.ok(isImageAsset({ mime_type: 'image/webp' }) && !isImageAsset({ mime_type: 'image/svg+xml' }));

const store = new Map();
const fake = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
rememberChat(fake, 5, 12);
assert.equal(rememberedChat(fake, 5), 12);
rememberChat(fake, 5, null);
assert.equal(rememberedChat(fake, 5), null);
assert.equal(rememberedChat({ getItem() { throw new Error('denied'); } }, 5), null);

const esc = (v) => String(v).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const html = chatHeaderHtml({ chatId: 3, conversation: { chats: [{ ...chats[0], title: '<b>x</b>' }, chats[1], chats[2]] } }, esc);
assert.ok(html.includes('&lt;b&gt;x&lt;/b&gt;') && !html.includes('<b>x</b>'));
assert.ok(html.includes('aria-checked="true" data-chat-switch="3"'));
assert.ok(html.includes('data-chat-restore="9"') && !html.includes('data-chat-switch="9"'));
assert.ok(html.includes('id="plan-chat-clear"') && html.includes('id="plan-chat-compact"') && html.includes('id="plan-chat-new"'));
console.log('plan chat header: ok');
