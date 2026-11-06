// Auto-layout: connected components (by relations), each placed as a BFS-ordered grid,
// components packed in shelves. Boxes never overlap by construction.
export const TABLE_WIDTH = 220;
export const HEADER_H = 32;
export const ROW_H = 22;
export const tableHeight = (t) => HEADER_H + Math.max(1, t.fields.length + (t.raw?.length ? 1 : 0)) * ROW_H + 26;

export function components(model) {
    const adj = new Map(model.tables.map((t) => [t.name, new Set()]));
    for (const r of model.relations) { adj.get(r.from)?.add(r.to); adj.get(r.to)?.add(r.from); }
    const seen = new Set();
    const groups = [];
    for (const t of [...model.tables].sort((a, b) => adj.get(b.name).size - adj.get(a.name).size)) {
        if (seen.has(t.name)) continue;
        const order = [];
        const queue = [t.name];
        seen.add(t.name);
        while (queue.length) {
            const name = queue.shift();
            order.push(name);
            for (const next of [...adj.get(name)].sort()) if (!seen.has(next)) { seen.add(next); queue.push(next); }
        }
        groups.push(order);
    }
    return groups.sort((a, b) => b.length - a.length);
}

// Lay out every table (fresh diagram). Each connected group is a BFS-ordered grid; groups are packed
// in shelves whose width is chosen so the whole bounding box matches the canvas aspect (width/height),
// which maximises the fit-to-view scale. Boxes never overlap by construction.
// Lay out every table (fresh diagram). Each connected group becomes a compact masonry block (BFS order,
// each table goes to the shortest column, so linked tables sit near each other); unrelated single tables
// share one block. Blocks are packed in shelves, and the block column counts + shelf width are chosen to
// make the whole bounding box match the canvas aspect (width / height), which maximises fit-to-view.
// Boxes never overlap by construction.
export function autoLayout(model, { gapX = 48, gapY = 36, aspect = 1.6 } = {}) {
    const byName = new Map(model.tables.map((t) => [t.name, t]));
    const groups = components(model);
    const singles = groups.filter((g) => g.length === 1).flat();
    const linked = groups.filter((g) => g.length > 1);
    const sets = singles.length ? [...linked, singles] : linked;
    let best = null;
    for (const shape of [0.6, 0.8, 1, 1.3, 1.7, 2.2]) {
        const blocks = sets.map((names) => masonryBlock(names.map((name) => byName.get(name)), { gapX, gapY, aspect: aspect * shape }));
        const widths = new Set();
        let acc = 0;
        for (const b of blocks) { acc += b.w + gapX * 2; widths.add(acc); }
        const maxBlock = Math.max(...blocks.map((b) => b.w), 1);
        for (let w = maxBlock; w <= acc; w += TABLE_WIDTH + gapX) widths.add(w);
        for (const maxWidth of widths) {
            if (maxWidth < maxBlock) continue;
            const placed = shelfPack(blocks, maxWidth, gapX, gapY);
            const cost = Math.max(placed.w / aspect, placed.h);   // fit scale is proportional to 1 / cost
            if (!best || cost < best.cost - 1e-6) best = { cost, placed };
        }
    }
    for (const { block, x, y } of best?.placed.at || []) for (const c of block.cells) { c.table.x = x + c.dx; c.table.y = y + c.dy; }
    return model;
}

function masonryBlock(tables, { gapX, gapY, aspect }) {
    const n = tables.length;
    const avgH = tables.reduce((sum, t) => sum + tableHeight(t), 0) / Math.max(1, n) + gapY;
    const cols = Math.max(1, Math.min(n, Math.round(Math.sqrt((n * aspect * avgH) / (TABLE_WIDTH + gapX)))));
    const heights = new Array(cols).fill(0);
    const cells = tables.map((table) => {
        const c = heights.indexOf(Math.min(...heights));
        const cell = { table, dx: c * (TABLE_WIDTH + gapX), dy: heights[c] };
        heights[c] += tableHeight(table) + gapY;
        return cell;
    });
    return { cells, w: cols * (TABLE_WIDTH + gapX) - gapX, h: Math.max(...heights) - gapY };
}

function shelfPack(blocks, maxWidth, gapX, gapY) {
    const at = [];
    let x = 0, y = 0, shelfH = 0, w = 0;
    for (const block of blocks) {
        if (x > 0 && x + block.w > maxWidth) { x = 0; y += shelfH + gapY * 2; shelfH = 0; }
        at.push({ block, x, y });
        w = Math.max(w, x + block.w);
        shelfH = Math.max(shelfH, block.h);
        x += block.w + gapX * 2;
    }
    return { at, w, h: y + shelfH };
}

export function needsLayout(model) { return model.tables.some((t) => !Number.isFinite(t.x) || !Number.isFinite(t.y)); }

// Place tables without a position (new ones from an agent edit) below everything else.
// Saved positions are never moved.
export function placeMissing(model, { aspect = 1.6 } = {}) {
    const placed = model.tables.filter((t) => Number.isFinite(t.x) && Number.isFinite(t.y));
    if (!placed.length) return autoLayout(model, { aspect });
    const left = Math.min(...placed.map((t) => t.x));
    const right = Math.max(...placed.map((t) => t.x + TABLE_WIDTH));
    let y = Math.max(...placed.map((t) => t.y + tableHeight(t))) + 60;
    let x = left, rowH = 0;
    for (const t of model.tables) if (!Number.isFinite(t.x) || !Number.isFinite(t.y)) {
        if (x > left && x + TABLE_WIDTH > Math.max(right, left + 4 * (TABLE_WIDTH + 60))) { x = left; y += rowH + 50; rowH = 0; }
        t.x = x; t.y = y; x += TABLE_WIDTH + 60; rowH = Math.max(rowH, tableHeight(t));
    }
    return model;
}

export function bounds(model) {
    if (!model.tables.length) return { x: 0, y: 0, w: 1, h: 1 };
    const xs = model.tables.map((t) => t.x), ys = model.tables.map((t) => t.y);
    const x = Math.min(...xs), y = Math.min(...ys);
    const r = Math.max(...model.tables.map((t) => t.x + TABLE_WIDTH));
    const b = Math.max(...model.tables.map((t) => t.y + tableHeight(t)));
    return { x, y, w: r - x, h: b - y };
}

export function overlaps(model) {
    let n = 0;
    const t = model.tables;
    for (let i = 0; i < t.length; i++) for (let j = i + 1; j < t.length; j++) {
        const a = t[i], b = t[j];
        if (a.x < b.x + TABLE_WIDTH && b.x < a.x + TABLE_WIDTH && a.y < b.y + tableHeight(b) && b.y < a.y + tableHeight(a)) n++;
    }
    return n;
}
