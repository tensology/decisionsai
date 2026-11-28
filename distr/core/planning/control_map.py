"""Map each control on a captured page to the code behind it (read-only; never imports or runs project code).

Django (MTV): url name, view (file:line), template, and the Form class whose fields match a page form.
React: route-table row, page component, and the handler or link target on the element that carries the label.

Best effort, and honest about it: a control with no confident match gets no logic cell.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from distr.core.planning import flow_map

logger = logging.getLogger(__name__)

_SKIP = {"node_modules", ".git", "dist", "build", "__pycache__", ".next", "venv", ".venv", "migrations", "coverage", "static", "staticfiles",
         "media", "tests", "test", "e2e", "vendor", "site-packages", "tmp", "test-results", "artifacts", "output"}
_MAX_FILES = 2500
_FORM_CLASS = re.compile(r"^class\s+(\w+)\(([^)]*)\)\s*:", re.M)
_FORM_FIELD = re.compile(r"^\s{4}(\w+)\s*=\s*(?:forms\.|serializers\.)?\w*(?:Field|Choice|Boolean|Date|Time|File|Image|Email|Url|URL)\w*\(", re.M)
_TAG = re.compile(r"<(?:Link|NavLink|a|button|Button|form|input|IconButton|[A-Z]\w*Button)\b[^<>]*?(?:\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}[^<>]*?)*>", re.S)
_ATTR = re.compile(r"\b(onClick|onSubmit|onChange|to|href|action)\s*=\s*(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}|\"[^\"]*\"|'[^']*')", re.S)


def _walk(root: Path, suffixes: tuple[str, ...], limit: int = _MAX_FILES):
    stack, count = [root], 0
    while stack and count < limit:
        directory = stack.pop()
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in _SKIP and not entry.name.startswith("."):
                    stack.append(entry)
            elif entry.suffix.lower() in suffixes:
                count += 1
                yield entry
                if count >= limit:
                    return


def _read(path: Path, limit: int = 400_000) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            return stream.read(limit)
    except OSError:
        return ""


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


# ---------------------------------------------------------------------------- Django


def _django_forms(root: Path) -> list[dict[str, Any]]:
    out = []
    for path in _walk(root, (".py",)):
        if path.name not in ("forms.py",) and "form" not in path.stem.lower():
            continue
        text = _read(path)
        matches = list(_FORM_CLASS.finditer(text))
        for i, m in enumerate(matches):
            if "Form" not in m.group(2) and "Serializer" not in m.group(2):
                continue
            body = text[m.end(): matches[i + 1].start() if i + 1 < len(matches) else len(text)]
            fields = {f for f in _FORM_FIELD.findall(body)}
            meta = re.search(r"fields\s*=\s*\[([^\]]*)\]|fields\s*=\s*\(([^)]*)\)", body)
            if meta:
                fields |= set(re.findall(r"['\"](\w+)['\"]", meta.group(1) or meta.group(2) or ""))
            if fields:
                out.append({"name": m.group(1), "file": _rel(root, path), "line": _line_of(text, m.start()), "fields": fields})
    return out


def _django_views(root: Path, wanted: set[str]) -> dict[str, tuple[str, int]]:
    found: dict[str, tuple[str, int]] = {}
    if not wanted:
        return found
    pattern = re.compile(r"^(?:class|def)\s+(" + "|".join(re.escape(w) for w in wanted) + r")\b", re.M)
    for path in _walk(root, (".py",)):
        if path.name.startswith("test") or path.name in ("urls.py", "forms.py", "models.py"):
            continue
        text = _read(path)
        for m in pattern.finditer(text):
            found.setdefault(m.group(1), (_rel(root, path), _line_of(text, m.start())))
        if len(found) >= len(wanted):
            break
    return found


def _has_literal(pattern: str) -> bool:
    return any(part and not part.startswith(":") for part in pattern.split("/"))


def _template_text(root: Path, name: str) -> str:
    if not name:
        return ""
    for path in _walk(root, (".html", ".htm"), limit=1500):
        if path.as_posix().endswith("/" + name.lstrip("/")):
            return _read(path, 200_000).lower()
    return ""


def _django_map(root: Path, pages: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    from distr.core.planning import project_scan as ps

    try:
        model = ps.build_html_sitemap(root)
    except Exception:
        logger.debug("control_map: sitemap not built", exc_info=True)
        return {}
    nodes = [n for n in model.get("nodes") or [] if n.get("kind") == "page" and n.get("route")]
    if not nodes:
        return {}
    views = _django_views(root, {str(n.get("view")) for n in nodes if n.get("view")})
    forms = _django_forms(root)
    out: dict[str, dict[str, Any]] = {}
    for route, info in pages.items():
        hits = [n for n in nodes if ps._route_matches(n["route"], route) and (route == "/" or _has_literal(n["route"]))]
        if not hits:
            continue
        # Several apps can mount the same relative route: prefer the template that holds this page's own labels.
        texts = {n["id"]: _template_text(root, str(n.get("template") or "")) for n in hits}
        labels = [str(l.get("text")) for l in info["controls"].get("links") or [] if l.get("text") and len(str(l.get("text"))) > 3][:60]
        scores = {n["id"]: sum(1 for t in labels if t.lower() in texts[n["id"]]) for n in hits}
        ranked = sorted(hits, key=lambda n: -scores[n["id"]])
        node = ranked[0]
        ambiguous = len(hits) > 1 and (scores[ranked[0]["id"]] == 0 or scores[ranked[0]["id"]] == scores[ranked[1]["id"]])
        view = str(node.get("view") or "")
        unresolved = bool(labels) and bool(texts[node["id"]]) and scores[node["id"]] == 0
        if unresolved:
            # The best URL match renders a template that holds none of this page's links: do not claim it.
            node = {**node, "template": "", "url_name": ""}
            view = ""
        file_line = views.get(view)
        page = {"unresolved": "no URL pattern's template matches this page's links (address may be served by a catch-all, CMS or include the scan cannot follow)"} if unresolved else {}
        page |= {"url name": node.get("url_name") or "", "view": f"{view} ({file_line[0]}:{file_line[1]})" if file_line else view,
                "template": node.get("template") or "", "sign-in required": "yes" if node.get("auth") is True else ("no" if node.get("auth_known") else "")}
        if ambiguous:
            page["note"] = f"{len(hits)} URL patterns match this address and the templates do not settle which; best guess shown"
        mapped: dict[str, str] = {}
        for form in info["controls"].get("forms") or []:
            names = {f.get("name") for f in form.get("fields", []) if f.get("name")} - {"csrfmiddlewaretoken"}
            if not names:
                continue
            best = max(forms, key=lambda f: len(names & f["fields"]) / max(1, len(names | f["fields"])), default=None)
            score = len(names & best["fields"]) / max(1, len(names | best["fields"])) if best else 0
            label = (form.get("submit") or [form.get("id") or "form"])[0]
            if best and score >= 0.4:
                mapped[f"form:{label}"] = f"{best['name']} ({best['file']}:{best['line']})" + (f"; view {view}" if view else "")
            elif names <= {"username", "password"}:
                mapped[f"form:{label}"] = "django.contrib.auth AuthenticationForm (assumed)" + (f"; view {view}" if view else "")
            elif view:
                mapped[f"form:{label}"] = f"view {view}"
        for button in info["controls"].get("buttons") or []:
            idx = button.get("form", -1)
            if isinstance(idx, int) and idx >= 0 and button.get("text"):
                forms_list = info["controls"].get("forms") or []
                if idx < len(forms_list):
                    mapped[f"button:{button['text']}"] = mapped.get(f"form:{(forms_list[idx].get('submit') or [forms_list[idx].get('id') or 'form'])[0]}", f"view {view}" if view else "")
        out[route] = {"page": page, "controls": mapped, "stack": "django"}
    return out


# ---------------------------------------------------------------------------- React


def _label_index(root: Path) -> dict[str, list[str]]:
    """Visible label -> content keys that hold it (locale JSON files)."""
    index: dict[str, list[str]] = {}
    for path in _walk(root, (".json",), limit=80):
        if not re.search(r"content|locale|lang|i18n|english|translations?", path.as_posix(), re.I):
            continue
        try:
            data = json.loads(_read(path, 1_500_000))
        except ValueError:
            continue

        def visit(node, trail):
            if isinstance(node, dict):
                for k, v in node.items():
                    visit(v, trail + [str(k)])
            elif isinstance(node, str) and 2 <= len(node) <= 80:
                index.setdefault(node.strip().lower(), []).append(".".join(trail))
        visit(data, [])
    return index


def _handler_near(text: str, anchor: int) -> str:
    """The onClick/onSubmit/to/href on the tag that wraps the text at ``anchor``."""
    start = text.rfind("<", max(0, anchor - 500), anchor)
    best = ""
    while start != -1 and anchor - start < 500:
        m = _TAG.match(text, start)
        if m and m.end() <= anchor + 2:
            attrs = {k: v for k, v in _ATTR.findall(m.group(0))}
            bits = []
            for key in ("onClick", "onSubmit", "to", "href", "action"):
                if key in attrs:
                    bits.append(f"{key}={re.sub(r'\\s+', ' ', attrs[key]).strip('{}')[:70]}")
            if bits:
                return ", ".join(bits)
            best = best or ""
        start = text.rfind("<", max(0, start - 500), start) if start > 0 else -1
    return best


def _react_map(root: Path, pages: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    from distr.core.planning import project_scan as ps

    try:
        rows = ps.scan_frontend_routes(root)
    except Exception:
        logger.debug("control_map: route table not read", exc_info=True)
        return {}
    if not rows:
        return {}
    auth_flags: dict[str, bool] = {}
    for path in _walk(root, (".jsx", ".js", ".tsx", ".ts"), limit=400):
        if path.name.lower() in ("routes.jsx", "routes.js"):
            for m in re.finditer(r"url:\s*[\"']([^\"']+)[\"'][^}]*?auth:\s*(true|false)", _read(path)):
                auth_flags[m.group(1)] = m.group(2) == "true"
    labels = _label_index(root)
    out: dict[str, dict[str, Any]] = {}
    for route, info in pages.items():
        usable = [r for r in rows if r["url"] not in ("/:slug", "/*", "*") and (r["url"] == "/" or _has_literal(r["url"]))]
        row = next((r for r in usable if ps._route_matches(r["url"], route)), None)
        final = flow_map.norm((info["controls"].get("nav") or {}).get("final"))
        if row is None and final:
            row = next((r for r in usable if ps._route_matches(r["url"], final)), None)
        if row is None:
            continue
        file = ps._resolve_page_file(root, row.get("view") or "")
        text = _read(file) if file else ""
        sources = {_rel(root, file): text} if file else {}
        if file:
            for spec in re.findall(r"from\s+['\"](\.[^'\"]+)['\"]", text)[:30]:
                comp = ps._component_file(file, spec, root)
                if comp and comp != file and len(sources) < 12 and comp.suffix in (".jsx", ".tsx") and re.search(r"/(components|pages)/", comp.as_posix()):
                    sources[_rel(root, comp)] = _read(comp)
        page = {"route table": f"{row.get('source')} ({row['url']} → view {row.get('view')})", "component": _rel(root, file) if file else "",
                "sign-in required": "yes" if auth_flags.get(row["url"]) else ("no" if row["url"] in auth_flags else "")}
        mapped: dict[str, str] = {}
        controls = info["controls"]
        wanted = [("link", l.get("text")) for l in controls.get("links") or []] + [("button", b.get("text")) for b in controls.get("buttons") or []] \
            + [("form", (f.get("submit") or [f.get("id") or "form"])[0]) for f in controls.get("forms") or []]
        for kind, label in wanted:
            if not label or len(label) < 2 or f"{kind}:{label}" in mapped:
                continue
            keys = [k.rsplit(".", 1)[-1] for k in labels.get(label.strip().lower(), [])[:3] if len(k.rsplit(".", 1)[-1]) >= 4]
            literal = re.escape(label)
            for name, source in sources.items():
                hit = re.search(r">\s*" + literal + r"\s*<|>\s*" + literal + r"\s*\n|\blabel=[\"']" + literal + r"[\"']", source)
                hit = hit or next((m for k in keys if (m := re.search(r"\.(?:" + re.escape(k) + r")\b|[\"']" + re.escape(k) + r"[\"']", source))), None)
                if hit:
                    handler = _handler_near(source, hit.start())
                    mapped[f"{kind}:{label}"] = f"{name}:{_line_of(source, hit.start())}" + (f" — {handler}" if handler else "")
                    break
        out[route] = {"page": page, "controls": mapped, "stack": "react"}
    return out


def map_project(project_root: Path | str, pages: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{route: {page: {...}, controls: {"kind:label": logic}}} for every captured page the source explains."""
    root = Path(project_root)
    out: dict[str, dict[str, Any]] = {}
    frameworks = {(info["controls"].get("framework") or "") for info in pages.values()}
    if frameworks & {"react", "next", "vue"}:
        out.update(_react_map(root, pages))
    rest = {r: i for r, i in pages.items() if r not in out}
    if rest and (not frameworks <= {"react", "next", "vue"} or not out):
        out.update({r: m for r, m in _django_map(root, rest).items() if r not in out})
    return out
