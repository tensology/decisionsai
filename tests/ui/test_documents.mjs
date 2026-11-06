import test from 'node:test';
import assert from 'node:assert/strict';
const base = '../../distr/gui/web/static/development/planning/';
const D = await import(new URL(base + 'documents.js', import.meta.url));
const C = await import(new URL(base + 'documents-config.js', import.meta.url));
const S = await import(new URL(base + 'inline-edit/sanitize.js', import.meta.url));
const it = (id, item_type, extra = {}) => ({ id, item_type, title: `${item_type} ${id}`, ...extra });

test('documents: order frac, brief, prd, handover, skills, file_structure, decision, overview, discovery, user docs; nothing dropped', () => {
    const items = [it(1, 'brief'), it(2, 'architecture'), it(3, 'prd'), it(4, 'frac'), it(5, 'skills'), it(6, 'handover'), it(7, 'file_structure'),
        it(8, 'decision'), it(9, 'project_overview'), it(10, 'discovery_proposal'), it(11, 'flows'), it(12, 'wireframe'), it(13, 'erd'), it(14, 'document'), it(15, 'decision')];
    const docs = D.orderDocuments(items);
    assert.deepEqual(docs.map((d) => d.id), [4, 1, 3, 6, 5, 7, 8, 15, 9, 10, 14]);
    assert.ok(!docs.some((d) => ['architecture', 'flows', 'wireframe', 'erd'].includes(d.item_type)));   // stay on Data / Interfaces
    const docTypes = new Set(items.filter(D.isDocument).map((d) => d.id));
    assert.equal(docTypes.size, docs.length);
});
test('documents: always one open, FRAC by default, remembered selection wins, stale selection falls back', () => {
    const docs = D.orderDocuments([it(1, 'brief'), it(4, 'frac'), it(3, 'prd')]);
    assert.equal(D.openDocument(docs, undefined).id, 4);
    assert.equal(D.openDocument(docs, 3).id, 3);
    assert.equal(D.openDocument(docs, '999').id, 4);
    assert.equal(D.openDocument(D.orderDocuments([it(1, 'brief'), it(3, 'prd')]), null).id, 1);   // no FRAC item: first tab
    assert.equal(D.openDocument([], 1), null);
});
test('documents: labels, user documents and duplicates use titles', () => {
    const docs = [it(4, 'frac'), it(8, 'decision', { title: 'Auth choice' }), it(9, 'decision', { title: 'DB choice' }), it(14, 'document', { title: 'Runbook' }), it(2, 'prd')];
    assert.equal(D.documentLabel(docs[0], docs), 'FRAC');
    assert.equal(D.documentLabel(docs[4], docs), 'PRD');
    assert.equal(D.documentLabel(docs[1], docs), 'Auth choice');
    assert.equal(D.documentLabel(docs[3], docs), 'Runbook');
    assert.ok(D.isUserDocument(docs[3]) && !D.isUserDocument(docs[0]));
});
test('documents: roving tab keys and modes', () => {
    assert.equal(D.moveTab(3, 2, 'ArrowRight'), 0);
    assert.equal(D.moveTab(3, 0, 'ArrowLeft'), 2);
    assert.equal(D.moveTab(3, 1, 'Home'), 0);
    assert.equal(D.moveTab(3, 1, 'End'), 2);
    assert.equal(D.moveTab(3, 1, 'a'), -1);
    assert.equal(D.normaliseMode('raw'), 'raw');
    assert.equal(D.normaliseMode('nope'), 'inline');
});
test('documents flag: on by default, test hook can force it off or on', () => {
    assert.equal(C.USER_DOCUMENTS_ENABLED, true);
    assert.equal(C.userDocumentsEnabled({}), true);
    assert.equal(C.userDocumentsEnabled({ __DECISIONS_TEST_FLAGS__: { userDocuments: false } }), false);
    assert.equal(C.userDocumentsEnabled({ __DECISIONS_TEST_FLAGS__: { userDocuments: true } }), true);
    assert.equal(C.userDocumentsEnabled({ __DECISIONS_TEST_FLAGS__: { userDocuments: 'no' } }), true);
});
test('raw markdown save: same allowlist for tags, markdown and code untouched', () => {
    const md = '# T\n\n> quote & more\n\n<script>alert(1)</script>**b** <u>u</u> <span style="color: red; position: fixed">c</span> <img src=x onerror=alert(1)> <a href="javascript:alert(1)">x</a>\n\n`<b>code</b>`\n\n```\n<script>keep in code</script>\n```\n';
    const out = S.sanitizeMarkdownHtml(md);
    assert.ok(out.includes('> quote & more'));
    assert.ok(!out.includes('<script>alert'));
    assert.ok(out.includes('<u>u</u>'));
    assert.ok(out.includes('<span style="color: red">c</span>'));
    assert.ok(!out.includes('<img') && !out.includes('javascript:'));
    assert.ok(out.includes('`<b>code</b>`'));
    assert.ok(out.includes('<script>keep in code</script>'));
    assert.equal(S.sanitizeMarkdownHtml('plain **md** 2 < 3 > 1'), 'plain **md** 2 < 3 > 1');
});
