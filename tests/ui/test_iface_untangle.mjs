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
