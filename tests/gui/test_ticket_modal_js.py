"""Shared ticket/proposal modal helpers: readable JSON notes, Files drop zone, constant height."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / 'distr/gui/web/static/development'
HELPERS = (STATIC / 'shared/ticket-modal.js').as_uri()


def _node(script: str) -> None:
    result = subprocess.run(['node', '--input-type=module', '-e', script.replace('SOURCE', repr(HELPERS))],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_json_note_round_trips_to_identical_json():
    _node(r'''
import assert from 'node:assert/strict';
const h = await import(SOURCE);
const samples = [
  '{"goal":"Ship","steps":[{"id":1,"done":false,"notes":null},{"id":2,"tags":["a","b"]}],"meta":{"owner":"Paul","nested":{"deep":"ü 日本 🚀"}},"empty":{},"none":[]}',
  '{\n  "b": 2,\n  "a": {\n    "z": true,\n    "y": 1.5\n  }\n}',
  '[1, "two", {"three": 3}]',
];
for (const text of samples) {
  const parsed = h.parseNoteJson(text);
  assert.equal(parsed.kind, 'json');
  const rows = h.flattenNote(parsed.value);
  // Feed every readable leaf back unchanged: JSON must be identical (same text, same shape, same order).
  const edits = new Map(rows.filter(r => r.kind === 'leaf' && r.editable).map(r => [r.key, r.text]));
  assert.equal(h.serializeNote(text, parsed.value, edits), text);
  assert.deepEqual(h.applyNoteEdits(parsed.value, edits), parsed.value);
  assert.equal(JSON.stringify(h.applyNoteEdits(parsed.value, edits)), JSON.stringify(parsed.value));
  assert.equal(h.noteChanged(text, h.serializeNote(text, parsed.value, edits)), false);
}
''')


def test_readable_rows_are_labeled_and_nested():
    _node(r'''
import assert from 'node:assert/strict';
const h = await import(SOURCE);
const rows = h.flattenNote(JSON.parse('{"acceptance_criteria":["works"],"contextNotes":{"repo":"x"}}'));
assert.deepEqual(rows.map(r => [r.kind, r.label, r.depth]), [
  ['group', 'Acceptance criteria', 0], ['leaf', 'Item 1', 1], ['group', 'Context Notes', 0], ['leaf', 'Repo', 1]
]);
const html = h.noteFieldsHtml(rows, s => String(s).replaceAll('<', '&lt;'));
assert.match(html, /<label class="ticket-note-field"[^>]*><span>Repo<\/span><input type="text" data-note-path=/);
''')


def test_edits_keep_types_key_order_and_unknown_keys():
    _node(r'''
import assert from 'node:assert/strict';
const h = await import(SOURCE);
const text = '{"z_unknown":{"keep":1},"title":"Old","count":3,"flag":false,"maybe":null,"list":["a"]}';
const value = h.parseNoteJson(text).value;
const edits = new Map([
  [JSON.stringify(['title']), 'New ✓'], [JSON.stringify(['count']), '4'],
  [JSON.stringify(['flag']), 'true'], [JSON.stringify(['list', 0]), 'b'],
]);
const out = h.serializeNote(text, value, edits);
assert.equal(out, '{"z_unknown":{"keep":1},"title":"New ✓","count":4,"flag":true,"maybe":null,"list":["b"]}');
assert.ok(h.noteChanged(text, out));
// A number field given non-numeric text keeps the text instead of corrupting it.
assert.equal(JSON.parse(h.serializeNote(text, value, new Map([[JSON.stringify(['count']), 'three']]))).count, 'three');
// Pretty-printed notes stay pretty-printed with the same indent.
const pretty = '{\n    "a": 1\n}';
assert.equal(h.serializeNote(pretty, JSON.parse(pretty), new Map([[JSON.stringify(['a']), '2']])), '{\n    "a": 2\n}');
''')


def test_non_json_and_empty_notes_stay_plain_text():
    _node(r'''
import assert from 'node:assert/strict';
const h = await import(SOURCE);
for (const text of ['', '   ', 'Plain notes about the ticket', '{not json', '"just a string"', '42', 'null', '[broken']) {
  assert.equal(h.parseNoteJson(text).kind, 'text', text);
  assert.equal(h.serializeNote(text, null, new Map()), text);
}
assert.equal(h.noteChanged('same', 'same  '), false);
assert.equal(h.noteChanged('', 'new'), true);
''')


def test_drop_zone_hover_state_and_drop_delivers_files():
    _node(r'''
import assert from 'node:assert/strict';
const h = await import(SOURCE);
function fakeNode() {
  const classes = new Set(); const handlers = {};
  return { dataset: {}, classes, handlers,
    classList: { add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c) },
    addEventListener: (type, fn) => { handlers[type] = fn; }, querySelector: () => pick };
}
const pick = { handlers: {}, addEventListener(type, fn) { this.handlers[type] = fn; } };
const zone = fakeNode(); let clicked = 0; const received = [];
h.bindFileDropZone(zone, files => received.push(...files), { click: () => { clicked += 1; } });
const ev = (files) => ({ preventDefault() {}, dataTransfer: { types: ['Files'], files } });
zone.handlers.dragenter(ev([]));
assert.ok(zone.classes.has('is-dragover'));
zone.handlers.drop(ev(['a.png', 'b.pdf']));
assert.ok(!zone.classes.has('is-dragover'));
assert.deepEqual(received, ['a.png', 'b.pdf']);
pick.handlers.click();
assert.equal(clicked, 1);
// Disabled zones (proposals) ignore drops.
const off = fakeNode(); off.classes.add('disabled'); const none = [];
h.bindFileDropZone(off, f => none.push(...f));
off.handlers.drop(ev(['x']));
assert.deepEqual(none, []);
assert.match(h.fileDropZoneHtml('z', { disabled: true }), /ticket-file-drop disabled/);
assert.doesNotMatch(h.fileDropZoneHtml('z', { disabled: true }), /data-file-pick/);
''')


def test_ticket_dialog_has_files_tab_without_links_and_uses_existing_upload_endpoint():
    html = (ROOT / 'distr/gui/web/templates/development/boards/ticket-dialog.html').read_text()
    js = (STATIC / 'boards/index.js').read_text()
    assert '>Files <span id="kanban-ticket-attachment-count">' in html
    assert 'Links' not in html and 'kanban-ticket-link' not in html
    assert 'id="kanban-ticket-dropzone" data-file-drop' in html
    assert 'id="kanban-ticket-notes-fields"' in html
    assert "bindFileDropZone(el('kanban-ticket-dropzone'), uploadKanbanTicketFiles" in js
    assert "`/tickets/tickets/${ticket.ticket_id}/files`" in js
    assert 'noteChanged(state.noteOriginal, note)' in js
    assert 'addKanbanTicketLink' not in js


def test_ticket_and_proposal_modals_share_one_constant_height():
    shared = (STATIC / 'shared/components.css').read_text()
    boards = (STATIC / 'boards/styles.css').read_text()
    incoming = (STATIC / 'incoming/styles.css').read_text()
    assert '--ticket-modal-height: min(640px, calc(100vh - 2rem))' in shared
    assert '.kanban-ticket-dialog { width: var(--ticket-modal-width); height: var(--ticket-modal-height);' in boards
    assert 'height: fit-content' not in boards.split('.kanban-ticket-dialog {', 1)[1].split('}', 1)[0]
    assert 'height: var(--ticket-modal-height); min-height: var(--ticket-modal-height);' in incoming
    proposals = (STATIC / 'incoming/index.js').read_text()
    assert "from '../shared/ticket-modal.js" in proposals
    assert "fileDropZoneHtml('proposal-file-drop'" in proposals


def test_proposal_thread_toast_lists_files_that_could_not_be_fetched():
    _node(r'''
import assert from 'node:assert/strict';
const {proposalThreadToast} = await import(SOURCE);
assert.equal(proposalThreadToast({chat_id:1}), 'Thread created.');
assert.equal(proposalThreadToast({chat_id:1, failed_attachments:[]}), 'Thread created.');
assert.equal(proposalThreadToast({chat_id:1, failed_attachments:[{name:'a.pdf'},{name:'voice.ogg'}]}),
  'Thread created. 2 files could not be fetched: a.pdf, voice.ogg');
''')
    text = (STATIC / 'incoming/index.js').read_text()
    assert 'proposalThreadToast(result)' in text
    assert 'Add your own files after this becomes a ticket' not in text
    assert "fileDropZoneHtml('proposal-file-drop', { disabled: true" in text
