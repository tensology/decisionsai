"""Read a page's cue from its RENDERED DOM instead of its source.

The case that needs this: a fully internationalised app has no literal text in its
components - `Home.tsx` says `<h2>{copy.moreTitle}</h2>` - so a static extractor
sees an empty page. The strings exist, but only after the app resolves them.

It is also the only honest source for a client-rendered page, and for any stack
with no source extractor at all.

Three rules, because this is the expensive path:

- **Bounded.** One page at a time, a page budget, a hard timeout, and it stops at
  the first failure rather than grinding through a route list.
- **Fail-closed.** No Playwright, no server, a slow page, a crash - all mean *no
  cue*, never a guessed one. Silence beats a wrong stencil.
- **Marked.** A DOM-derived cue carries `observed: True`, because a rendered page
  is evidence of what a user sees, not of what the developer intended.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MS = 8_000
MAX_ROUTES = 40
MAX_CUE_FIELDS = 8
MAX_CUE_LABELS = 6

# What to read out of a rendered page. Kept to landmarks and controls: the same
# shape the source extractor produces, so everything downstream is unchanged.
_JS_READ = """() => {
  const text = (el) => (el && el.textContent ? el.textContent.replace(/\\s+/g, ' ').trim() : '');
  const clean = (value) => (value || '').slice(0, 80);
  const uniq = (list) => [...new Set(list.filter(Boolean))];

  const headings = uniq([...document.querySelectorAll('h1,h2,h3,h4,h5,h6')]
    .map(el => clean(text(el)))
    .filter(t => t.length >= 2 && t.length <= 80)).slice(0, 8);

  const fields = [];
  const types = {};
  for (const el of document.querySelectorAll('input,textarea,select')) {
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (['hidden', 'submit', 'reset', 'button', 'image'].includes(type)) continue;
    let label = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('name') || '';
    if (!label && el.id) {
      const forLabel = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      label = text(forLabel);
    }
    if (!label) {
      const wrap = el.closest('label');
      label = wrap ? text(wrap) : '';
    }
    label = clean(label);
    if (label.length < 2 || label.length > 80 || fields.includes(label)) continue;
    fields.push(label);
    if (['email', 'password', 'tel', 'number', 'search', 'url'].includes(type)) types[label] = type;
    if (fields.length >= 8) break;
  }

  const buttons = uniq([...document.querySelectorAll('button,input[type=submit],input[type=button],[role=button]')]
    .map(el => clean(el.getAttribute('aria-label') || text(el)))
    .filter(t => t.length >= 2 && t.length <= 40)).slice(0, 6);

  const links = [];
  for (const el of document.querySelectorAll('a[href]')) {
    const label = clean(text(el));
    const route = el.getAttribute('href') || '';
    if (label.length < 2 || !route.startsWith('/') || route === '#') continue;
    if (links.some(l => l.label === label)) continue;
    links.push({ label, route });
    if (links.length >= 4) break;
  }

  const regions = {};
  for (const name of ['header', 'nav', 'main', 'aside', 'footer']) {
    const el = document.querySelector(name);
    const label = clean(el ? text(el).slice(0, 48) : '');
    if (el) regions[name] = label.length >= 2 ? label : name[0].toUpperCase() + name.slice(1);
  }

  const columns = uniq([...document.querySelectorAll('table th')]
    .map(el => clean(text(el)))
    .filter(t => t.length >= 2)).slice(0, 8);

  return { headings, fields, types, buttons, links, regions, columns };
}"""


# Material/Google icon ligatures (arrow_forward, chevron_right) leak into innerText.
_ICON_WORD = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")


def strip_icon_words(text: Any) -> str:
    words = " ".join(str(text or "").split()).split(" ")
    kept = [word for word in words if word and not _ICON_WORD.fullmatch(word)]
    return " ".join(kept) or " ".join(words).strip()


def clean_cue(cue: Any) -> Any:
    """Drop icon ligature words from a saved cue's labels."""
    if not isinstance(cue, dict):
        return cue
    out = dict(cue)
    for key in ("buttons", "headings", "sections"):
        if isinstance(out.get(key), list):
            out[key] = [strip_icon_words(v) if isinstance(v, str) else v for v in out[key]]
    if isinstance(out.get("links"), list):
        out["links"] = [{**v, "label": strip_icon_words(v.get("label"))} if isinstance(v, dict) else v for v in out["links"]]
    return out


def _normalise(raw: Any) -> dict[str, Any]:
    """Shape a browser result into a cue, or {} when it is unusable."""
    if not isinstance(raw, dict):
        return {}
    def _list(key: str, limit: int, minimum: int = 2) -> list[str]:
        values = raw.get(key) if isinstance(raw.get(key), list) else []
        out: list[str] = []
        for value in values:
            text = (" ".join(str(value or "").split()) if key == "fields" else strip_icon_words(value))[:80]
            if len(text) >= minimum and text not in out:
                out.append(text)
        return out[:limit]

    links = []
    for entry in (raw.get("links") or [])[:4]:
        if not isinstance(entry, dict):
            continue
        label = strip_icon_words(entry.get("label"))[:80]
        route = str(entry.get("route") or "").strip()
        if label and route.startswith("/"):
            links.append({"label": label, "route": route})

    regions = {}
    for name, value in (raw.get("regions") or {}).items():
        if name not in ("header", "nav", "main", "aside", "footer"):
            continue
        label = " ".join(str(value or "").split())[:48]
        if label:
            regions[name] = label

    types = {}
    for label, kind in (raw.get("types") or {}).items():
        if kind in ("email", "password", "tel", "number", "search", "url"):
            types[str(label)[:80]] = kind

    cue = {
        "headings": _list("headings", MAX_CUE_FIELDS),
        "fields": _list("fields", MAX_CUE_FIELDS),
        "buttons": _list("buttons", MAX_CUE_LABELS),
        "links": links,
        "regions": regions,
        "columns": _list("columns", MAX_CUE_FIELDS),
        "types": types,
        "observed": True,
    }
    return {key: value for key, value in cue.items() if value or key == "observed"}


def available() -> bool:
    """True when a browser can actually be driven. Cached after the first look."""
    global _AVAILABLE
    if _AVAILABLE is None:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401

            _AVAILABLE = True
        except Exception:
            logger.info("dom-cues: Playwright is not available; rendered cues are skipped")
            _AVAILABLE = False
    return bool(_AVAILABLE)


_AVAILABLE: bool | None = None


def read_route(
    base_url: str,
    route: str,
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    page: Any = None,
) -> dict[str, Any]:
    """The cue for one route, read from its rendered DOM. {} on any failure."""
    if not base_url or not route:
        return {}
    if page is None and not available():
        return {}
    url = urljoin(base_url.rstrip("/") + "/", route.lstrip("/"))
    owns_page = page is None
    browser = None
    try:
        if owns_page:
            from playwright.sync_api import sync_playwright

            browser = sync_playwright().start().chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(400)
        return _normalise(page.evaluate(_JS_READ))
    except Exception:
        logger.debug("dom-cues: could not read %s", url, exc_info=True)
        return {}
    finally:
        try:
            if owns_page and browser is not None:
                browser.close()
        except Exception:
            pass


def read_routes(
    base_url: str,
    routes: Iterable[str],
    *,
    limit: int = MAX_ROUTES,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> dict[str, dict[str, Any]]:
    """Cues for many routes on one browser. Stops at the first hard failure.

    A page budget and a single browser, because launching one per route is the
    difference between a scan and a hang.
    """
    if not available() or not base_url:
        return {}
    wanted = [str(r or "").strip() for r in routes if str(r or "").strip()][:limit]
    if not wanted:
        return {}

    out: dict[str, dict[str, Any]] = {}
    browser = None
    try:
        from playwright.sync_api import sync_playwright

        browser = sync_playwright().start().chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        for route in wanted:
            cue = read_route(base_url, route, timeout_ms=timeout_ms, page=page)
            if cue:
                out[route] = cue
    except Exception:
        logger.debug("dom-cues: rendered probe stopped early", exc_info=True)
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
    return out


def merge_into(cue: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    """Fill a source cue's gaps from a rendered one. Source wins where it spoke."""
    if not observed:
        return cue
    merged = dict(cue or {})
    for key in ("headings", "fields", "buttons", "columns"):
        have = list(merged.get(key) or [])
        for value in observed.get(key) or []:
            if value not in have:
                have.append(value)
        if have:
            merged[key] = have[:MAX_CUE_FIELDS]
    if not merged.get("links") and observed.get("links"):
        merged["links"] = list(observed["links"])
    if not merged.get("regions") and observed.get("regions"):
        merged["regions"] = dict(observed["regions"])
    types = dict(observed.get("types") or {})
    types.update(merged.get("types") or {})
    if types:
        merged["types"] = types
    merged["observed"] = True
    return merged
