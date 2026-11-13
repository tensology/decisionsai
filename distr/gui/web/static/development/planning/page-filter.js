// Default page skip rules for the Interfaces map (pure, no DOM; node-testable). Planning fixes, 12 Nov 2026.
// A page is hidden only when it is clearly not a real page of this project:
//   1. placeholder:   label or route holds a template/format placeholder ({{ }}, {% %}, %s, {url}, {qs},
//                     {language.iso_code}), e.g. "{url}?{qs}", "%s#parameters", the vendored translations
//                     admin language page.
//   2. admin name:    no route and the label is an unresolved Django URL name such as "Admin:index".
//   3. example shop:  outline-only screens (no scanned page behind them) from the bundled example shop:
//                     4 or more of /catalog, /cart, /checkout, /orders, /intents, /webhook, /csrf, /categories
//                     together. One real /checkout page on its own is never hidden.
//   4. stale copy:    a "(stale)" screen whose current counterpart (same route or same name) is on the map.
// Hidden pages are counted, never deleted; the stored plan is unchanged.
const PLACEHOLDER = /\{\{|\{%|%s|\{url\}|\{qs\}|\{language\.iso_code\}/i;
const EXAMPLE_SHOP = new Set(['catalog', 'cart', 'checkout', 'orders', 'intents', 'webhook', 'csrf', 'categories']);
const EXAMPLE_SHOP_MIN = 4;
const STALE = /\s*\(stale\)\s*$/i;

export function normRoute(route) {
    const r = String(route || '').trim()
        .replace(/<(?:[a-z_]+:)?([A-Za-z_]\w*)>/g, ':$1')
        .replace(/\{([A-Za-z_]\w*)\}/g, ':$1')
        .replace(/\/+$/, '');
    return r || (String(route || '').trim() ? '/' : '');
}

const firstSegment = (route) => String(route || '').replace(/^\/+/, '').split(/[/?#]/)[0].toLowerCase();

export function junkReason(node) {
    const label = String(node?.label || '').trim(), route = String(node?.route || '').trim();
    if (PLACEHOLDER.test(label) || PLACEHOLDER.test(route)) return 'placeholder';
    if (!route && /^admin:/i.test(label)) return 'admin name';
    return '';
}

// Removes hidden nodes from `nodes` in place and returns [{ label, route, reason }].
export function hideJunkPages(nodes) {
    const reasons = new Map();
    for (const node of nodes) { const why = junkReason(node); if (why) reasons.set(node, why); }
    const shop = nodes.filter((n) => !n.sitemapId && !reasons.has(n) && EXAMPLE_SHOP.has(firstSegment(n.route)));
    if (new Set(shop.map((n) => firstSegment(n.route))).size >= EXAMPLE_SHOP_MIN) {
        for (const node of shop) reasons.set(node, 'example shop');
    }
    const live = nodes.filter((n) => !reasons.has(n) && !STALE.test(n.label || ''));
    const liveRoutes = new Set(live.map((n) => normRoute(n.route)).filter(Boolean));
    const liveNames = new Set(live.map((n) => String(n.label || '').trim().toLowerCase()).filter(Boolean));
    for (const node of nodes) {
        if (reasons.has(node) || !STALE.test(node.label || '')) continue;
        const name = String(node.label).replace(STALE, '').trim().toLowerCase();
        const route = normRoute(node.route);
        if ((route && liveRoutes.has(route)) || (name && liveNames.has(name))) reasons.set(node, 'stale copy');
    }
    const hidden = [];
    for (let i = nodes.length - 1; i >= 0; i -= 1) {
        const why = reasons.get(nodes[i]);
        if (!why) continue;
        hidden.unshift({ label: nodes[i].label, route: nodes[i].route, reason: why });
        nodes.splice(i, 1);
    }
    return hidden;
}
