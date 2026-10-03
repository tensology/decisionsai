// Pure projection of expanded DSL screens. It never invents pages or transitions.
export function wireframeSitemap(documents) {
    const pages = documents.flatMap(({id, screens}) => screens.map(screen => ({artifactId: id, screen})));
    if (pages.length > 200) throw new Error('The sitemap supports at most 200 pages.');
    const label = value => String(value).slice(0, 160).replace(/[&<>"\n\r#]/g, char => `#${char.codePointAt(0)};`);
    const routeKey = value => {
        if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')) return null;
        return value.split(/[?#]/)[0].replace(/\/+$/, '') || '/';
    };
    const routes = new Map();
    const diagnostics = [];
    const lines = ['flowchart TD'];
    const nodes = pages.map(({artifactId, screen}, index) => {
        const key = `p${index}`;
        const route = routeKey(screen.attrs.route);
        if (route) routes.set(route, [...(routes.get(route) || []), key]);
        lines.push(`  ${key}["${label(screen.label || screen.id)}${route ? ` (${label(route)})` : ''}"]`);
        return {key, artifactId, screen};
    });
    for (const [route, keys] of routes) {
        if (keys.length > 1) diagnostics.push(`Multiple pages define ${route}. Navigation to that route is ambiguous.`);
    }
    const edges = new Set();
    const missing = new Map();
    for (const page of nodes) {
        const visit = node => {
            if (node !== page.screen) {
                const route = routeKey(node.attrs.route || node.attrs.href);
                if (route) {
                    const matches = routes.get(route) || [];
                    let target = matches.length === 1 ? matches[0] : null;
                    if (!target) {
                        if (!missing.has(route)) {
                            if (missing.size >= 200) throw new Error('Too many undefined routes to display in one sitemap.');
                            const key = `u${missing.size}`;
                            missing.set(route, key);
                            lines.push(`  ${key}["${label(route)} (${matches.length ? 'ambiguous' : 'not defined'})"]`);
                            lines.push(`  style ${key} stroke-dasharray:5 5`);
                            if (!matches.length) diagnostics.push(`No wireframe defines ${route}.`);
                        }
                        target = missing.get(route);
                    }
                    edges.add(`  ${page.key} --> ${target}`);
                    if (edges.size > 800) throw new Error('Too many navigation links to display in one sitemap.');
                }
            }
            node.children.forEach(visit);
        };
        visit(page.screen);
    }
    return {source: [...lines, ...edges].join('\n'), diagnostics, pages: nodes};
}


const SITEMAP_NODE = { width: 210, height: 176, gapX: 84, gapY: 44 };
const SITEMAP_EDGE_KINDS = ['parent', 'navigation', 'journey', 'auth', 'redirect', 'success', 'failure'];
const SITEMAP_STATUSES = ['verified', 'inferred', 'incomplete', 'missing'];

function sitemapRouteKey(value) {
    if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')) return '';
    const path = value.split(/[?#]/)[0];
    if (path === '/') return '/';
    return path.replace(/\/+$/, '') || '/';
}

// Connected-node projection of the cached HTML/views/URL sitemap.
// Base templates stay in scaffolding and are never nodes. Mermaid is not used.
export function connectedSitemap(model, screens = []) {
    const safe = model && typeof model === 'object' ? model : {};
    const scaffolding = (Array.isArray(safe.scaffolding) ? safe.scaffolding : [])
        .filter(item => item && item.kind !== 'page' && item.kind !== 'page_template')
        .map(item => ({
            id: String(item.id || item.path || item.label || 'scaffold'),
            label: String(item.label || item.path || 'Base template'),
            path: String(item.path || ''),
            template: String(item.template || ''),
        }));
    const hidden = new Set(scaffolding.flatMap(item => [item.id, item.template, item.path].filter(Boolean)));
    const discovered = (Array.isArray(safe.nodes) ? safe.nodes : []).filter(node => {
        if (!node || !node.id) return false;
        if (node.kind === 'base_template') return false;
        if (hidden.has(node.id) || hidden.has(node.template) || hidden.has(node.path)) return false;
        return node.kind === 'page';
    });
    const byRoute = new Map();
    for (const screen of screens || []) {
        const key = sitemapRouteKey(screen?.attrs?.route);
        if (key) byRoute.set(key, [...(byRoute.get(key) || []), screen]);
    }
    const prepared = discovered.map(node => {
        const route = sitemapRouteKey(node.route || '');
        const candidates = byRoute.get(route) || [];
        const wanted = String(node.label || '').trim().toLowerCase();
        const screen = candidates.find(candidate => String(candidate.label || '').trim().toLowerCase() === wanted)
            || candidates.find(candidate => !/^overview\b/i.test(candidate.label || ''))
            || candidates[0];
        return {
            id: String(node.id),
            kind: 'page',
            label: String(node.label || node.route || node.id).slice(0, 80),
            route: route || String(node.route || ''),
            template: String(node.template || ''),
            view: String(node.view || ''),
            urlName: String(node.url_name || ''),
            status: SITEMAP_STATUSES.includes(node.status) ? node.status : 'incomplete',
            clickable: Boolean(screen),
            screenId: screen?.id ? String(screen.id) : '',
        };
    });
    // Pages without a stencil stay on the canvas. They are not clickable.
    const pages = prepared;
    const ids = new Set(pages.map(node => node.id));
    const edges = (Array.isArray(safe.edges) ? safe.edges : []).filter(edge => (
        edge && ids.has(String(edge.from)) && ids.has(String(edge.to)) && SITEMAP_EDGE_KINDS.includes(edge.kind)
    )).map(edge => ({
        from: String(edge.from),
        to: String(edge.to),
        kind: edge.kind,
        label: String(edge.label || ''),
    }));
    const incoming = new Set(edges.map(edge => edge.to));
    const byPageRoute = new Map(pages.map(node => [node.route, node]));
    for (const node of pages) {
        if (node.route === '/' || incoming.has(node.id)) continue;
        const parts = node.route.split('/').filter(Boolean);
        let parent = null;
        while (parts.length && !parent) {
            parts.pop();
            parent = byPageRoute.get(parts.length ? `/${parts.join('/')}` : '/') || null;
        }
        if (!parent || parent.id === node.id) continue;
        edges.push({ from: parent.id, to: node.id, kind: 'parent', label: 'URL hierarchy' });
        incoming.add(node.id);
    }
    return {
        note: String(safe.note || ''),
        coverage: String(safe.coverage || ''),
        scaffolding,
        nodes: pages,
        edges,
        discoveredCount: prepared.length,
        unmappedCount: pages.filter(node => !node.clickable).length,
    };
}

export function layoutConnectedSitemap(model, screens = []) {
    const graph = connectedSitemap(model, screens);
    const columns = new Map();
    for (const node of graph.nodes) {
        const parts = String(node.route || '').split('/').filter(part => part && !part.startsWith(':'));
        const column = node.route === '/' ? 0 : Math.max(1, parts.length);
        columns.set(column, [...(columns.get(column) || []), node]);
    }
    const placed = [];
    let width = SITEMAP_NODE.width;
    let height = SITEMAP_NODE.height;
    for (const column of [...columns.keys()].sort((a, b) => a - b)) {
        const rows = columns.get(column).slice().sort((a, b) => a.label.localeCompare(b.label) || a.id.localeCompare(b.id));
        rows.forEach((node, index) => {
            const x = column * (SITEMAP_NODE.width + SITEMAP_NODE.gapX);
            const y = index * (SITEMAP_NODE.height + SITEMAP_NODE.gapY);
            placed.push({ ...node, x, y, w: SITEMAP_NODE.width, h: SITEMAP_NODE.height });
            width = Math.max(width, x + SITEMAP_NODE.width);
            height = Math.max(height, y + SITEMAP_NODE.height);
        });
    }
    const byId = new Map(placed.map(node => [node.id, node]));
    const edges = graph.edges.map(edge => {
        const from = byId.get(edge.from);
        const to = byId.get(edge.to);
        return {
            ...edge,
            x1: from.x + from.w,
            y1: from.y + from.h / 2,
            x2: to.x,
            y2: to.y + to.h / 2,
        };
    });
    return { ...graph, nodes: placed, edges, width, height };
}
