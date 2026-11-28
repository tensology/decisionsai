"""How a site's pages connect, and what every control on each page does.

Built from what the capture browser saw (``controls``): navigation links, form posts, redirects and
sign-in gates. Pure functions over the saved page metadata; nothing here touches the network.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from distr.core.planning import controls as page_controls

_CHROME = {"nav", "header", "footer", "sidebar"}
_MAX_EDGES = 600


def norm(route: str) -> str:
    value = str(route or "").split("?")[0].split("#")[0].strip()
    if not value.startswith("/"):
        return ""
    return value if value == "/" else value.rstrip("/")


def captured_pages(project_root: Path | str) -> dict[str, dict[str, Any]]:
    """Controls, look and nav of every captured page, keyed by the route that was asked for."""
    out: dict[str, dict[str, Any]] = {}
    base = Path(project_root)
    for directory in (base / "planning", base / ".planning"):
        if not directory.is_dir() or directory.is_symlink():
            continue
        for meta in sorted(directory.glob("*.json")):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeError):
                continue
            if not isinstance(data, dict) or not data.get("url") or not isinstance(data.get("controls"), dict):
                continue
            clean = page_controls.sanitize(data["controls"])
            if not clean.get("nav", {}).get("requested") and not clean.get("links") and not clean.get("forms"):
                continue
            route = norm(urlparse(str(data["url"])).path or "/") or "/"
            out.setdefault(route, {"controls": clean, "style": data.get("style") if isinstance(data.get("style"), dict) else {}})
    return out


def describe(controls: dict[str, Any], route: str) -> list[dict[str, str]]:
    """One row per control: kind, label, where it sits, and what it does."""
    rows: list[dict[str, str]] = []
    forms = controls.get("forms") or []
    seen_forms: set[str] = set()
    for form in forms:
        names = ", ".join(f"{f.get('name') or f.get('label') or f.get('type')}{'*' if f.get('required') else ''}" for f in form.get("fields", [])[:10])
        sig = f"{form.get('method')}|{form.get('action')}|{names}"
        if sig in seen_forms:
            continue
        seen_forms.add(sig)
        action = form.get("action") or route
        extras = [x for x in ("CSRF token" if form.get("csrf") else "", "file upload" if form.get("multipart") else "") if x]
        where = form.get("in") or "body"
        verb = "Posts" if form.get("method") in ("POST", "PUT", "PATCH", "DELETE") else "Submits a GET query"
        if form.get("kind") not in ("route", "empty", None, "") and form.get("action"):
            action = f"{form.get('kind')}: {form.get('action')}"
        if not form.get("action") and (controls.get("framework") in ("react", "vue", "next")):
            what = f"Handled in the page's script (no action attribute); fields: {names or 'none'}"
        else:
            what = f"{verb} to {action}" + (f" ({'; '.join(extras)})" if extras else "") + f"; fields: {names or 'none'}"
        rows.append({"kind": "form", "label": (form.get("submit") or [form.get("id") or "form"])[0], "in": where, "does": what,
                     "target": norm(action) if isinstance(action, str) else ""})
    seen_links: set[str] = set()
    for link in controls.get("links") or []:
        key = f"{link.get('kind')}|{link.get('to')}|{link.get('text')}"
        if key in seen_links:
            continue
        seen_links.add(key)
        kind, to = link.get("kind"), link.get("to") or ""
        if kind == "route":
            does = f"Navigates to {to}" + (" in a new tab" if link.get("new_tab") else "")
        elif kind == "anchor":
            does = f"Scrolls to {to}"
        elif kind == "external":
            does = f"Leaves the site for {to or 'another site'}" + (" in a new tab" if link.get("new_tab") else "")
        elif kind in ("mailto", "tel"):
            does = "Opens the mail app" if kind == "mailto" else "Starts a phone call"
        else:
            does = "Script-driven: no address on the link" + (f" ({', '.join(link.get('hooks') or [])})" if link.get("hooks") else "")
        rows.append({"kind": "link", "label": link.get("text") or "(icon)", "in": link.get("in") or "body", "does": does,
                     "target": norm(to) if kind == "route" else ""})
    form_list = forms
    for button in controls.get("buttons") or []:
        text = button.get("text") or "(icon)"
        idx = button.get("form", -1)
        hooks = button.get("hooks") or []
        if isinstance(idx, int) and 0 <= idx < len(form_list) and button.get("type") in ("submit",):
            form = form_list[idx]
            does = f"Submits the form: {form.get('method')} {form.get('action') or route}"
        elif hooks:
            does = "Wired to: " + ", ".join(hooks[:4])
        elif button.get("type") == "submit":
            does = "Button without a form: click handler lives in the page's script"
        else:
            does = "Click handler lives in the page's script (not visible in the markup)"
        if button.get("disabled"):
            does += "; disabled until its conditions are met"
        rows.append({"kind": "button", "label": text, "in": button.get("in") or "body", "does": does, "target": ""})
    for field in controls.get("fields") or []:
        rows.append({"kind": "field", "label": field.get("label") or field.get("name") or field.get("type") or "field", "in": "body",
                     "does": f"{field.get('type')} input outside a form: read by the page's script", "target": ""})
    return rows


def _global_chrome(pages: dict[str, dict[str, Any]]) -> set[tuple[str, str]]:
    """Links in the site chrome that appear on most pages: drawn once, not once per page."""
    if len(pages) < 3:
        return set()
    counts: Counter = Counter()
    for info in pages.values():
        for key in {(norm(l.get("to")), l.get("text") or "") for l in info["controls"].get("links", []) if l.get("kind") == "route" and l.get("in") in _CHROME}:
            if key[0]:
                counts[key] += 1
    need = max(2, int(len(pages) * 0.6 + 0.5))
    return {key for key, n in counts.items() if n >= need}


def edges(pages: dict[str, dict[str, Any]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(page-to-page edges, site-wide navigation links). Kinds: nav, link, form, redirect, gate."""
    chrome = _global_chrome(pages)
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(src, dst, kind, label=""):
        if not src or not dst or (src == dst and kind in ("link", "nav")):
            return
        key = (src, dst, kind)
        if key in seen or len(out) >= _MAX_EDGES:
            return
        seen.add(key)
        out.append({"from": src, "to": dst, "kind": kind, "label": label[:60]})

    for route, info in sorted(pages.items()):
        c = info["controls"]
        nav = c.get("nav") or {}
        asked, final = norm(nav.get("requested")) or route, norm(nav.get("final"))
        if nav.get("redirected") and final and final != asked:
            first = (nav.get("redirects") or [{}])[0].get("status")
            add(asked, final, "gate" if nav.get("gate") else "redirect", f"{first} sign-in required" if nav.get("gate") == "login" and first else (f"HTTP {first}" if first else "client redirect"))
        for link in c.get("links") or []:
            if link.get("kind") != "route":
                continue
            target = norm(link.get("to"))
            if not target or (target, link.get("text") or "") in chrome:
                continue
            add(route, target, "nav" if link.get("in") in _CHROME else "link", link.get("text") or "")
        for form in c.get("forms") or []:
            target = norm(form.get("action")) if form.get("kind") in ("route", None) else ""
            target = target or route
            label = (form.get("submit") or [form.get("id") or "form"])[0]
            add(route, target, "form", f"{form.get('method')} {label}")
    shared = sorted(chrome)
    site_nav = []
    seen_nav = set()
    for target, text in shared:
        if (target, text) not in seen_nav:
            seen_nav.add((target, text))
            site_nav.append({"to": target, "label": text})
    return out, site_nav


def _mid(route: str) -> str:
    return "n_" + re.sub(r"[^A-Za-z0-9]", "_", route.strip("/") or "home")[:40]


def _q(text: str) -> str:
    return re.sub(r'["\\\n\r\x00-\x1f]', " ", str(text))[:60]


def mermaid(pages: dict[str, dict[str, Any]], project: str = "") -> str:
    """The flow map as a Mermaid flowchart: solid = links, thick = form posts, dotted = redirects, red dotted = sign-in gates."""
    es, site_nav = edges(pages)
    nodes = {r for r in pages} | {e["from"] for e in es} | {e["to"] for e in es}
    lines = ["flowchart LR"]
    for r in sorted(nodes):
        mark = "" if r in pages else " (not captured)"
        lines.append(f'    {_mid(r)}["{_q(r)}{mark}"]' + (":::gate" if any(e["from"] == r and e["kind"] == "gate" for e in es) else ""))
    if site_nav:
        lines.append(f'    site_nav(["Site navigation: {_q(", ".join(n["label"] or n["to"] for n in site_nav[:6]))}"])')
    arrows = {"nav": "-->", "link": "-->", "form": "==>", "redirect": "-.->", "gate": "-.->"}
    styles = []
    for i, e in enumerate(es):
        label = f"|{_q(e['kind'] + (': ' + e['label'] if e['label'] else ''))}|"
        lines.append(f"    {_mid(e['from'])} {arrows[e['kind']]}{label} {_mid(e['to'])}")
        if e["kind"] == "gate":
            styles.append(f"    linkStyle {i} stroke:#c0392b,stroke-dasharray:4")
    lines.append("    classDef gate stroke:#c0392b,stroke-width:2px")
    return "\n".join(lines + styles) + "\n"


def wire_lines(pages: dict[str, dict[str, Any]], project: str = "Site") -> list[str]:
    """The flow map as an Interfaces screen, so it sits beside the page stencils."""
    es, site_nav = edges(pages)
    if not es and not site_nav:
        return []
    q = lambda t: re.sub(r'["\\\x00-\x1f\x7f]', " ", str(t))[:70]
    lines = [f'screen "Flow map · {q(project)}" device=desktop layer=guidance', "  stack",
             f'    heading "How the pages connect"',
             '    text "Solid: links. Form: where a form posts. Redirect: where an address sends you. Gate: needs a sign-in first."']
    if site_nav:
        lines.append('    heading "Site navigation (on every page)"')
        for n in site_nav[:14]:
            lines.append(f'    link "{q(n["label"] or n["to"])}" route="{q(n["to"])}"')
    by_source = defaultdict(list)
    for e in es:
        by_source[e["from"]].append(e)
    for route in sorted(by_source):
        lines.append(f'    heading "{q(route)}"')
        for e in by_source[route][:30]:
            tag = {"nav": "nav", "link": "link", "form": "form", "redirect": "redirect", "gate": "sign-in gate"}[e["kind"]]
            label = f'{tag} · {e["label"]}' if e["label"] else tag
            lines.append(f'    link "{q(label)} → {q(e["to"])}" route="{q(e["to"])}"')
    return lines[:700]


def controls_markdown(route: str, controls: dict[str, Any], mapping: dict[str, Any] | None = None) -> str:
    """The page's controls as a table, mapped to its view logic where the source is known."""
    rows = describe(controls, route)
    if not rows:
        return ""
    nav = controls.get("nav") or {}
    out = ["## Controls (what each element does)", ""]
    if nav.get("redirected") and nav.get("final") and norm(nav["final"]) != route:
        out.append(f"- Address `{route}` {'is behind a sign-in and ' if nav.get('gate') == 'login' else ''}ends at `{nav['final']}`"
                   + (f" (first response {nav['redirects'][0]['status']})" if nav.get("redirects") else "") + ".")
    if nav.get("gate") == "denied":
        out.append(f"- Address `{route}` answers {nav.get('status')}: access denied without signing in.")
    if mapping:
        bits = [f"{k}: `{v}`" for k, v in mapping.get("page", {}).items() if v]
        if bits:
            out.append("- Page logic: " + "; ".join(bits))
    if len(out) > 2:
        out.append("")
    out += ["| Control | Label | Where | Behaviour | Logic |", "|---|---|---|---|---|"]
    mapped = (mapping or {}).get("controls") or {}
    for i, row in enumerate(rows[:120]):
        logic = mapped.get(f"{row['kind']}:{row['label']}") or ""
        cell = lambda t: str(t).replace("|", "/").replace("\n", " ")[:160]
        out.append(f"| {row['kind']} | {cell(row['label'])} | {row['in']} | {cell(row['does'])} | {cell(logic)} |")
    if len(rows) > 120:
        out.append(f"\n_{len(rows) - 120} more controls not listed._")
    return "\n".join(out) + "\n"


def project_style(pages: dict[str, dict[str, Any]]) -> dict[str, Any]:
    from distr.core.planning import base_style

    return base_style.aggregate([info.get("style") or {} for info in pages.values()])
