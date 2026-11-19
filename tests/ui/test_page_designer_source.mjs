import { register } from 'node:module';
import test from 'node:test';
import assert from 'node:assert/strict';
register(new URL('./strip-query-loader.mjs', import.meta.url).href);
const D = await import('../../distr/gui/web/static/development/planning/page-designer-source.js');

const SRC = [
    'screen "Services" route=/services',
    '  #| Lists the workshop services and links to booking.',
    '  heading "Our services"',
    '  row',
    '    button "Book" route=/book',
    '      #| primary call to action',
    '    text "From R250"',
    '  stack',
    '    input "Bike model" required',
].join('\n');
const page = () => D.designPage(SRC, 0);
const node = (p, label) => [...p.layers.values()].find(e => (e.node.label || e.node.type) === label);

test('remove deletes a widget with its annotations and selects its neighbour', () => {
    const p = page();
    const btn = node(p, 'Book');
    const out = D.removeWidget(SRC, btn.node, btn.parent);
    assert.ok(!out.source.includes('Book'));
    assert.ok(!out.source.includes('primary call to action'));
    assert.ok(out.source.includes('text "From R250"'));
    assert.equal(out.line, 4, 'falls back to the row when it was the first child');
    assert.ok(D.designPage(out.source, 0).valid);
});

test('remove deletes a container and its whole subtree', () => {
    const p = page();
    const row = node(p, 'row');
    const out = D.removeWidget(SRC, row.node, row.parent);
    assert.ok(!/Book|From R250|row/.test(out.source));
    assert.equal(out.line, 3, 'selects the previous sibling heading');
    assert.ok(D.designPage(out.source, 0).valid);
});

test('remove refuses the screen root and a missing parent', () => {
    const p = page();
    assert.equal(D.removeWidget(SRC, p.layers.get(1).node, null), null);
    assert.equal(D.removeWidget(SRC, node(p, 'Book').node, null), null);
});

test('moving a widget carries its annotation lines', () => {
    assert.ok(page().valid);
    const p = page();
    const btn = node(p, 'Book');
    const stack = node(p, 'stack');
    const out = D.moveWidget(SRC, btn.node, stack.node, stack.node.children.length);
    const lines = out.source.split('\n');
    const at = lines.indexOf('    button "Book" route=/book');
    assert.ok(at > lines.indexOf('  stack'));
    assert.equal(lines[at + 1], '      #| primary call to action');
    assert.ok(D.designPage(out.source, 0).valid);
});

test('editing a label keeps attributes and flags and escapes quotes', () => {
    const p = page();
    const field = node(p, 'Bike model');
    const out = D.editWidgetLabel(SRC, field.node, 'Bike "model" \\ size');
    const reparsed = D.designPage(out.source, 0);
    assert.ok(reparsed.valid);
    const edited = reparsed.layers.get(field.node.line).node;
    assert.equal(edited.label, 'Bike "model" \\ size');
    assert.equal(edited.attrs.required, 'true');
});

test('adding a widget lands after trailing annotations at the right depth', () => {
    const p = page();
    const row = node(p, 'row');
    const out = D.addWidget(SRC, row.node, 'button');
    assert.ok(D.designPage(out.source, 0).valid);
    assert.equal(out.source.split('\n')[out.line - 1], '    button "Button" variant=primary');
    assert.equal(D.addWidget(SRC, row.node, 'script'), null);
});

test('add inserts a dropped widget before the child it was dropped on', () => {
    const p = page();
    const row = node(p, 'row');
    const out = D.addWidget(SRC, row.node, 'text', 1);
    const lines = out.source.split('\n');
    assert.equal(lines[out.line - 1], '    text "Text"');
    assert.ok(lines.indexOf('    text "Text"') < lines.indexOf('    text "From R250"'));
    assert.ok(lines.indexOf('      #| primary call to action') < lines.indexOf('    text "Text"'), 'stays after the button note');
    assert.ok(D.designPage(out.source, 0).valid);
});

test('add at index 0 goes first and past the end appends', () => {
    const p = page();
    const stack = node(p, 'stack');
    const first = D.addWidget(SRC, stack.node, 'button', 0).source.split('\n');
    assert.ok(first.indexOf('    button "Button" variant=primary') < first.indexOf('    input "Bike model" required'));
    const last = D.addWidget(SRC, stack.node, 'button', 9).source.split('\n');
    assert.equal(last.at(-1), '    button "Button" variant=primary');
});
