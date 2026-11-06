import test from 'node:test';
import assert from 'node:assert/strict';
const base = '../../distr/gui/web/static/development/planning/erd/';
const D = await import(new URL(base + 'db-drawer.js', import.meta.url));
const P = await import(new URL(base + 'parser.js', import.meta.url));
const M = await import(new URL(base + 'model.js', import.meta.url));

const diff = {
    added: [{ key: 'public.orders', schema: 'public', table: 'orders', entity: 'public__orders' }],
    changed: [{ key: 'public.customers', schema: 'public', table: 'customers', entity: 'public__customers', changes: [{ column: 'email', change: 'type', from: 'varchar(255)', to: 'text' }] }],
    removed: [{ key: 'public.old', schema: 'public', table: 'old', entity: 'public__old', already_marked: false }, { key: 'public.older', schema: 'public', table: 'older', entity: 'public__older', already_marked: true }],
    unchanged: [{ key: 'audit.log', schema: 'audit', table: 'log', entity: 'audit__log' }],
    conflicts: [{ key: 'public.users', schema: 'public', table: 'users', entity: 'public__users', reason: 'clash' }],
};

test('picker rows: kinds, search by schema/table/entity, conflicts disabled', () => {
    const rows = D.pickerRows(diff);
    assert.deepEqual(rows.map((r) => r.kind), ['added', 'changed', 'removed', 'gone', 'unchanged', 'conflict']);
    assert.equal(rows.find((r) => r.kind === 'conflict').disabled, true);
    assert.deepEqual(D.pickerRows(diff, 'AUDIT').map((r) => r.key), ['audit.log']);
    assert.deepEqual(D.pickerRows(diff, 'public__cust').map((r) => r.key), ['public.customers']);
    assert.deepEqual(D.pickerRows(null), []);
});

test('default selection = added + changed + newly removed, capped at 300', () => {
    assert.deepEqual([...D.defaultSelection(diff)].sort(), ['public.customers', 'public.old', 'public.orders']);
    const big = { ...diff, added: Array.from({ length: 450 }, (_, i) => ({ key: `s.t${i}`, schema: 's', table: `t${i}` })) };
    assert.equal(D.defaultSelection(big).size, D.MAX_APPLY);
    assert.equal(D.MAX_APPLY, 300);
});

test('change descriptions', () => {
    assert.equal(D.describeChange({ column: 'a', change: 'type', from: 'int', to: 'bigint' }), 'a: int → bigint');
    assert.equal(D.describeChange({ column: 'a', change: 'nullable', to: true }), 'a: nullable');
    assert.equal(D.describeChange({ column: 'a', change: 'keys', from: [], to: ['PK'] }), 'a: keys – → PK');
    assert.equal(D.describeChange({ change: 'back_in_db' }), 'back in the database');
});

const tagged = `erDiagram
    public__orders {
        bigint id PK
        decimal total
    }
    HAND {
        int id PK
    }
%% decisions-erd: {"v":1,"db":{"owner":4,"links":[{"link":4,"snapshot_at":"2026-11-03T07:00:00+00:00","relations":[]}]},"tables":{"public__orders":{"x":10,"y":20,"interfaces":[],"fields":{"total":{"nullable":true,"type":"decimal(10,2)"}},"db":{"link":4,"schema":"public","table":"orders","raw":{"id":"int8"}}},"HAND":{"x":300,"y":20,"interfaces":[]}}}
`;

test('canvas round trip keeps the db sidecar keys (top level and per table)', () => {
    const { model } = P.parseErd(tagged);
    const store = M.createStore(model);
    store.moveTable('public__orders', 50, 60);
    store.renameTable('public__orders', 'orders_renamed');
    const text = P.emitErd(store.state);
    const side = JSON.parse(text.trim().split('\n').pop().slice(P.SIDECAR_PREFIX.length));
    assert.equal(side.db.owner, 4);
    assert.equal(side.db.links[0].link, 4);
    assert.deepEqual(side.tables.orders_renamed.db, { link: 4, schema: 'public', table: 'orders', raw: { id: 'int8' } });
    assert.equal(side.tables.orders_renamed.x, 50);
    assert.equal(side.tables.orders_renamed.fields.total.type, 'decimal(10,2)');
    assert.equal(side.tables.HAND.db, undefined);
    assert.equal(P.emitErd(P.parseErd(text).model), text);   // stable
});

test('duplicating a db-tagged table does not copy the db tag', () => {
    const store = M.createStore(P.parseErd(tagged).model);
    const copy = store.duplicateTable('public__orders');
    const side = P.sidecarOf(store.state);
    assert.ok(side.tables.public__orders.db);
    assert.equal(side.tables[copy].db, undefined);
});

test('a diagram without extras emits the same sidecar shape as before', () => {
    const { model } = P.parseErd('erDiagram\n    A {\n        int id PK\n    }\n');
    assert.deepEqual(Object.keys(P.sidecarOf(model)), ['v', 'tables']);
    assert.deepEqual(Object.keys(P.sidecarOf(model).tables.A), ['x', 'y', 'interfaces']);
});
