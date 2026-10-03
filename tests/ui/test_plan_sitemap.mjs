import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const load = async name => import(`data:text/javascript;base64,${Buffer.from(await readFile(new URL(`../../distr/gui/web/static/development/planning/${name}.js`, import.meta.url), 'utf8')).toString('base64')}`);
const { wireframeSitemap, connectedSitemap, layoutConnectedSitemap } = await load('sitemap');
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


test('connected sitemap shows only pages linked to stencils and keeps technical scaffolding out of the canvas', () => {
    const graph = connectedSitemap({
        scaffolding: [{id: 'base', kind: 'base_template', label: 'base.html', path: 'templates/base.html', template: 'base.html'}],
        nodes: [
            {id: 'base', kind: 'page', label: 'Base', route: '/base', status: 'verified', template: 'base.html'},
            {id: 'home', kind: 'page', label: 'Home', route: '/home', status: 'verified', template: 'home.html'},
            {id: 'catalog', kind: 'page_template', label: 'Catalog', template: 'catalog.html', status: 'verified'},
            {id: 'list', kind: 'page', label: 'List', route: '/catalog', status: 'inferred'},
            {id: 'gone', kind: 'page', label: 'Gone', route: '/gone', status: 'missing'},
            {id: 'partial', kind: 'page', label: 'Partial', route: '/partial', status: 'not-a-status'},
        ],
        edges: [
            {from: 'catalog', to: 'list', kind: 'parent'},
            {from: 'home', to: 'list', kind: 'navigation'},
            {from: 'home', to: 'list', kind: 'journey'},
            {from: 'home', to: 'list', kind: 'auth'},
            {from: 'home', to: 'gone', kind: 'redirect'},
            {from: 'list', to: 'home', kind: 'success'},
            {from: 'list', to: 'gone', kind: 'failure'},
            {from: 'home', to: 'base', kind: 'navigation'},
            {from: 'home', to: 'list', kind: 'squiggle'},
        ],
    }, [{id: 'wf-home', attrs: {route: '/home/'}}]);
    assert.equal(graph.nodes.find(node => node.id === 'base'), undefined);
    assert.equal(graph.scaffolding[0].label, 'base.html');
    assert.equal(graph.nodes.find(node => node.id === 'home').clickable, true);
    assert.equal(graph.nodes.find(node => node.id === 'home').screenId, 'wf-home');
    assert.equal(graph.nodes.find(node => node.id === 'catalog'), undefined);
    assert.equal(graph.nodes.find(node => node.id === 'list'), undefined);
    assert.equal(graph.unmappedCount, 3);
    assert.deepEqual(graph.edges, []);
});

test('layout places linked pages in route order and points edges from page to page', () => {
    const layout = layoutConnectedSitemap({
        nodes: [
            {id: 'home', kind: 'page', label: 'Home', route: '/', status: 'verified'},
            {id: 'list', kind: 'page', label: 'List', route: '/products', status: 'verified'},
        ],
        edges: [{from: 'home', to: 'list', kind: 'navigation', label: 'products'}],
        scaffolding: [],
    }, [
        {id: 'wf-home', attrs: {route: '/'}},
        {id: 'wf-list', attrs: {route: '/products'}},
    ]);
    const home = layout.nodes.find(node => node.id === 'home');
    const list = layout.nodes.find(node => node.id === 'list');
    assert.ok(home.x < list.x);
    assert.equal(layout.edges[0].kind, 'navigation');
    assert.ok(layout.edges[0].x1 < layout.edges[0].x2);
    assert.equal(list.clickable, true);
});

test('pages without an explicit journey inherit a directional URL hierarchy', () => {
    const graph = connectedSitemap({
        nodes: [
            {id: 'home', kind: 'page', label: 'Home', route: '/', status: 'verified'},
            {id: 'account', kind: 'page', label: 'Account', route: '/account', status: 'verified'},
            {id: 'orders', kind: 'page', label: 'Orders', route: '/account/orders', status: 'verified'},
        ],
        edges: [],
    }, [
        {id: 'wf-home', attrs: {route: '/'}},
        {id: 'wf-account', attrs: {route: '/account'}},
        {id: 'wf-orders', attrs: {route: '/account/orders'}},
    ]);
    assert.deepEqual(graph.edges.map(edge => [edge.from, edge.to, edge.kind]), [
        ['home', 'account', 'parent'],
        ['account', 'orders', 'parent'],
    ]);
});

test('duplicate routes prefer the stencil whose label matches the page', () => {
    const graph = connectedSitemap({
        nodes: [{id: 'home', kind: 'page', label: 'Home', route: '/', status: 'verified'}],
        edges: [],
    }, [
        {id: 'wf-overview', label: 'Overview', attrs: {route: '/'}},
        {id: 'wf-home', label: 'Home', attrs: {route: '/'}},
    ]);
    assert.equal(graph.nodes[0].screenId, 'wf-home');
});
