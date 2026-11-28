import { register } from 'node:module';
import test from 'node:test';
import assert from 'node:assert/strict';

register(new URL('./strip-query-loader.mjs', import.meta.url).href);
const { buildIfaceModel, layoutIface } = await import('../../distr/gui/web/static/development/planning/iface-model.js');
const { decodeEntities } = await import('../../distr/gui/web/static/development/planning/wireframe.js');

const link = (route) => ({ type: 'link', label: route, attrs: { route }, children: [] });
const page = (index, links = []) => ({ id: `p${index}`, label: `Page ${index}`, attrs: { id: `p${index}`, route: `/p${index}` }, children: links.map(link) });

// 60 pages in pairs that link to each other, listed so the default grid puts every pair 30 cards apart.
const screens = Array.from({ length: 60 }, (_, i) => page(i, [`/p${i < 30 ? i + 30 : i - 30}`]));

test('a large map places linked pages next to each other', () => {
    const model = buildIfaceModel({ screens });
    layoutIface(model, { aspect: 1.6 });
    const at = new Map(model.nodes.map(node => [node.key, node]));
    const visible = model.edges.filter(edge => !edge.chrome);
    const mean = visible.reduce((sum, edge) => {
        const a = at.get(edge.from), b = at.get(edge.to);
        return sum + Math.hypot(a.x - b.x, a.y - b.y);
    }, 0) / visible.length;
    assert.ok(mean < 400, `mean link length ${mean}`);
    assert.equal(new Set(model.nodes.map(node => `${node.x},${node.y}`)).size, 60);
});

test('card text decodes HTML entities', () => {
    assert.equal(decodeEntities('You&#x27;re Ready &amp; &times; &bogus;'), "You're Ready & × &bogus;");
});

test('a large clustered map stays wide enough that Fit stays readable', () => {
    const screens = Array.from({ length: 54 }, (_, i) => {
        const group = Math.floor(i / 3);
        const links = [`/p${group * 3}`, `/p${group * 3 + 1}`, `/p${group * 3 + 2}`].filter(route => route !== `/p${i}`);
        return page(i, links);
    });
    const model = buildIfaceModel({ screens });
    const layout = layoutIface(model, { aspect: 1.6 });
    assert.ok(layout.width / layout.height > 1.0, `aspect ${layout.width}x${layout.height}`);
    assert.ok(model.nodes.every(node => node.access), 'every card has an access group');
});

test('a stale pin far outside a large map is ignored, a nearby pin is kept', () => {
    const model = buildIfaceModel({ screens });
    const plain = layoutIface(model, { aspect: 1.6 });
    const model2 = buildIfaceModel({ screens });
    const layout = layoutIface(model2, { aspect: 1.6, pinned: { p0: { x: 40, y: 60 }, p1: { x: 10, y: plain.height + 5000 } } });
    const at = new Map(model2.nodes.map(node => [node.screenKey, node]));
    assert.equal(at.get('p0').x, 40);
    assert.ok(at.get('p1').y < plain.height + 1000, 'stale pin dropped');
    assert.ok(layout.height < plain.height + 1000, `height ${layout.height} vs ${plain.height}`);
});
