import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const base = '../../distr/gui/web/static/development/planning/erd/';
const P = await import(new URL(base + 'parser.js', import.meta.url));
const L = await import(new URL(base + 'layout.js', import.meta.url));
const M = await import(new URL(base + 'model.js', import.meta.url));
const G = await import(new URL(base + 'geometry.js', import.meta.url));
const fixture = await readFile(new URL('./fixtures/erd_merrypak.mmd', import.meta.url), 'utf8');
const strip = (m) => JSON.parse(JSON.stringify({ tables: m.tables.map(({ x, y, ...t }) => t), relations: m.relations, preserved: m.preserved }));

test('Merrypak-sized fixture parses: 51 entities, 30 relations, keys/comments/aliases', () => {
    const { model, warnings } = P.parseErd(fixture);
    assert.equal(model.tables.length, 51);
    assert.equal(model.relations.length, 30);
    const customer = model.tables.find((t) => t.name === 'CUSTOMER');
    assert.equal(customer.alias, 'Customer');
    assert.deepEqual(customer.fields[0], { type: 'int', name: 'id', keys: ['PK'], comment: 'surrogate key' });
    assert.ok(model.tables.some((t) => t.fields.some((f) => f.keys.join() === 'PK,FK')));
    assert.ok(model.relations.some((r) => r.label === 'has many') && model.relations.some((r) => r.identifying === false));
    assert.ok(model.tables.every((t) => Array.isArray(t.interfaces) && t.interfaces.length === 0));   // phase-2 shape
    // unrepresentable lines are preserved and reported, never dropped
    assert.ok(model.preserved.includes('direction LR') && model.preserved.includes('style CUSTOMER fill:#f97316') && model.preserved.includes('classDef hot fill:#f00'));
    assert.ok(model.preserved.includes('%% Merrypak ERD (fixture, 51 entities)') && model.preserved.includes('%% trailing comment'));
    assert.ok(model.tables.some((t) => t.raw.includes("weird line ??? that mermaid parser here can't map")));
    assert.ok(model.tables.some((t) => t.raw.includes('%% inline comment inside a block')));
    assert.ok(warnings.length >= 4, warnings.join('\n'));
});

test('round trip: parse -> mermaid -> parse is identical, and stable on a second pass', () => {
    const first = P.parseErd(fixture).model;
    L.autoLayout(first);
    const text = P.emitErd(first);
    const second = P.parseErd(text);
    assert.deepEqual(strip(second.model), strip(first));
    assert.deepEqual(second.model.tables.map((t) => [t.x, t.y]), first.tables.map((t) => [t.x, t.y]));   // layout via sidecar
    assert.equal(P.emitErd(second.model), text);
    assert.ok(P.isErdMermaid(text) && second.hasLayout);
    for (const raw of ['style CUSTOMER fill:#f97316', 'classDef hot fill:#f00', "weird line ??? that mermaid parser here can't map"]) assert.ok(text.includes(raw), raw);
});

test('sidecar: one trailing %% line with view, positions, interfaces[] and field extras', () => {
    const { model } = P.parseErd('erDiagram\n    A {\n        int id PK\n    }\n');
    L.autoLayout(model);
    model.view = { zoom: 0.5, x: 10, y: 20 };
    model.tables[0].fields[0].nullable = false;
    model.tables[0].fields[0].default = '0';
    model.tables[0].interfaces = ['/checkout'];
    const text = P.emitErd(model);
    const line = text.trim().split('\n').at(-1);
    assert.ok(line.startsWith(P.SIDECAR_PREFIX));
    assert.deepEqual(JSON.parse(line.slice(P.SIDECAR_PREFIX.length)), { v: 1, view: { zoom: 0.5, x: 10, y: 20 }, tables: { A: { x: 0, y: 0, interfaces: ['/checkout'], fields: { id: { nullable: false, default: '0' } } } } });
    const again = P.parseErd(text).model;
    assert.equal(again.tables[0].fields[0].default, '0');
    assert.deepEqual(again.tables[0].interfaces, ['/checkout']);
    // a broken sidecar is ignored with a warning, diagram still parses
    const broken = P.parseErd('erDiagram\n    A {\n    }\n%% decisions-erd: {nope');
    assert.equal(broken.model.tables.length, 1);
    assert.ok(broken.warnings.some((w) => /layout/.test(w)));
});

test('auto-layout: no overlapping boxes for the 51-table fixture, components grouped', () => {
    const { model } = P.parseErd(fixture);
    L.autoLayout(model);
    assert.equal(L.overlaps(model), 0);
    assert.ok(model.tables.every((t) => Number.isFinite(t.x) && Number.isFinite(t.y)));
    const comps = L.components(model);
    assert.equal(comps.flat().length, 51);
    const b = L.bounds(model);
    assert.ok(b.w / b.h > 1 && b.w / b.h < 2.6, `default 1.6 aspect: ${b.w}x${b.h}`);   // was a fixed 2600px shelf width
});

test('model commands + undo/redo', () => {
    const store = M.createStore(P.parseErd('erDiagram\n    A {\n        int id PK\n    }\n    B {\n    }\n    A ||--o{ B : has\n').model);
    const name = store.addTable('Order Line', 10, 20);
    assert.equal(name, 'Order_Line');
    assert.equal(store.addField(name, { name: 'qty', type: 'int' }), 'qty');
    store.updateField(name, 1, { keys: ['FK'], comment: 'say "hi"' });
    assert.equal(store.state.tables.find((t) => t.name === name).fields[1].comment, "say 'hi'");
    store.moveField(name, 1, -1);
    assert.equal(store.state.tables.find((t) => t.name === name).fields[0].name, 'qty');
    assert.equal(store.renameTable('A', 'ACCOUNT'), true);
    assert.equal(store.state.relations[0].from, 'ACCOUNT');
    assert.equal(store.renameTable('ACCOUNT', 'B'), false);            // no duplicate names
    const idx = store.addRelation('ACCOUNT', name, '|o', '|{', 'owns');
    store.updateRelation(idx, { left: '}o', label: 'many' });
    const copy = store.duplicateTable('ACCOUNT');
    assert.equal(copy, 'ACCOUNT_copy');
    store.deleteTable('ACCOUNT');
    assert.equal(store.state.relations.length, 0);                        // relations of a deleted table go too
    const snapshot = JSON.stringify(store.state);
    store.undo();
    assert.equal(store.state.tables.some((t) => t.name === 'ACCOUNT'), true);
    assert.equal(store.state.relations.length, 2);
    store.redo();
    assert.equal(JSON.stringify(store.state), snapshot);
    store.deleteField(name, 0);
    store.deleteRelation(0);
    let n = 0; while (store.undo()) n++;
    assert.equal(n, 10);   // deleteRelation(0) was a no-op (no relations left), so not on the stack
    assert.deepEqual(store.state.tables.map((t) => t.name), ['A', 'B']);
    const text = P.emitErd(store.state);
    assert.match(text, /A \|\|--o\{ B : "has"/);
});

test('emitted mermaid quotes odd names/labels; crow\'s-foot geometry markers', () => {
    const m = P.emptyModel();
    m.tables.push({ name: 'Line Item', alias: '', fields: [], raw: [], x: 0, y: 0, interfaces: [] }, { name: 'B', alias: '', fields: [], raw: [], x: 400, y: 0, interfaces: [] });
    m.relations.push({ from: 'Line Item', to: 'B', left: '}|', right: 'o|', identifying: false, label: 'a "b"' });
    const text = P.emitErd(m, { sidecar: false });
    assert.match(text, /"Line Item" \}\|\.\.o\| B : "a 'b'"/);
    assert.equal(P.parseErd(text).model.relations[0].from, 'Line Item');
    const g = G.relationGeometry(m.tables[0], m.tables[1]);
    assert.deepEqual(g.d1, [1, 0]);
    assert.match(G.markerPath('}|').d, /M0,-7 L12,0/);          // crow's foot (many)
    assert.equal(G.markerPath('o|').circle.r, 4);               // zero
});

test('#5 trailing %% comment on a relation is kept as a comment, label stays clean', () => {
    const { model } = P.parseErd('erDiagram\n    A ||--o{ B : "x" %% c\n    C ||--|| D : plain %% note "q"\n    E ||--|| F : "a %% not a comment"\n');
    assert.deepEqual(model.relations.map((r) => r.label), ['x', 'plain', 'a %% not a comment']);
    assert.ok(model.preserved.includes('%% c') && model.preserved.includes('%% note "q"'));
    const again = P.parseErd(P.emitErd(model)).model;
    assert.deepEqual(again.relations.map((r) => r.label), ['x', 'plain', 'a %% not a comment']);
    // only unquote when the label both starts and ends with a quote
    assert.equal(P.parseErd('erDiagram\n    A ||--o{ B : "half\n').model.relations[0].label, '"half');
});

test('#6 an unclosed { block does not swallow the following relations / tables', () => {
    const { model, warnings } = P.parseErd('erDiagram\n    A {\n        int id PK\n    A ||--o{ B : has\n    B {\n        string name\n    }\n');
    assert.equal(model.relations.length, 1);
    assert.deepEqual(model.tables.find((t) => t.name === 'A').raw, []);
    assert.deepEqual(model.tables.find((t) => t.name === 'B').fields.map((f) => f.name), ['name']);
    assert.ok(warnings.some((w) => /not closed/.test(w)));
});

test('#8 newlines in labels, aliases, comments and names never inject diagram lines', () => {
    const m = P.emptyModel();
    m.tables.push({ name: 'A\nB ||--|| C : x', alias: 'al\nias', fields: [{ name: 'id', type: 'int', keys: ['PK', 'FK'], comment: 'c\r\nd\u2028e' }], raw: [], x: 0, y: 0, interfaces: [] },
        { name: 'B', alias: '', fields: [], raw: [], x: 300, y: 0, interfaces: [] });
    m.relations.push({ from: 'B', to: 'B', left: '||', right: 'o{', identifying: true, label: 'one\ntwo\u2029three' });
    const text = P.emitErd(m, { sidecar: false });
    const reparsed = P.parseErd(text);
    assert.equal(reparsed.model.relations.length, 1);
    assert.equal(reparsed.model.relations[0].label, 'one two three');
    assert.equal(reparsed.model.tables.length, 2);
    assert.match(text, /int id PK, FK "c d e"/);                 // keys keep the `PK, FK` style
    assert.ok(!/\n\s*B \|\|--\|\| C/.test(text));
});

test('P3 preserved lines keep their position (before header, direction, mid-diagram comments)', () => {
    const src = '%% top comment\nerDiagram\n    direction LR\n    A {\n        int id PK\n    }\n    %% about B\n    B {\n    }\n    A ||--o{ B : "has"\n    %% after the relation\n    style A fill:#f00\n';
    const out = P.emitErd(P.parseErd(src).model, { sidecar: false });
    assert.equal(out, src.replace('    B {\n    }', '    B {\n    }'));
    assert.deepEqual(out.split('\n').slice(0, 3), ['%% top comment', 'erDiagram', '    direction LR']);
});

test('Part C: aspect-aware auto layout of the 51-table fixture (no overlaps, groups kept, wider fit)', () => {
    for (const aspect of [16 / 9, 4 / 3, 1.25]) {
        const { model } = P.parseErd(fixture);
        L.autoLayout(model, { aspect });
        assert.equal(L.overlaps(model), 0);
        const b = L.bounds(model);
        assert.ok(b.w / b.h > aspect * 0.6 && b.w / b.h < aspect * 1.7, `aspect ${aspect}: ${b.w}x${b.h}`);
        // connected tables stay together: every relation spans less than half the diagram
        for (const r of model.relations) {
            const a = model.tables.find((t) => t.name === r.from), c = model.tables.find((t) => t.name === r.to);
            assert.ok(Math.hypot(a.x - c.x, a.y - c.y) < Math.max(b.w, b.h) * 0.75);
        }
    }
    // existing positions are never moved; only new tables get placed
    const { model } = P.parseErd(fixture);
    L.autoLayout(model, { aspect: 1.6 });
    const before = model.tables.map((t) => [t.x, t.y]);
    model.tables.push({ name: 'NEW1', alias: '', fields: [], raw: [], x: null, y: null, interfaces: [] });
    L.placeMissing(model, { aspect: 1.6 });
    assert.deepEqual(model.tables.slice(0, -1).map((t) => [t.x, t.y]), before);
    assert.equal(L.overlaps(model), 0);
});

const T = await import(new URL(base + 'field-types.js', import.meta.url));
test('field types: one shared list, split/join, custom types kept verbatim', () => {
    for (const t of ['int', 'bigint', 'varchar', 'text', 'boolean', 'date', 'timestamp', 'uuid', 'json', 'decimal']) assert.ok(T.FIELD_TYPES.includes(t), t);
    assert.equal(new Set(T.FIELD_TYPES).size, T.FIELD_TYPES.length);
    assert.deepEqual(T.splitType('varchar(255)'), { base: 'varchar', raw: 'varchar', param: '255', known: true, value: 'varchar(255)' });
    assert.equal(T.splitType('decimal(12,2)').param, '12,2');
    assert.equal(T.splitType('VARCHAR2(30)').known, false);
    assert.equal(T.splitType('int(11)').known, false);            // int takes no parameter in the list -> Custom
    assert.equal(T.joinType('varchar', '100'), 'varchar(100)');
    assert.equal(T.joinType('int', '9'), 'int');
    assert.equal(T.mermaidType('decimal(12,2)'), 'decimal');
    assert.equal(T.mermaidType('varchar(255)'), 'varchar(255)');
});
test('field types: decimal(12,2) round-trips exactly via the sidecar and the Mermaid stays a single safe token', () => {
    const src = 'erDiagram\n    ORDER {\n        decimal(12,2) total\n        varchar(255) name\n        VARCHAR2(30) code\n        double ratio\n    }\n';
    const { model } = P.parseErd(src);
    assert.deepEqual(model.tables[0].fields.map((f) => f.type), ['decimal(12,2)', 'varchar(255)', 'VARCHAR2(30)', 'double']);
    model.tables[0].fields[3].type = 'double precision';
    const out = P.emitErd(model);
    const body = out.split('\n').filter((l) => !l.startsWith('%%'));
    assert.ok(body.includes('        decimal total'));
    assert.ok(body.includes('        varchar(255) name'));
    assert.ok(body.includes('        double_precision ratio'));
    for (const l of body.filter((l) => /^ {8}\S/.test(l))) assert.match(l.trim().split(' ')[0], T.MERMAID_TYPE_RE);
    const again = P.parseErd(out).model.tables[0].fields.map((f) => f.type);
    assert.deepEqual(again, ['decimal(12,2)', 'varchar(255)', 'VARCHAR2(30)', 'double precision']);
    assert.equal(P.emitErd(P.parseErd(out).model), out);
});

test('addedDbColumns only lists a new field on a linked table', () => {
    const side = { v: 1, tables: { ORDERS: { x: 0, y: 0, interfaces: [], db: { link: 4, schema: 'public', table: 'orders' } } } };
    const doc = (body) => `erDiagram\n    ORDERS {\n${body}\n    }\n%% decisions-erd: ${JSON.stringify(side)}\n`;
    const before = doc('        int id PK');
    const added = doc('        int id PK\n        text note');
    assert.deepEqual(P.addedDbColumns(before, added), [{ link_id: 4, schema: 'public', table: 'orders', column: 'note', field_type: 'text' }]);
    const renamed = doc('        int key PK\n        text note');
    assert.deepEqual(P.addedDbColumns(added, renamed), []);
    const plain = (body) => `erDiagram\n    NEW {\n${body}\n    }\n%% decisions-erd: ${JSON.stringify({ v: 1, tables: { NEW: { x: 0, y: 0, interfaces: [] } } })}\n`;
    assert.deepEqual(P.addedDbColumns(plain('        int id PK'), plain('        int id PK\n        text note')), []);
});
