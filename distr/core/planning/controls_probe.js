() => {
  // Every control on the rendered page: where each link goes, where each form posts, and what each
  // button is wired to. No field values are read; only names, types and visible labels.
  const clean = (t, n) => (t || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const region = (el) => {
    for (let n = el.parentElement; n; n = n.parentElement) {
      const tag = n.tagName.toLowerCase(), role = n.getAttribute('role') || '';
      if (tag === 'nav' || role === 'navigation') return 'nav';
      if (tag === 'header' || role === 'banner') return 'header';
      if (tag === 'footer' || role === 'contentinfo') return 'footer';
      if (tag === 'aside' || role === 'complementary') return 'sidebar';
      if (tag === 'dialog' || role === 'dialog') return 'dialog';
      if (tag === 'main' || role === 'main') return 'main';
    }
    return 'body';
  };
  const label = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return clean(aria, 80);
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return clean(l.textContent, 80); }
    const wrap = el.closest('label'); if (wrap) return clean(wrap.textContent, 80);
    const group = el.closest('fieldset'); const legend = group && group.querySelector('legend');
    return clean(el.getAttribute('placeholder') || (legend ? legend.textContent : ''), 80);
  };
  const handlers = (el) => {
    const out = [];
    for (const name of ['onclick', 'onchange', 'onsubmit', 'oninput']) if (el.hasAttribute(name)) out.push(name);
    for (const attr of el.attributes) {
      if (/^(data-(bs-)?(toggle|target|dismiss|action|href|url|modal|tab|bind)|hx-(get|post|put|delete|target)|x-on:|@click|v-on:|wire:click|ng-click|data-controller)/.test(attr.name)) {
        out.push(`${attr.name}=${clean(attr.value, 60)}`);
      }
    }
    for (const name of ['aria-controls', 'aria-expanded', 'aria-haspopup']) if (el.hasAttribute(name)) out.push(`${name}=${clean(el.getAttribute(name), 40)}`);
    return out.slice(0, 6);
  };
  const forms = [];
  const formIndex = new Map();
  for (const form of [...document.forms].slice(0, 30)) {
    if (!visible(form) && !form.querySelector('input:not([type=hidden]),select,textarea')) continue;
    const fields = [];
    let csrf = false;
    for (const f of [...form.elements].slice(0, 60)) {
      const tag = f.tagName.toLowerCase();
      if (!['input', 'select', 'textarea'].includes(tag)) continue;
      const type = tag === 'input' ? (f.type || 'text') : tag;
      if (type === 'hidden') { if (/csrf|_token|authenticity/i.test(f.name)) csrf = true; continue; }
      if (['submit', 'button', 'reset', 'image'].includes(type)) continue;
      fields.push({ n: clean(f.name || f.id, 60), t: type, l: label(f), r: f.required ? 1 : 0, ...(tag === 'select' ? { o: f.options.length } : {}) });
    }
    const submits = [...form.querySelectorAll('button:not([type=button]):not([type=reset]),input[type=submit],input[type=image]')]
      .map(b => clean(b.textContent || b.value || b.getAttribute('aria-label'), 50)).filter(Boolean).slice(0, 4);
    formIndex.set(form, forms.length);
    forms.push({ a: form.getAttribute('action') || '', m: (form.method || 'get').toLowerCase(), enc: form.enctype === 'multipart/form-data' ? 'multipart' : '',
      id: clean(form.id, 40), fields, submits, csrf: csrf ? 1 : 0, rg: region(form), h: handlers(form) });
  }
  const links = [], seen = new Set();
  for (const a of [...document.querySelectorAll('a[href]')].slice(0, 1500)) {
    if (!visible(a)) continue;
    const href = a.getAttribute('href') || '';
    const text = clean(a.textContent || a.getAttribute('aria-label') || a.getAttribute('title') || (a.querySelector('img') || {}).alt, 70);
    const key = href + '|' + text;
    if (seen.has(key)) continue;
    seen.add(key);
    if (links.length < 160) links.push({ h: href.slice(0, 200), t: text, rg: region(a), tg: a.target || '', hd: handlers(a) });
  }
  const buttons = [];
  for (const b of [...document.querySelectorAll('button,[role=button],input[type=button],input[type=submit]')].slice(0, 400)) {
    if (!visible(b) || buttons.length >= 80) continue;
    const form = b.closest('form');
    buttons.push({ t: clean(b.textContent || b.value || b.getAttribute('aria-label') || b.getAttribute('title'), 60), ty: (b.getAttribute('type') || (b.tagName === 'BUTTON' ? 'submit' : 'button')),
      fm: form && formIndex.has(form) ? formIndex.get(form) : -1, rg: region(b), dis: b.disabled ? 1 : 0, hd: handlers(b) });
  }
  const loose = [];
  for (const f of [...document.querySelectorAll('input,select,textarea')].slice(0, 400)) {
    if (f.form || !visible(f) || loose.length >= 30) continue;
    const type = f.tagName === 'INPUT' ? (f.type || 'text') : f.tagName.toLowerCase();
    if (['hidden', 'submit', 'button'].includes(type)) continue;
    loose.push({ n: clean(f.name || f.id, 60), t: type, l: label(f), hd: handlers(f) });
  }
  const meta = document.querySelector('meta[http-equiv=refresh]');
  return { title: clean(document.title, 120), links, forms, buttons, loose, refresh: meta ? clean(meta.getAttribute('content'), 120) : '',
    framework: window.__NEXT_DATA__ ? 'next' : (window.React || document.querySelector('[data-reactroot],#root,#__next') ? 'react' : (window.Vue || document.querySelector('[data-v-app]') ? 'vue' : (document.querySelector('input[name=csrfmiddlewaretoken]') ? 'django' : ''))) };
}
