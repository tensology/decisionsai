import test from 'node:test';
import assert from 'node:assert/strict';
const base = '../../distr/gui/web/static/development/planning/';
const F = await import(new URL(base + 'flow-meta.js', import.meta.url));
const W = await import(new URL(base + 'wireframe.js', import.meta.url));
const PAGE = 'screen "Customers" id=customers route=/customers\n  #| Lists every **customer**.\n  #|\n  #| - paged\n  heading "Customers"\n';
const TWO = PAGE + 'screen "Saved" route=/saved\n  text "Done"\n';
const FLOW = 'flowchart LR\n  A{Ok?} -->|yes| B([Done])\n  B --> C[(Store)]\n';

test('round trip: sorted compact JSON on the last line; read back equal', () => {
    const out = F.writeSidecar(PAGE, 'wire', { customers: { shape: 'diamond', note: 'Admins only', image: 17, links: { saved: 'on save' } } });
    const last = out.trimEnd().split('\n').pop();
    assert.equal(last, '# decisions-flow: {"nodes":{"customers":{"image":"17","links":{"saved":"on save"},"note":"Admins only","shape":"diamond"}},"v":1}');
    assert.ok(out.endsWith('\n'));
    const read = F.readSidecar(out, 'wire');
    assert.equal(read.error, '');
    assert.deepEqual(read.nodes, { customers: { image: '17', links: { saved: 'on save' }, note: 'Admins only', shape: 'diamond' } });
    assert.equal(read.body, PAGE);
    assert.equal(F.writeSidecar(out, 'wire', read.nodes), out);                  // idempotent
});
test('#| description lines are untouched; the wire parser ignores the sidecar', () => {
    const out = F.updateNodeMeta(PAGE, 'wire', { key: 'customers', patch: { shape: 'circle' } }).content;
    assert.ok(out.startsWith(PAGE));
    const a = W.parseWireframe(PAGE), b = W.parseWireframe(out);
    assert.deepEqual(b.diagnostics, a.diagnostics);
    assert.equal(JSON.stringify(b.screens.map((s) => [s.label, s.attrs.id, s.children.length])), JSON.stringify(a.screens.map((s) => [s.label, s.attrs.id, s.children.length])));
});
test('trailing position: the sidecar moves to the end and blank lines are trimmed', () => {
    const out = F.writeSidecar(PAGE + '\n\n\n', 'wire', { customers: { shape: 'oval' } });
    assert.match(out, /heading "Customers"\n# decisions-flow: /);
    const misplaced = '# decisions-flow: {"v":1,"nodes":{}}\n' + PAGE;
    assert.equal(F.readSidecar(misplaced, 'wire').error, 'The flow metadata line must be the last line of the item.');
    assert.deepEqual(F.readSidecar(misplaced, 'wire').nodes, {});
});
test('bad JSON is ignored with an error, never thrown', () => {
    for (const bad of ['{nope', '[]', '{"v":2,"nodes":{}}', 'null']) {
        const r = F.readSidecar(PAGE + `# decisions-flow: ${bad}\n`, 'wire');
        assert.equal(r.error, 'Flow metadata is not valid JSON.');
        assert.deepEqual(r.nodes, {});
    }
    assert.equal(F.readSidecar(PAGE, 'wire').error, '');
});
test('absent metadata: empty nodes, content unchanged; empty nodes removes the line', () => {
    assert.deepEqual(F.readSidecar(PAGE, 'wire'), { nodes: {}, body: PAGE, error: '' });
    const out = F.writeSidecar(PAGE, 'wire', { customers: { shape: 'square' } });
    assert.equal(F.writeSidecar(out, 'wire', {}), PAGE);
    assert.equal(F.updateNodeMeta(out, 'wire', { key: 'customers', patch: { shape: 'page' } }).content, PAGE);   // page = default
});
test('id auto-assign: first metadata on a screen without id= adds a unique slug in the same text', () => {
    const r = F.updateNodeMeta(TWO, 'wire', { screenLine: 6, patch: { note: 'Shown after save' } });
    assert.equal(r.id, 'saved');
    assert.ok(r.content.includes('screen "Saved" id=saved route=/saved\n'));
    assert.ok(r.content.startsWith(PAGE));
    assert.deepEqual(W.parseWireframe(r.content).diagnostics.filter((d) => d.severity === 'error'), []);
    const clash = 'screen "Saved" id=saved\nscreen "Saved"\n';
    assert.equal(F.ensureScreenId(clash, 2).id, 'saved-2');
    assert.equal(F.ensureScreenId(clash, 1).added, false);
    assert.equal(F.slugId('42 Ünïcode Page!'), 'unicode-page');
    assert.throws(() => F.ensureScreenId(TWO, 2), /not a page/);
});
test('orphan keys are pruned on write', () => {
    const start = F.writeSidecar(PAGE, 'wire', { customers: { shape: 'oval' }, gone: { note: 'old' } });
    const r = F.updateNodeMeta(start, 'wire', { key: 'customers', patch: { note: 'n' } });
    assert.deepEqual(r.pruned, ['gone']);
    assert.deepEqual(Object.keys(F.readSidecar(r.content, 'wire').nodes), ['customers']);
});
test('mermaid: %% prefix, native ids, no shapes', () => {
    assert.deepEqual(F.nodeIds(FLOW, 'mermaid'), ['A', 'B', 'C']);
    const r = F.updateNodeMeta(FLOW, 'mermaid', { key: 'A', patch: { note: 'Check', links: { customers: 'opens' } } });
    assert.ok(r.content.startsWith(FLOW));
    assert.match(r.content, /\n%% decisions-flow: \{"nodes":\{"A":\{"links":\{"customers":"opens"\},"note":"Check"\}\},"v":1\}\n$/);
    assert.throws(() => F.updateNodeMeta(FLOW, 'mermaid', { key: 'A', patch: { shape: 'circle' } }), /set in the diagram itself/);
    assert.deepEqual(F.readSidecar(FLOW + '%% decisions-flow: {"v":1,"nodes":{"A":{"shape":"circle"}}}\n', 'mermaid').nodes, {});
});
test('limits: note 500, unknown shape, 16 KB, link label 120', () => {
    assert.throws(() => F.updateNodeMeta(PAGE, 'wire', { key: 'customers', patch: { note: 'x'.repeat(501) } }), /at most 500/);
    assert.throws(() => F.updateNodeMeta(PAGE, 'wire', { key: 'customers', patch: { shape: 'star' } }), /Unknown node shape "star"/);
    const many = {}; for (let i = 0; i < 40; i += 1) many[`customers`] = { note: 'y'.repeat(500) };
    const big = 'flowchart LR\n' + Array.from({ length: 40 }, (_, i) => `  N${i} --> M${i}\n`).join('');
    const nodes = Object.fromEntries(Array.from({ length: 40 }, (_, i) => [`N${i}`, { note: 'z'.repeat(500) }]));
    assert.throws(() => F.writeSidecar(big, 'mermaid', nodes), /larger than 16 KB/);
    const r = F.readSidecar(F.writeSidecar(PAGE, 'wire', { customers: { links: { saved: 'l'.repeat(200) } } }), 'wire');
    assert.equal(r.nodes.customers.links.saved.length, 120);
});
