import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const load = async name => import(`data:text/javascript;base64,${Buffer.from(await readFile(new URL(`../../distr/gui/web/static/development/planning/${name}.js`, import.meta.url), 'utf8')).toString('base64')}`);
const { wireframeSitemap } = await load('sitemap');
const { parseWireframe } = await load('wireframe');
const document = (id, source) => ({id, screens: parseWireframe(source).screens});

test('projects inherited navigation across artifacts, with stable deduplicated edges', () => {
    const graph = wireframeSitemap([
        document(1, 'template Shell\n  link "Customers" route=/customers\nscreen Home route=/ uses=Shell\n  link "Customers again" route=/customers?sort=name'),
        document(2, 'screen Customers route=/customers/\n  link Home route=/'),
    ]);
    assert.equal(graph.pages.length, 2);
    assert.match(graph.source, /p0 --> p1/);
    assert.match(graph.source, /p1 --> p0/);
    assert.equal(graph.source.split('p0 --> p1').length, 2);
    assert.deepEqual(graph.diagnostics, []);
});

test('unresolved and duplicate routes are explicit, never guessed', () => {
    const graph = wireframeSitemap([document(1, 'screen A route=/same\n  link Missing route=/missing\n  link Ambiguous route=/same\nscreen B route=/same')]);
    assert.equal(graph.diagnostics.length, 2);
    assert.match(graph.source, /not defined/);
    assert.match(graph.source, /ambiguous/);
    assert.doesNotMatch(graph.source, /p0 --> p1/);
});

test('labels cannot inject Mermaid directives and external links are not project pages', () => {
    const graph = wireframeSitemap([document(1, 'screen "<img>" route=/\n  link "External" href=https://example.org')]);
    assert.doesNotMatch(graph.source, /<img>|example.org/);
    assert.match(graph.source, /#60;img#62;/);
    assert.deepEqual(graph.diagnostics, []);
});

test('empty projects and bounded page counts', () => {
    assert.equal(wireframeSitemap([]).pages.length, 0);
    const doc = document(1, 'screen A');
    assert.throws(() => wireframeSitemap(Array(201).fill(doc)), /200 pages/);
});
