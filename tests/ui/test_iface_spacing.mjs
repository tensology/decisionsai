import { register } from 'node:module';
import test from 'node:test';
import assert from 'node:assert/strict';

register(new URL('./strip-query-loader.mjs', import.meta.url).href);
const { buildIfaceModel, layoutIface, NODE } = await import('../../distr/gui/web/static/development/planning/iface-model.js');

const screen = (id, label, route, children = []) => ({ id, label, attrs: { id, route }, children });

test('the map retains navigation back to Home and ignores protocol-relative destinations', () => {
    const link = (label, route) => ({ type: 'link', label, attrs: { route }, children: [] });
    const model = buildIfaceModel({ screens: [
        screen('home', 'Home', '/', [link('Customers', '/customers')]),
        screen('customers', 'Customers', '/customers', [link('Back to Home', '/'), link('External', '//example.com')]),
        screen('external', 'External', '//example.com'),
    ] });
    assert.deepEqual(model.edges.map(({ from, to, label }) => ({ from, to, label })), [
        { from: 'w:home', to: 'w:customers', label: 'Customers' },
        { from: 'w:customers', to: 'w:home', label: 'Back to Home' },
    ]);
    assert.equal(model.nodes.find(node => node.screenKey === 'external').route, '');
});

test('pages split into backend, frontend, ajax, and guidance, and a comment is kept', () => {
    const source = [
        'screen "Orders" id=orders route=/admin/orders layer=backend',
        '  #| Staff fulfil the order here.',
        'screen "Shop" id=shop route=/shop layer=frontend',
        '  link "Load" route=/api/orders',
        'screen "Orders feed" id=feed route=/api/orders layer=ajax',
        'screen "How checkout works" id=guide-checkout layer=guidance',
        '  link "Shop" route=/shop',
    ].join('\n');
    const screen = (id, label, route, layer, children = []) => ({ id, label, attrs: { id, route, layer }, children });
    const model = buildIfaceModel({
        source,
        screens: [
            screen('orders', 'Orders', '/admin/orders', 'backend'),
            screen('shop', 'Shop', '/shop', 'frontend', [{ type: 'link', label: 'Load', attrs: { route: '/api/orders' }, children: [] }]),
            screen('feed', 'Orders feed', '/api/orders', 'ajax'),
            screen('guide-checkout', 'How checkout works', '', 'guidance', [{ type: 'link', label: 'Shop', attrs: { route: '/shop' }, children: [] }]),
        ],
        meta: { 'guide-checkout': { note: 'Payment is taken only after the basket is valid.' } },
    });
    layoutIface(model);
    const byKey = Object.fromEntries(model.nodes.map((node) => [node.screenKey, node]));
    assert.equal(byKey.orders.lane, 'backend');
    assert.equal(byKey.shop.lane, 'frontend');
    assert.equal(byKey.feed.lane, 'ajax');
    assert.equal(byKey['guide-checkout'].lane, 'guidance');
    const guide = model.nodes.find((node) => node.lane === 'guidance');
    assert.equal(guide.note, 'Payment is taken only after the basket is valid.');
    assert.equal(byKey.orders.note, 'Staff fulfil the order here.');
    assert.ok(byKey.orders.y < byKey.shop.y);
    assert.ok(byKey.shop.y < byKey.feed.y);
    assert.ok(byKey.feed.y < guide.y);
    assert.ok(model.edges.some((edge) => edge.from === byKey.shop.key && edge.to === byKey.feed.key));
    assert.ok(model.edges.some((edge) => edge.from === guide.key && edge.to === byKey.shop.key));
});

test('connected pages get a wider gap than an unconnected page', () => {
    const model = buildIfaceModel({
        screens: [
            screen('home', 'Home', '/home', [{ type: 'link', label: 'Shop', attrs: { route: '/shop' }, children: [] }]),
            screen('shop', 'Shop', '/shop'),
            screen('about', 'About', '/about'),
        ],
    });
    const edge = model.edges.find((item) => item.kind === 'link');
    assert.ok(edge, 'wire link becomes an edge');
    layoutIface(model);
    const home = model.nodes.find((node) => node.screenKey === 'home');
    const shop = model.nodes.find((node) => node.screenKey === 'shop');
    const about = model.nodes.find((node) => node.screenKey === 'about');
    assert.equal(shop.x - home.x, NODE.w + NODE.gapX + NODE.linkGap);
    assert.ok(about.y > home.y);
    assert.ok(shop.x - home.x > NODE.w + NODE.gapX);
});

test('an ajax route is a short part, not a page card', () => {
    const model = buildIfaceModel({ screens: [screen('feed', 'Orders feed', '/api/orders')] });
    layoutIface(model);
    const feed = model.nodes[0];
    assert.equal(feed.lane, 'ajax');
    assert.equal(feed.h, NODE.partH);
    assert.ok(feed.h < NODE.h);
});

test('a hub with many outgoing links sits at the center and neighbors sit farther out', () => {
    const link = (label, route) => ({ type: 'link', label, attrs: { route }, children: [] });
    const model = buildIfaceModel({
        screens: [
            screen('overview', 'Overview', '/overview', [
                link('A', '/a'), link('B', '/b'), link('C', '/c'), link('D', '/d'),
            ]),
            screen('a', 'A', '/a', [link('Deep', '/deep')]),
            screen('b', 'B', '/b'),
            screen('c', 'C', '/c'),
            screen('d', 'D', '/d'),
            screen('deep', 'Deep', '/deep'),
        ],
    });
    const layout = layoutIface(model);
    assert.equal(layout.lanes.length, 0);
    const at = (id) => model.nodes.find((node) => node.screenKey === id);
    const hub = at('overview');
    const targets = ['a', 'b', 'c', 'd'].map(at);
    const center = (node) => ({ x: node.x + node.w / 2, y: node.y + node.h / 2 });
    const hubCenter = center(hub);
    const distance = (node) => Math.hypot(center(node).x - hubCenter.x, center(node).y - hubCenter.y);
    const grid = NODE.w + NODE.gapX;
    for (const node of targets) assert.ok(distance(node) > grid);
    assert.ok(distance(at('deep')) > distance(at('a')));
    const minX = Math.min(...targets.map((node) => node.x));
    const maxX = Math.max(...targets.map((node) => node.x + node.w));
    const minY = Math.min(...targets.map((node) => node.y));
    const maxY = Math.max(...targets.map((node) => node.y + node.h));
    assert.ok(hub.x > minX && hub.x + hub.w < maxX);
    assert.ok(hub.y > minY && hub.y + hub.h < maxY);
    assert.ok(targets.some((node) => center(node).x < hubCenter.x));
    assert.ok(targets.some((node) => center(node).x > hubCenter.x));
});
