// Interfaces canvas model (pure, no DOM; node-testable): turns the cached HTML sitemap plus the wire item's
// screens into canvas nodes in four bands (Backend, Frontend, Ajax, Guidance). A screen sets `layer=`.
// When it does not, the route decides. Edges come from real flow data, sidecar links, and wire `link` /
// `button` routes. A page with many outgoing links sits at the center of the whole map and the other
// pages sit on rings by hop. Otherwise each layer keeps its own band. A page comment is the sidecar
// note, or the first `#|` line under that screen. Nothing here invents links or writes content.
import { connectedSitemap } from './sitemap.js?v=20261028-responsive';
import { hideJunkPages } from './page-filter.js?v=20261112-skip';

export const NODE = { w: 200, h: 168, guideH: 96, partH: 120, quietH: 56, gapX: 64, gapY: 56, laneGap: 88, laneHead: 34, linkGap: 48 };
export const LANES = [
    { id: 'backend', label: 'Backend' },
    { id: 'frontend', label: 'Frontend' },
    { id: 'ajax', label: 'Ajax' },
    { id: 'guidance', label: 'Guidance' },
];
const LAYERS = new Set(LANES.map((lane) => lane.id));

const API = /\.(xml|txt|json|csv|ics|pdf)$|^\/(api|media|static|assets|feeds?)(\/|$)|:path\b/i;
const EXIT = /(^|\/)(logout|signout|sign-out|redirect|send-again|go)(\/|$)/i;
const FORM = /(register|signup|sign-up|login|log-in|password|activate|activation|email|reset|confirm|validate|lookup|receiver|submit|upload|delete|search|contact|checkout|apply|subscribe)/i;

// page | rectangle (form / auth) | circle (API, media, static) | oval (redirect / exit / not-found)
export function defaultShape(node = {}, shot = null) {
    const route = String(node.route || '');
    if (API.test(route)) return 'circle';
    if (shot?.notFound || EXIT.test(route)) return 'oval';
    if (FORM.test(route) || FORM.test(String(node.view || ''))) return 'rectangle';
    return 'page';
}
export const SHAPE_KIND = { page: 'Page', rectangle: 'Form / account', circle: 'API, media or static file', oval: 'Redirect or exit', square: 'Square', diamond: 'Decision' };

const routeKey = (value) => {
    if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')) return '';
    const path = value.split(/[?#]/)[0];
    return path === '/' ? '/' : (path.replace(/\/+$/, '') || '/');
};

// Overview journey-map cards must not own "/" or bind to Home. A real page named
// "Overview" at /overview stays a page; "Overview · …" or route / is guidance.
export function isJourneyMap(label, route = '', layer = '') {
    if (!/^overview\b/i.test(String(label || ''))) return false;
    const r = String(route || '');
    const ly = String(layer || '').toLowerCase();
    if (ly === 'guidance' || r === '' || r === '/') return true;
    return /[·]|journey\s*map/i.test(String(label || ''));
}

function screenSlug(label, route, taken) {
    const raw = String(label || route || 'page').normalize('NFKD').replace(/[^\w\s-]/g, ' ').trim().toLowerCase().replace(/[\s_]+/g, '-').replace(/-+/g, '-').replace(/^[^a-z]+/, '') || 'page';
    const base = raw.slice(0, 56);
    let id = base, n = 2;
    while (taken.has(id)) id = `${base}-${n++}`;
    taken.add(id);
    return id;
}

function routePrefix(route) {
    const part = String(route || '').split('/').filter(Boolean)[0];
    if (!part || part.startsWith(':') || part === '*') return '';
    return `/${part}`;
};

const AJAX = /\/(ajax|api)(\/|$)|xhr|\.json$/i;
const DOWNLOAD = /\/(download|export|import|publish)(\/|$)/i;
const LOOKUP = /\/(perk-card|per-card|profile)\/(lookup|receiver)$/i;
const TRANSLATE = /^\/(files|translate|publish|export|import)(\/|$)/i;

// JSON, file, POST-only, media, robots, and scan-marked AJAX routes are compact URL cards, not pages.
export function isEndpoint(node = {}, screen = null) {
    const explicit = String(screen?.attrs?.layer || node.layer || node.lane || '').toLowerCase();
    if (explicit === 'ajax') return true;
    const route = String(node.route || screen?.attrs?.route || '');
    const path = route.split(/[?#]/)[0].replace(/\/+$/, '') || (route ? '/' : '');
    if (AJAX.test(path) || API.test(path) || DOWNLOAD.test(path) || LOOKUP.test(path) || TRANSLATE.test(path)) return true;
    const methods = node.methods || screen?.attrs?.methods;
    const listed = Array.isArray(methods) ? methods.map((m) => String(m).toUpperCase()) : [];
    const single = String(screen?.attrs?.method || node.method || '').toUpperCase();
    const verbs = listed.length ? listed : (single ? [single] : []);
    if (verbs.length && verbs.every((m) => m !== 'GET' && m !== 'HEAD')) return true;
    const view = String(node.view || screen?.attrs?.view || '');
    const kind = String(node.scanKind || screen?.attrs?.kind || '').toLowerCase();
    if (kind === 'api' || kind === 'endpoint' || kind === 'ajax') return true;
    if (/JsonResponse|APIView|ViewSet|FileResponse|StreamingResponse/i.test(view) && !/Template|render/i.test(view)) return true;
    return false;
}

// First `#|` line under each screen, keyed by screen id. The wire parser skips those lines.
export function screenComments(source) {
    const out = new Map();
    let id = '';
    let open = false;
    for (const line of String(source || '').split('\n')) {
        const screen = /^\s*screen\b(.*)$/.exec(line);
        if (screen) {
            id = (/\bid=([A-Za-z][\w-]*)/.exec(screen[1]) || [])[1] || '';
            open = true;
            continue;
        }
        if (!open) continue;
        const desc = /^\s*#\| ?(.*)$/.exec(line);
        if (desc) {
            const text = desc[1].trim();
            if (id && text && !out.has(id)) out.set(id, text);
            continue;
        }
        if (line.trim()) open = false;
    }
    return out;
}

export function pageLayer(node = {}, screen = null) {
    const explicit = String(screen?.attrs?.layer || node.layer || '').toLowerCase();
    if (LAYERS.has(explicit)) return explicit;
    if (node.shape === 'diamond') return 'guidance';
    const route = String(node.route || '');
    const template = String(node.template || '');
    const id = String(screen?.attrs?.id || node.screenKey || '');
    if (isEndpoint(node, screen) || AJAX.test(route) || API.test(route)) return 'ajax';
    if (route.startsWith('/admin') || template.startsWith('admin/') || /(^|-)admin($|-)/.test(id)) return 'backend';
    return 'frontend';
}

// sitemap: workspace.sitemap (may be null), screens: parseWireframe(...).screens, captures: pageCaptureFor(...)
// meta: readSidecar(content, 'wire').nodes keyed by screen attrs.id.
export function buildIfaceModel({ sitemap = null, screens = [], captures = null, meta = {}, source = '' } = {}) {
    const graph = connectedSitemap(sitemap || {}, screens);
    const byScreen = new Map(screens.map((s) => [String(s.id), s]));
    const comments = screenComments(source);
    const used = new Set();
    const nodes = [];
    const takenKeys = new Set();
    for (const screen of screens) if (screen?.attrs?.id) takenKeys.add(screen.attrs.id);
    const add = (base, screen) => {
        const journey = isJourneyMap(base.label || screen?.label, base.route, screen?.attrs?.layer);
        if (journey) base.route = '';
        const shot = captures?.pages?.[base.sitemapId] || null;
        const screenKey = screen?.attrs?.id || (screen ? screenSlug(base.label, base.route, takenKeys) : '');
        const sidecar = screenKey ? (meta[screenKey] || null) : null;
        const shape = sidecar?.shape || defaultShape(base, shot);
        const note = sidecar?.note || comments.get(screenKey) || '';
        const rawMethod = String(screen?.attrs?.method || '').toUpperCase();
        const method = /^(GET|POST|PUT|PATCH|DELETE|HEAD)$/.test(rawMethod) ? rawMethod : '';
        const lane = journey ? 'guidance' : pageLayer({ ...base, shape, screenKey }, screen);
        const kind = journey ? 'guidance' : (isEndpoint({ ...base, shape, screenKey, method, lane }, screen) || lane === 'ajax' ? 'endpoint' : 'page');
        nodes.push({ ...base, key: base.key, screenId: screen ? String(screen.id) : '', screenKey, shot,
            shape, shapeSource: sidecar?.shape ? 'sidecar' : 'default', note, method, links: sidecar?.links || {},
            lane, kind, quiet: Boolean(shot?.notFound) && !screen });
    };
    for (const n of graph.nodes) {
        const screen = n.screenId ? byScreen.get(n.screenId) : null;
        if (screen) used.add(String(screen.id));
        add({ key: `s:${n.id}`, sitemapId: n.id, label: n.label, route: n.route, template: n.template, view: n.view, status: n.status, auth: n.auth }, screen);
    }
    for (const screen of screens) {
        if (used.has(String(screen.id))) continue;
        const route = routeKey(screen.attrs?.route) || '';
        const base = { key: `w:${screen.id}`, sitemapId: '', label: String(screen.label || screen.attrs?.id || 'Page').slice(0, 80), route, template: screen.attrs?.uses || '', view: '', status: '', auth: false };
        add(base, screen);
    }
    const hidden = hideJunkPages(nodes);
    for (let i = nodes.length - 1; i >= 0; i -= 1) {
        const node = nodes[i];
        if (!isJourneyMap(node.label, node.route, node.lane)) continue;
        hidden.unshift({ label: node.label, route: node.route, reason: 'journey map' });
        nodes.splice(i, 1);
    }
    const byKey = new Map(nodes.map((n) => [n.key, n]));
    const bySitemap = new Map(nodes.filter((n) => n.sitemapId).map((n) => [n.sitemapId, n]));
    const byScreenKey = new Map(nodes.filter((n) => n.screenKey).map((n) => [n.screenKey, n]));
    const edges = [];
    const seenEdges = new Map();
    const addEdge = (from, to, kind, label, journey = false) => {
        if (!from || !to || from === to) return;
        const key = `${from}>${to}`;
        const existing = seenEdges.get(key);
        if (existing) {
            if (journey && !existing.journey) Object.assign(existing, { kind, label, journey });
            return;
        }
        const edge = { from, to, kind, label: label || '', journey };
        seenEdges.set(key, edge);
        edges.push(edge);
    };
    for (const e of graph.edges) {
const a = bySitemap.get(e.from), b = bySitemap.get(e.to);
        if (a && b) addEdge(a.key, b.key, e.kind, e.label || '');
    }
    for (const n of nodes) for (const [target, label] of Object.entries(n.links || {})) {
        const b = byScreenKey.get(target);
        if (b) addEdge(n.key, b.key, 'link', String(label || ''), true);
    }
    const byRoute = new Map();
    for (const n of nodes) {
        if (!n.route || n.lane === 'guidance' || isJourneyMap(n.label, n.route, n.lane)) continue;
        if (!byRoute.has(n.route)) byRoute.set(n.route, n);
    }
    const walkLinks = (screenNode, found, inNavigation = false) => {
        if (!screenNode || typeof screenNode !== 'object') return;
        const route = routeKey(screenNode.attrs?.route || screenNode.attrs?.href || '');
        const shared = inNavigation || ['nav', 'header', 'footer', 'menu'].includes(screenNode.type);
        if ((screenNode.type === 'link' || screenNode.type === 'button') && route) {
            const target = byRoute.get(route);
            const label = String(screenNode.label || '');
            const journey = !shared && (screenNode.type === 'button' || (label && label.toLowerCase() !== target?.label.toLowerCase()));
            found.push({ target, label, journey: Boolean(journey) });
        }
        for (const child of screenNode.children || []) walkLinks(child, found, shared);
    };
    for (const screen of screens) {
        const from = nodes.find((n) => n.screenId === String(screen.id));
        if (!from) continue;
        const found = [];
        walkLinks(screen, found);
        for (const link of found) addEdge(from.key, link.target?.key, 'link', link.label, link.journey);
    }
    for (const n of nodes) {
        if (!n.route || n.route === '/' || n.lane === 'guidance' || isJourneyMap(n.label, n.route, n.lane)) continue;
        const parts = n.route.split('/').filter(Boolean);
        let parent = null;
        while (parts.length && !parent) {
            parts.pop();
            parent = byRoute.get(parts.length ? `/${parts.join('/')}` : '/') || null;
            if (parent && (parent.key === n.key || parent.lane === 'guidance' || parent.route === '/')) parent = null;
        }
        if (parent) addEdge(parent.key, n.key, 'parent', 'URL hierarchy');
    }
    markChromeEdges(nodes, edges);
    const states = pageStates(source, meta);
    for (const node of nodes) node.state = states.get(node.screenKey) || states.get(node.key) || 'normal';
    // Outlined counts real pages only (guidance and endpoints are not pages).
    return { nodes, edges, byKey, coverage: graph.coverage, note: graph.note, hidden,
        outlined: nodes.filter((n) => n.screenId && n.lane !== 'guidance' && n.kind !== 'endpoint').length };
}

const GENERATED = /^\s*#\s*@generated\s+scan:\S+\s+hash:([0-9a-f]{8})\s*$/;
const STALE_MARK = /^\s*#\s*@stale\s+scan:\S+\s+/;
const STALE_LABEL = /\s*\(stale\)\s*$/i;

// Derive normal / changed / deleted from generated-hash and stale markers, or a sidecar state.
export function pageStates(source = '', meta = {}) {
    const states = new Map();
    const lines = String(source || '').split('\n');
    let marker = '';
    let stale = false;
    let hasMarkers = false;
    const screens = [];
    for (const line of lines) {
        if (GENERATED.test(line)) { marker = 'generated'; hasMarkers = true; stale = false; continue; }
        if (STALE_MARK.test(line)) { marker = 'stale'; hasMarkers = true; stale = true; continue; }
        const screen = /^\s*screen\b(.*)$/.exec(line);
        if (screen) {
            const id = (/\bid=([A-Za-z][\w-]*)/.exec(screen[1]) || [])[1] || '';
            const label = (/"([^"]*)"/.exec(screen[1]) || [])[1] || '';
            screens.push({ id, label, marker, stale: stale || STALE_LABEL.test(label) });
            marker = '';
            stale = false;
        }
    }
    for (const screen of screens) {
        if (!screen.id) continue;
        const sidecar = meta[screen.id]?.state;
        if (sidecar === 'deleted' || sidecar === 'changed' || sidecar === 'normal') states.set(screen.id, sidecar);
        else if (screen.stale) states.set(screen.id, 'deleted');
        else if (screen.marker === 'generated') states.set(screen.id, 'normal');
        else if (hasMarkers) states.set(screen.id, 'changed');
        else states.set(screen.id, 'normal');
    }
    return states;
}

function markChromeEdges(nodes, edges) {
    const pages = nodes.filter((node) => node.lane !== 'guidance').length || nodes.length;
    const threshold = Math.max(3, Math.ceil(pages * 0.4));
    const incoming = new Map();
    const outgoing = new Map();
    for (const edge of edges) {
        incoming.set(edge.to, (incoming.get(edge.to) || 0) + 1);
        outgoing.set(edge.from, (outgoing.get(edge.from) || 0) + 1);
    }
    const byKey = new Map(nodes.map((node) => [node.key, node]));
    for (const edge of edges) {
        const from = byKey.get(edge.from);
        const manyIn = (incoming.get(edge.to) || 0) >= threshold;
        const overviewOut = from && (isJourneyMap(from.label, from.route, from.lane) || (/^overview\b/i.test(from.label || '') && (outgoing.get(edge.from) || 0) >= threshold));
        edge.chrome = !edge.journey && Boolean(manyIn || overviewOut);
    }
}

const HUB_LINKS = 3;
const MAX_RADIAL_PAGES = 40;

function cardHeight(node) {
    if (node.lane === 'guidance') return NODE.guideH;
    if (node.kind === 'endpoint' || node.lane === 'ajax') return NODE.quietH;
    return node.quiet ? NODE.quietH : NODE.h;
}

// Undirected hop from the hub. A direct link is ring 1. The page after that is ring 2.
function hopRings(nodes, edges, hubKey) {
    const ids = new Set(nodes.map((node) => node.key));
    const next = new Map();
    const link = (from, to) => {
        if (!ids.has(from) || !ids.has(to) || from === to) return;
        const list = next.get(from) || [];
        if (!list.includes(to)) list.push(to);
        next.set(from, list);
    };
    for (const edge of edges) { link(edge.from, edge.to); link(edge.to, edge.from); }
    const dist = new Map([[hubKey, 0]]);
    const queue = [hubKey];
    while (queue.length) {
        const cur = queue.shift();
        const hop = dist.get(cur);
        for (const key of next.get(cur) || []) {
            if (dist.has(key)) continue;
            dist.set(key, hop + 1);
            queue.push(key);
        }
    }
    return dist;
}

function hubNode(nodes, edges) {
    const ids = new Set(nodes.map((node) => node.key));
    const out = new Map();
    for (const edge of edges) {
        if (!ids.has(edge.from) || !ids.has(edge.to) || edge.from === edge.to) continue;
        const list = out.get(edge.from) || [];
        if (!list.includes(edge.to)) list.push(edge.to);
        out.set(edge.from, list);
    }
    let hub = null, best = 0;
    const ordered = [...nodes].sort((a, b) => a.label.localeCompare(b.label) || a.route.localeCompare(b.route));
    for (const node of ordered) {
        const count = (out.get(node.key) || []).length;
        if (count > best) { best = count; hub = node; }
    }
    return best >= HUB_LINKS ? hub : null;
}

// ponytail: even rings, not a force layout. Cards on one ring share a radius. Upgrade path is an
// overlap pass if a ring mixes very different card heights and a chord still clips.
function layoutRadial(model, hub) {
    const dist = hopRings(model.nodes, model.edges || [], hub.key);
    const byHop = new Map();
    const loose = [];
    for (const node of model.nodes) {
        if (node.key === hub.key) continue;
        const hop = dist.get(node.key);
        if (hop == null) loose.push(node);
        else {
            const list = byHop.get(hop) || [];
            list.push(node);
            byHop.set(hop, list);
        }
    }
    const gap = NODE.gapX + NODE.linkGap;
    const chord = NODE.w + gap;
    const spots = [{ node: hub, cx: 0, cy: 0 }];
    let prev = 0;
    const ringRadius = (count, minRadius) => Math.max(minRadius, count < 2 ? chord : (count * chord) / (2 * Math.PI));
    const placeArc = (nodes, radius) => {
        const groups = LANES.map((lane) => nodes.filter((node) => node.lane === lane.id)).filter((group) => group.length);
        const count = nodes.length;
        const gapAngle = groups.length > 1 ? Math.min(0.5, Math.PI / Math.max(count, 1)) : 0;
        const step = count ? (2 * Math.PI - gapAngle * (groups.length - 1)) / count : 0;
        let angle = -Math.PI / 2;
        for (const group of groups) {
            for (const node of group) {
                spots.push({ node, cx: Math.cos(angle) * radius, cy: Math.sin(angle) * radius });
                angle += step;
            }
            angle += gapAngle;
        }
    };
    for (const hop of [...byHop.keys()].sort((a, b) => a - b)) {
        const ring = byHop.get(hop).slice().sort((a, b) => (
            LANES.findIndex((lane) => lane.id === a.lane) - LANES.findIndex((lane) => lane.id === b.lane)
            || a.label.localeCompare(b.label) || a.route.localeCompare(b.route)
        ));
        for (let index = 0; index < ring.length; index += 8) {
            const slice = ring.slice(index, index + 8);
            const radius = ringRadius(slice.length, prev + NODE.h + gap);
            placeArc(slice, radius);
            prev = radius;
        }
    }
    if (loose.length) {
        loose.sort((a, b) => a.label.localeCompare(b.label) || a.route.localeCompare(b.route));
        const count = loose.length;
        const fit = count < 2 ? NODE.w + NODE.gapX : (count * (NODE.w + NODE.gapX)) / (2 * Math.PI);
        const radius = Math.max(prev + NODE.h + gap, fit);
        loose.forEach((node, index) => {
            const angle = (2 * Math.PI * index) / count;
            spots.push({ node, cx: Math.cos(angle) * radius, cy: Math.sin(angle) * radius });
        });
    }
    let minX = Infinity, minY = Infinity;
    for (const spot of spots) {
        minX = Math.min(minX, spot.cx - NODE.w / 2);
        minY = Math.min(minY, spot.cy - cardHeight(spot.node) / 2);
    }
    let width = NODE.w, height = 0;
    for (const spot of spots) {
        const h = cardHeight(spot.node);
        spot.node.x = Math.round(spot.cx - NODE.w / 2 - minX);
        spot.node.y = Math.round(spot.cy - h / 2 - minY);
        spot.node.w = NODE.w;
        spot.node.h = h;
        width = Math.max(width, spot.node.x + NODE.w);
        height = Math.max(height, spot.node.y + h);
    }
    return { lanes: [], width, height: Math.max(height, NODE.h), cols: 0, focus: 'hub' };
}

// One outgoing edge per page, kept inside the lane. A chain of two or more is placed as its own row.
function flowChains(rows, edges) {
    const ids = new Set(rows.map((n) => n.key));
    const byKey = new Map(rows.map((n) => [n.key, n]));
    const next = new Map();
    const incoming = new Set();
    for (const edge of edges) {
        if (!ids.has(edge.from) || !ids.has(edge.to) || next.has(edge.from)) continue;
        next.set(edge.from, edge.to);
        incoming.add(edge.to);
    }
    const seen = new Set();
    const chains = [];
    for (const node of rows) {
        if (seen.has(node.key) || incoming.has(node.key)) continue;
        const chain = [];
        let cur = node;
        while (cur && !seen.has(cur.key)) {
            seen.add(cur.key);
            chain.push(cur);
            cur = byKey.get(next.get(cur.key));
        }
        if (chain.length > 1) chains.push(chain);
        else for (const step of chain) seen.delete(step.key);
    }
    return { chains, rest: rows.filter((n) => !seen.has(n.key)) };
}

// Lanes stacked top to bottom. A connected chain is its own row, with linkGap of open space between cards.
// Pages with no flow stay in a wrapped grid. Compact "not found" cards start a new row. `aspect` is canvas
// width / height so the board fits the viewport about right.
export function layoutIface(model, { aspect = 1.6, pinned = {} } = {}) {
    const large = model.nodes.length > MAX_RADIAL_PAGES;
    const hub = large ? null : hubNode(model.nodes, model.edges || []);
    if (hub) {
        const radial = layoutRadial(model, hub);
        return applyPinned(model, radial, pinned);
    }
    const lanes = [];
    let y = 0, width = NODE.w;
    const counts = LANES.map((l) => model.nodes.filter((n) => n.lane === l.id).length);
    const total = counts.reduce((a, b) => a + b, 0) || 1;
    const idealColumns = Math.round(Math.sqrt(total * aspect * (NODE.h + NODE.gapY) / (NODE.w + NODE.gapX)));
    const cols = Math.max(2, large ? idealColumns : Math.min(8, idealColumns));
    const linkStep = NODE.w + NODE.gapX + NODE.linkGap;
    const gridStep = NODE.w + NODE.gapX;
    const place = (node, x) => {
        const h = large && !node.screenId && !node.shot?.image && node.route && node.lane !== 'ajax'
            ? Math.min(cardHeight(node), 80)
            : cardHeight(node);
        node.x = x;
        node.y = y;
        node.w = NODE.w;
        node.h = h;
        width = Math.max(width, node.x + NODE.w);
        return h;
    };
    for (const lane of LANES) {
        const rows = model.nodes.filter((n) => n.lane === lane.id)
            .sort((a, b) => Number(a.quiet) - Number(b.quiet) || a.label.localeCompare(b.label) || a.route.localeCompare(b.route));
        if (!rows.length) continue;
        const { chains, rest } = large
            ? flowClusters(rows, model.edges || [])
            : flowChains(rows, model.edges || []);
        const top = y;
        y += NODE.laneHead;
        for (const chain of chains) {
            let x = 0, rowH = 0, col = 0;
            for (const node of chain) {
                if (col >= cols) { y += rowH + NODE.gapY + NODE.linkGap; x = 0; rowH = 0; col = 0; }
                rowH = Math.max(rowH, place(node, x));
                x += linkStep;
                col += 1;
            }
            y += rowH + NODE.gapY + NODE.linkGap;
        }
        let col = 0, rowH = 0, quietStarted = false;
        for (const node of rest) {
            if (node.quiet && !quietStarted) { quietStarted = true; if (col) { y += rowH + NODE.gapY; col = 0; rowH = 0; } }
            if (col >= cols) { y += rowH + NODE.gapY; col = 0; rowH = 0; }
            rowH = Math.max(rowH, place(node, col * gridStep));
            col += 1;
        }
        if (col) y += rowH + NODE.gapY;
        lanes.push({ id: lane.id, label: lane.label, count: rows.length, x: -14, y: top, h: y - top - NODE.gapY + 14 });
        y += NODE.laneGap - NODE.gapY;
    }
    for (const lane of lanes) lane.w = width + 28;
    return applyPinned(model, { lanes, width, height: Math.max(NODE.h, y - NODE.laneGap + NODE.gapY), cols, large }, pinned);
}

// Connected groups on real flow edges (shared-nav / overview-star edges are ignored).
function flowClusters(rows, edges) {
    const ids = new Set(rows.map((node) => node.key));
    const byKey = new Map(rows.map((node) => [node.key, node]));
    const adj = new Map(rows.map((node) => [node.key, []]));
    for (const edge of edges) {
        if (edge.chrome || !ids.has(edge.from) || !ids.has(edge.to)) continue;
        adj.get(edge.from).push(edge.to);
        adj.get(edge.to).push(edge.from);
    }
    const byPrefix = new Map();
    for (const node of rows) {
        const prefix = routePrefix(node.route);
        if (!prefix || node.lane === 'guidance') continue;
        const list = byPrefix.get(prefix) || [];
        list.push(node.key);
        byPrefix.set(prefix, list);
    }
    for (const keys of byPrefix.values()) {
        for (let i = 1; i < keys.length; i += 1) {
            adj.get(keys[0]).push(keys[i]);
            adj.get(keys[i]).push(keys[0]);
        }
    }
    const seen = new Set();
    const clusters = [];
    const rest = [];
    for (const node of rows) {
        if (seen.has(node.key)) continue;
        const group = [];
        const queue = [node.key];
        seen.add(node.key);
        while (queue.length) {
            const key = queue.shift();
            group.push(byKey.get(key));
            for (const next of adj.get(key) || []) {
                if (seen.has(next)) continue;
                seen.add(next);
                queue.push(next);
            }
        }
        if (group.length > 1) {
            const chained = flowChains(group, edges);
            if (chained.chains.length === 1 && chained.rest.length === 0) clusters.push(chained.chains[0]);
            else clusters.push(group);
        } else rest.push(node);
    }
    return { chains: clusters, rest };
}

function applyPinned(model, layout, pinned) {
    const pins = pinned && typeof pinned === 'object' ? pinned : {};
    for (const node of model.nodes) {
        const pin = pins[node.screenKey] || pins[node.key];
        if (!pin || !Number.isFinite(Number(pin.x)) || !Number.isFinite(Number(pin.y))) continue;
        node.x = Number(pin.x);
        node.y = Number(pin.y);
        node.pinned = true;
        layout.width = Math.max(layout.width, node.x + node.w);
        layout.height = Math.max(layout.height, node.y + node.h);
    }
    for (const lane of layout.lanes || []) lane.w = layout.width + 28;
    return layout;
}

// Stable page id for a new screen (same rule as flow-meta slugId: letters first, 64 max).
export function newScreenSource({ label, route = '', id }) {
    const safe = String(label || 'New page').replace(/["\n\r]/g, '').trim().slice(0, 80) || 'New page';
    const r = routeKey(route.trim().startsWith('/') ? route.trim() : (route.trim() ? `/${route.trim()}` : ''));
    return `screen "${safe}" id=${id}${r ? ` route=${r}` : ''}\n  heading "${safe}"\n`;
}
