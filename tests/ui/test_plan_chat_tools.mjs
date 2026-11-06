// Plan chat part 2 helpers: tool labels, drafts payload, card states/HTML, memory panel.
import assert from 'node:assert/strict';
import { toolLabel, draftsPayload, cardState, toolCardsHtml, memoryPanelHtml } from '../../distr/gui/web/static/development/planning/chat-tools.js';

const esc = (v) => String(v).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
assert.equal(toolLabel('update_interface'), 'Updated interface');
assert.equal(toolLabel('create_erd'), 'Created ERD');
assert.equal(toolLabel('weird'), 'Changed item');
assert.deepEqual(draftsPayload({ '12': { baseRevision: 3 }, '12:desc:home': { baseRevision: 3 }, 'x': {}, '7': {} }), [{ id: 7, base_revision: null }, { id: 12, base_revision: 3 }]);

const applied = { tool: 'update_document', id: 1, title: 'PRD', status: 'applied', from_revision: 1, to_revision: 2 };
assert.equal(cardState(applied, { id: 1, revision_count: 2 }), 'undoable');
assert.equal(cardState(applied, { id: 1, revision_count: 3 }), 'changed');
assert.equal(cardState({ ...applied, from_revision: 0 }, null), 'undone');
assert.equal(cardState({ status: 'conflict', id: 1 }, { id: 1 }), 'conflict');

const message = { id: 31, tool_calls: [applied, { tool: 'update_interface', id: 5, title: '<i>Home</i>', status: 'conflict', proposed_content: 'x' }] };
const html = toolCardsHtml(message, [{ id: 1, revision_count: 2 }, { id: 5, revision_count: 4 }], esc);
assert.ok(html.includes('data-tool-undo data-turn="31" data-card="0"') && html.includes('r1 → r2'));
assert.ok(html.includes('data-tool-apply') && html.includes('data-tool-keep') && html.includes('&lt;i&gt;Home'));
assert.ok(!toolCardsHtml(message, [], esc, new Set(['31:0', '31:1'])));
assert.equal(toolCardsHtml({ id: 1 }, [], esc), '');

const panel = memoryPanelHtml({ enabled: true, count: 1, records: [{ text: '<script>x</script> [REDACTED:api-key]', at: '2026-11-03T08:00:00Z' }] }, esc);
assert.ok(panel.includes('checked') && panel.includes('&lt;script&gt;') && !panel.includes('<script>'));
assert.ok(memoryPanelHtml({ enabled: false, count: 0, records: [] }, esc).includes('data-memory-forget="chat" disabled'));
console.log('plan chat tools: ok');
