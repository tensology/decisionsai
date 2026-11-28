() => {
  // Raw layout read of the rendered page: one record per visible element, with its
  // computed box and the CSS that decides how it is laid out. Python reduces it to
  // the stencil DSL (layout_probe.py). Keys are stable across viewport changes on the
  // same document, so desktop and mobile reads can be compared element by element.
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'HEAD', 'META', 'LINK', 'TITLE', 'BR', 'WBR',
    'OPTION', 'OPTGROUP', 'DATALIST', 'SOURCE', 'TRACK', 'AREA', 'MAP', 'DEFS', 'PATH', 'G', 'USE']);
  const MAX_KEYS = 6000, MAX_RECORDS = 3500;
  const keys = (window.__wfKeys = window.__wfKeys || { map: new WeakMap(), next: 1, parents: {} });
  const num = (v) => { const n = parseFloat(v); return Number.isFinite(n) ? Math.round(n * 10) / 10 : 0; };
  const color = (v) => {
    const m = /rgba?\(([^)]+)\)/.exec(v || '');
    if (!m) return [0, 0, 0, 0];
    const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
    return [p[0] | 0, p[1] | 0, p[2] | 0, p.length > 3 ? p[3] : 1];
  };
  const clean = (t, n) => (t || '').replace(/\s+/g, ' ').trim().slice(0, n);
  // Assign keys to every element (hidden too), so a mobile-only element has a key on desktop.
  let count = 0;
  (function assign(el, parentKey) {
    if (count >= MAX_KEYS || SKIP.has(el.tagName.toUpperCase())) return;
    let k = keys.map.get(el);
    if (!k) { k = keys.next++; keys.map.set(el, k); keys.parents[k] = parentKey; }
    count++;
    for (const child of el.children) assign(child, k);
  })(document.body, 0);

  const scrollX = window.scrollX, scrollY = window.scrollY;
  const records = [];
  const inlineDisplay = new Set(['inline', 'contents']);
  function textInfo(el) {
    let own = 0, blocky = false;
    for (const node of el.childNodes) {
      if (node.nodeType === 3 && node.textContent.trim()) own += node.textContent.trim().length;
      else if (node.nodeType === 1) {
        const d = getComputedStyle(node).display;
        if (d === 'none') continue;
        if (!inlineDisplay.has(d) && !['IMG', 'SVG', 'BR'].includes(node.tagName.toUpperCase())) blocky = true;
      }
    }
    return { own, blocky };
  }
  function walk(el, parentKey, clip) {
    if (records.length >= MAX_RECORDS) return;
    const tag = el.tagName.toLowerCase();
    if (SKIP.has(el.tagName.toUpperCase())) return;
    // Elements past the key cap (long scroll lists) are keyed on the fly.
    let k = keys.map.get(el);
    if (!k) { k = keys.next++; keys.map.set(el, k); keys.parents[k] = parentKey; }
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' && el !== document.body) {
      // Hidden here; its children are hidden too, so there are no records below it.
      return;
    }
    const rect = el.getBoundingClientRect();
    const r = [Math.round(rect.left + scrollX), Math.round(rect.top + scrollY), Math.round(rect.width), Math.round(rect.height)];
    const display = cs.display;
    if (display !== 'contents' && (r[2] <= 0 || r[3] <= 0) && el !== document.body) {
      // A zero-sized box can still hold visible overflow, so keep walking its children.
      for (const child of el.children) walk(child, parentKey, clip);
      return;
    }
    if (parseFloat(cs.opacity) === 0) return;
    if (display !== 'contents' && r[0] + r[2] <= 0 && el !== document.body) return;
    // Content scrolled out of a clipping ancestor is not on screen, so it is not part of the layout.
    if (clip && cs.position !== 'fixed' && display !== 'contents'
        && (r[0] >= clip[2] || r[0] + r[2] <= clip[0] || r[1] >= clip[3] || r[1] + r[3] <= clip[1])) return;
    let childClip = clip;
    if (el !== document.body && (cs.overflowX !== 'visible' || cs.overflowY !== 'visible')) {
      const mine = [r[0], r[1], r[0] + r[2], r[1] + r[3]];
      childClip = clip ? [Math.max(clip[0], mine[0]), Math.max(clip[1], mine[1]), Math.min(clip[2], mine[2]), Math.min(clip[3], mine[3])] : mine;
    }
    const rec = { k, p: parentKey, t: tag, d: display, r };
    if (cs.position !== 'static') rec.ps = cs.position;
    const flexish = display.includes('flex');
    if (flexish) {
      rec.fd = cs.flexDirection; if (cs.flexWrap !== 'nowrap') rec.fw = cs.flexWrap;
      rec.jc = cs.justifyContent; rec.ai = cs.alignItems;
    }
    if (display.includes('grid')) { rec.gtc = cs.gridTemplateColumns; rec.ai = cs.alignItems; }
    if (flexish || display.includes('grid')) { rec.rg = num(cs.rowGap); rec.cg = num(cs.columnGap); }
    const pcs = el.parentElement ? getComputedStyle(el.parentElement) : null;
    if (pcs && pcs.display.includes('flex')) { rec.fg = num(cs.flexGrow); }
    if (pcs && (pcs.display.includes('flex') || pcs.display.includes('grid')) && cs.alignSelf !== 'auto') rec.as = cs.alignSelf;
    if (pcs && pcs.display.includes('grid')) {
      const a = cs.gridColumnStart, b = cs.gridColumnEnd;
      if (a !== 'auto' || b !== 'auto') rec.gc = a + '/' + b;
    }
    rec.pad = [num(cs.paddingTop), num(cs.paddingRight), num(cs.paddingBottom), num(cs.paddingLeft)];
    rec.mar = [num(cs.marginTop), num(cs.marginRight), num(cs.marginBottom), num(cs.marginLeft)];
    if (cs.maxWidth !== 'none') rec.mw = num(cs.maxWidth);
    const bg = color(cs.backgroundColor);
    if (bg[3] > 0.04) rec.bg = bg;
    if (cs.backgroundImage !== 'none') rec.bi = 1;
    const bw = [num(cs.borderTopWidth), num(cs.borderRightWidth), num(cs.borderBottomWidth), num(cs.borderLeftWidth)];
    const bs = [cs.borderTopStyle, cs.borderRightStyle, cs.borderBottomStyle, cs.borderLeftStyle];
    const bc = [color(cs.borderTopColor), color(cs.borderRightColor), color(cs.borderBottomColor), color(cs.borderLeftColor)];
    const bd = bw.map((w, i) => (w > 0 && bs[i] !== 'none' && bs[i] !== 'hidden' && bc[i][3] > 0.04) ? w : 0);
    if (bd.some(Boolean)) rec.bd = bd; else rec.bdw = bw;
    const rad = num(cs.borderTopLeftRadius);
    if (rad > 0) rec.rad = rad;
    if (cs.boxShadow !== 'none') rec.sh = 1;
    rec.fg2 = color(cs.color);
    rec.fz = num(cs.fontSize);
    const lh = parseFloat(cs.lineHeight);
    rec.lh = Number.isFinite(lh) ? lh : Math.round(rec.fz * 1.2 * 10) / 10;
    if (parseInt(cs.fontWeight, 10) >= 600) rec.fb = 1;
    if (cs.textAlign && !['start', 'left'].includes(cs.textAlign)) rec.ta = cs.textAlign;
    if (cs.overflow !== 'visible') rec.ov = cs.overflow;
    if (/icon|symbol|glyph|awesome/i.test(cs.fontFamily || '')) rec.ic = 1;
    const info = textInfo(el);
    if (info.own && !info.blocky) {
      rec.tb = 1;
      rec.txt = clean(el.textContent, 80);
      try {
        const range = document.createRange(); range.selectNodeContents(el);
        const rects = [...range.getClientRects()].filter(q => q.width > 1 && q.height > 1);
        if (rects.length) {
          const tops = new Set(rects.map(q => Math.round(q.top / Math.max(4, rec.lh * 0.5))));
          rec.tl = tops.size;
          rec.tx = [Math.round(Math.min(...rects.map(q => q.left)) + scrollX), Math.round(Math.max(...rects.map(q => q.right)) + scrollX)];
        }
      } catch (e) { /* text extents are optional */ }
    } else if (info.own) rec.ot = info.own;
    if (['input', 'select', 'textarea'].includes(tag)) {
      rec.it = (el.getAttribute('type') || (tag === 'input' ? 'text' : tag)).toLowerCase();
      let label = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('name') || '';
      if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) label = l.textContent || label; }
      const wrap = el.closest('label'); if (!label && wrap) label = wrap.textContent;
      rec.lab = clean(label, 60);
      if (el.getAttribute('placeholder')) rec.ph = clean(el.getAttribute('placeholder'), 40);
    }
    if (tag === 'input' && ['submit', 'button', 'reset', 'image'].includes(rec.it)) rec.txt = clean(el.value || el.getAttribute('aria-label'), 40);
    if (tag === 'a') { rec.href = (el.getAttribute('href') || '').slice(0, 120); if (!rec.txt) rec.txt = clean(el.textContent, 60); }
    if (tag === 'button' && !rec.txt) rec.txt = clean(el.getAttribute('aria-label') || el.textContent, 60);
    if (['img', 'svg', 'canvas', 'video', 'picture', 'iframe'].includes(tag)) { rec.media = 1; rec.alt = clean(el.getAttribute('alt') || el.getAttribute('aria-label'), 60); }
    const cls = typeof el.className === 'string' ? el.className : '';
    const hint = clean((el.id || '') + ' ' + cls + ' ' + (el.getAttribute('role') || ''), 90);
    if (hint) rec.hint = hint;
    if (tag === 'li') rec.li = 1;
    records.push(rec);
    // Leaves hide their children; the DSL stops there.
    const stop = ['svg', 'select', 'textarea', 'picture', 'video', 'canvas', 'iframe', 'img', 'input'].includes(tag);
    if (!stop) for (const child of el.children) walk(child, display === 'contents' ? parentKey : k, childClip);
  }
  walk(document.body, 0, null);
  const htmlBg = color(getComputedStyle(document.documentElement).backgroundColor);
  const bodyBg = color(getComputedStyle(document.body).backgroundColor);
  return {
    vw: window.innerWidth, vh: window.innerHeight,
    pw: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
    ph: Math.max(document.documentElement.scrollHeight, document.body.scrollHeight),
    bg: bodyBg[3] > 0.04 ? bodyBg : (htmlBg[3] > 0.04 ? htmlBg : [255, 255, 255, 1]),
    records, truncated: records.length >= MAX_RECORDS,
  };
}
