import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const source = await readFile(new URL('../../distr/gui/web/static/development/planning/build-results.js', import.meta.url), 'utf8');
const { buildResultsHtml } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const escape = text => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');

test('stored tasks retain parent hierarchy and execution dependencies', () => {
    const html = buildResultsHtml({ tasks: [
        { id: 2, parent_id: 1, title: 'Form', depends_on: [3] },
        { id: 1, parent_id: null, title: 'Customer feature', depends_on: [] },
        { id: 3, parent_id: 1, title: 'Model', depends_on: [] },
    ] }, escape);
    assert.match(html, /#1 Customer feature<\/span><ul><li><span>#2 Form/);
    assert.match(html, /<small>After #3<\/small>/);
    assert.equal((html.match(/#1 Customer feature/g) || []).length, 1);
});

test('legacy results, unsafe titles and malformed parent cycles are safe', () => {
    assert.equal(buildResultsHtml({ created: 1 }, escape), '');
    const html = buildResultsHtml({ tasks: [
        { id: 1, parent_id: 2, title: '<img onerror=alert(1)>', depends_on: ['<script>'] },
        { id: 2, parent_id: 1, title: 'Other' },
    ] }, escape);
    assert.doesNotMatch(html, /<img|<script>/);
    assert.match(html, /&lt;img/);
    assert.equal((html.match(/<li>/g) || []).length, 2);
});
