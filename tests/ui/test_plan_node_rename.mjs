import test from 'node:test';
import assert from 'node:assert/strict';
const M = await import(new URL('../../distr/gui/web/static/development/planning/flow-meta.js', import.meta.url));
const C = 'screen "Customers" id=customers route=/c\n#| desc\n  heading "x"\nscreen "Saved" id=saved\n  text "y"\n# decisions-flow: {"nodes":{"customers":{"links":{"saved":"go"}},"saved":{"note":"n"}},"v":1}\n';

test('rename moves id=, sidecar key and link targets together; #| untouched; sidecar last', () => {
    const r = M.renameNodeId(C, 'wire', 'saved', 'done');
    assert.ok(r.content.includes('screen "Saved" id=done') && !r.content.includes('id=saved'));
    assert.deepEqual(M.readSidecar(r.content, 'wire').nodes, { customers: { links: { done: 'go' } }, done: { note: 'n' } });
    assert.ok(r.content.includes('\n#| desc\n'));
    assert.ok(r.content.trimEnd().split('\n').pop().startsWith('# decisions-flow:'));
});

test('rename guards: clash, bad id, unknown id, mermaid, no-op, no sidecar', () => {
    assert.throws(() => M.renameNodeId(C, 'wire', 'saved', 'customers'), /already uses/);
    assert.throws(() => M.renameNodeId(C, 'wire', 'saved', '9bad'), /letter first/);
    assert.throws(() => M.renameNodeId(C, 'wire', 'ghost', 'x'), /No page has id/);
    assert.throws(() => M.renameNodeId('flowchart LR\n A-->B\n', 'mermaid', 'A', 'C'), /page outlines/);
    assert.equal(M.renameNodeId(C, 'wire', 'saved', 'saved').content, C);
    assert.equal(M.renameNodeId('screen "A" id=a-1\n', 'wire', 'a-1', 'b').content, 'screen "A" id=b\n');
});
