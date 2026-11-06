// Editor commands over the parsed model, with an undo/redo stack of snapshots.
const clone = (v) => JSON.parse(JSON.stringify(v));
export const validName = (s) => String(s || '').trim().replace(/\s+/g, '_').replace(/[^\w-]/g, '');

export function createStore(model, { limit = 100 } = {}) {
    let state = clone(model);
    const undoStack = [], redoStack = [];
    const listeners = new Set();
    const emit = (kind) => listeners.forEach((fn) => fn(state, kind));
    const table = (name) => state.tables.find((t) => t.name === name);
    const uniqueName = (base) => { let name = base, i = 2; while (table(name)) name = `${base}_${i++}`; return name; };
    function commit(fn) {
        const before = clone(state);
        const result = fn(state);
        if (result === false) return false;
        undoStack.push(before);
        if (undoStack.length > limit) undoStack.shift();
        redoStack.length = 0;
        emit('change');
        return result === undefined ? true : result;
    }
    const api = {
        get state() { return state; },
        subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
        canUndo: () => undoStack.length > 0,
        canRedo: () => redoStack.length > 0,
        undo() { if (!undoStack.length) return false; redoStack.push(clone(state)); state = undoStack.pop(); emit('change'); return true; },
        redo() { if (!redoStack.length) return false; undoStack.push(clone(state)); state = redoStack.pop(); emit('change'); return true; },
        replace(next) { state = clone(next); undoStack.length = 0; redoStack.length = 0; emit('replace'); },
        setView(view) { state.view = view; },  // zoom/pan: not an undoable edit
        addTable(name = 'NEW_TABLE', x = 0, y = 0) {
            return commit((s) => { const n = uniqueName(validName(name) || 'NEW_TABLE'); s.tables.push({ name: n, alias: '', fields: [{ name: 'id', type: 'int', keys: ['PK'], comment: '' }], raw: [], x, y, interfaces: [] }); return n; });
        },
        renameTable(from, to) {
            const clean = validName(to);
            if (!clean || clean === from || table(clean) || !table(from)) return false;
            return commit((s) => {
                s.tables.find((t) => t.name === from).name = clean;
                s.relations.forEach((r) => { if (r.from === from) r.from = clean; if (r.to === from) r.to = clean; });
            });
        },
        moveTable(name, x, y) { return commit((s) => { const t = s.tables.find((t) => t.name === name); if (!t) return false; t.x = Math.round(x); t.y = Math.round(y); }); },
        deleteTable(name) {
            return commit((s) => {
                const i = s.tables.findIndex((t) => t.name === name);
                if (i < 0) return false;
                s.tables.splice(i, 1);
                s.relations = s.relations.filter((r) => r.from !== name && r.to !== name);
            });
        },
        duplicateTable(name) {
            const src = table(name);
            if (!src) return false;
            return commit((s) => { const copy = clone(src); copy.name = uniqueName(`${src.name}_copy`); copy.x += 40; copy.y += 40; copy.interfaces = []; if (copy.sidecarExtra) delete copy.sidecarExtra.db; s.tables.push(copy); return copy.name; });
        },
        addField(tableName, field = {}) {
            return commit((s) => {
                const t = s.tables.find((t) => t.name === tableName);
                if (!t) return false;
                let name = validName(field.name || 'field') || 'field', i = 2;
                while (t.fields.some((f) => f.name === name)) name = `${validName(field.name || 'field') || 'field'}_${i++}`;
                t.fields.push({ name, type: field.type || 'string', keys: field.keys || [], comment: field.comment || '' });
                return name;
            });
        },
        updateField(tableName, index, patch) {
            return commit((s) => {
                const f = s.tables.find((t) => t.name === tableName)?.fields[index];
                if (!f) return false;
                if ('name' in patch) { const n = validName(patch.name); if (!n) return false; patch = { ...patch, name: n }; }
                if ('type' in patch) patch = { ...patch, type: String(patch.type).trim().replace(/\s+/g, '') || 'string' };
                if ('comment' in patch) patch = { ...patch, comment: String(patch.comment).replace(/"/g, "'") };
                Object.assign(f, patch);
                if (patch.nullable === undefined && 'nullable' in patch) delete f.nullable;
            });
        },
        moveField(tableName, index, delta) {
            return commit((s) => {
                const fields = s.tables.find((t) => t.name === tableName)?.fields;
                const to = index + delta;
                if (!fields || to < 0 || to >= fields.length) return false;
                const [f] = fields.splice(index, 1);
                fields.splice(to, 0, f);
            });
        },
        deleteField(tableName, index) {
            return commit((s) => { const t = s.tables.find((t) => t.name === tableName); if (!t?.fields[index]) return false; t.fields.splice(index, 1); });
        },
        addRelation(from, to, left = '||', right = 'o{', label = 'has') {
            if (!table(from) || !table(to)) return false;
            return commit((s) => { s.relations.push({ from, to, left, right, identifying: true, label }); return s.relations.length - 1; });
        },
        updateRelation(index, patch) { return commit((s) => { if (!s.relations[index]) return false; Object.assign(s.relations[index], patch); }); },
        deleteRelation(index) { return commit((s) => { if (!s.relations[index]) return false; s.relations.splice(index, 1); }); },
    };
    return api;
}
