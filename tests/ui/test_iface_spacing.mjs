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

test('a large discovery uses compact lanes even when several pages link to the same route', () => {
    const screens = Array.from({ length: 90 }, (_, index) => ({
        id: `page-${index}`, label: `Page ${index}`, attrs: { id: `page-${index}`, route: `/page-${index}` },
        children: index === 0 ? [1, 2, 3, 4].map(target => ({
            type: 'link', label: `Page ${target}`, attrs: { route: `/page-${target}` }, children: [],
        })) : [],
    }));
    const model = buildIfaceModel({ screens });
    const layout = layoutIface(model, { aspect: 1 });
    assert.equal(layout.lanes[0].count, 90);
    assert.ok(layout.width / layout.height > 0.75 && layout.width / layout.height < 1.25);
    assert.ok(model.nodes.every(node => node.x + node.w <= layout.width && node.y + node.h <= layout.height));
});

test('a large single lane fills a wide canvas without becoming a tall column', () => {
    const model = buildIfaceModel({ screens: Array.from({ length: 158 }, (_, index) =>
        screen(`page-${index}`, `Page ${index}`, `/page-${index}`)) });
    const layout = layoutIface(model, { aspect: 1.6 });
    assert.ok(layout.cols > 8);
    assert.ok(layout.width / layout.height > 1.2);
    assert.equal(new Set(model.nodes.map(node => `${node.x},${node.y}`)).size, 158);
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
    assert.equal(feed.kind, 'endpoint');
    assert.equal(feed.h, NODE.quietH);
    assert.ok(feed.h < NODE.h);
});

test('JSON and file endpoints are compact URL cards and are not counted as pages', () => {
    const model = buildIfaceModel({
        screens: [
            screen('home', 'Home', '/'),
            screen('publish', 'Translation.translate publish', '/publish'),
            screen('files', 'Translation file list redirect', '/files'),
            screen('robots', 'Robots txt', '/robots.txt'),
            screen('lookup', 'Perk card lookup', '/perk-card/lookup'),
        ],
    });
    layoutIface(model);
    const byKey = Object.fromEntries(model.nodes.map((node) => [node.screenKey, node]));
    assert.equal(byKey.home.kind, 'page');
    for (const id of ['publish', 'files', 'robots', 'lookup']) {
        assert.equal(byKey[id].kind, 'endpoint', id);
        assert.equal(byKey[id].lane, 'ajax', id);
        assert.equal(byKey[id].h, NODE.quietH, id);
    }
    assert.equal(model.outlined, 1);
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


test('shared navigation and an overview map are marked as chrome links', () => {
    const link = (label, route) => ({ type: 'link', label, attrs: { route }, children: [] });
    const overviewLinks = Array.from({ length: 8 }, (_, index) => link(`Page ${index}`, `/p${index}`));
    const screens = [
        screen('overview', 'Overview · Shop', '', overviewLinks),
        screen('home', 'Home', '/', [link('Shop', '/shop')]),
        screen('shop', 'Shop', '/shop'),
        ...Array.from({ length: 8 }, (_, index) => screen(`p${index}`, `Page ${index}`, `/p${index}`, [
            link('Home', '/'),
        ])),
    ];
    const model = buildIfaceModel({ screens });
    assert.ok(!model.nodes.some((node) => /^overview/i.test(node.label)));
    const chrome = model.edges.filter((edge) => edge.chrome);
    assert.ok(chrome.length >= 8, 'shared-nav edges are chrome');
});

test('pinned pages keep their coordinates while unpinned pages are still placed', () => {
    const model = buildIfaceModel({ screens: [
        screen('home', 'Home', '/home'),
        screen('shop', 'Shop', '/shop'),
        screen('about', 'About', '/about'),
    ] });
    layoutIface(model, { pinned: { home: { x: 900, y: 40 } } });
    const home = model.nodes.find((node) => node.screenKey === 'home');
    const shop = model.nodes.find((node) => node.screenKey === 'shop');
    assert.equal(home.x, 900);
    assert.equal(home.y, 40);
    assert.equal(home.pinned, true);
    assert.notEqual(shop.x, 900);
});

test('an Overview journey-map does not take / or bind to Home, and checkout clusters on URL links', () => {
    const link = (label, route) => ({ type: 'link', label, attrs: { route }, children: [] });
    const screens = [
        screen('overview', 'Overview · Merrypak', '/', [
            link('Home', '/'),
            link('Checkout', '/shop/checkout'),
            link('Gateway failed', '/shop/checkout/gateway/failed'),
        ]),
        screen('home', 'Home', '/', [link('Shop', '/shop')]),
        { id: 'wf-checkout', label: 'Checkout', attrs: { route: '/shop/checkout' }, children: [link('Gateway', '/shop/checkout/gateway')] },
        { id: 'wf-gateway', label: 'Checkout gateway', attrs: { route: '/shop/checkout/gateway' }, children: [] },
        { id: 'wf-failed', label: 'Checkout gateway failed', attrs: { route: '/shop/checkout/gateway/failed' }, children: [] },
        { id: 'wf-basket', label: 'Basket', attrs: { route: '/shop/basket' }, children: [] },
        ...Array.from({ length: 40 }, (_, i) => ({ id: `wf-p${i}`, label: `Page ${i}`, attrs: { route: `/other/page-${i}` }, children: [] })),
    ];
    const model = buildIfaceModel({ screens });
    layoutIface(model);
    const overview = model.nodes.find((node) => /^overview/i.test(node.label));
    const home = model.nodes.find((node) => node.label === 'Home');
    const failed = model.nodes.find((node) => node.label === 'Checkout gateway failed');
    const gateway = model.nodes.find((node) => node.label === 'Checkout gateway');
    const checkout = model.nodes.find((node) => node.label === 'Checkout');
    assert.ok(!overview, 'journey-map overview is hidden from the map');
    assert.ok(home && failed && gateway && checkout);
    assert.equal(home.route, '/');
    assert.ok(model.hidden?.some((row) => /^overview/i.test(row.label) && row.reason === 'journey map'));
    assert.ok(failed.screenKey);
    assert.ok(model.edges.some((edge) => edge.from === checkout.key && edge.to === gateway.key && !edge.chrome));
    assert.ok(model.edges.some((edge) => edge.from === gateway.key && edge.to === failed.key && !edge.chrome));
    assert.ok(!model.edges.some((edge) => /overview/i.test(String(edge.from)) || /overview/i.test(String(edge.to))));
    assert.ok(Math.abs(gateway.y - failed.y) < 40 || Math.abs(gateway.x - checkout.x) < NODE.w + NODE.gapX + NODE.linkGap * 2);
});
