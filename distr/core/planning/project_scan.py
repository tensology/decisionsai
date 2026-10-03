"""Deterministic project → plan builders for any linked project folder.

Same pipeline for every board. Stdlib only. No project-specific shortcuts.

Flow: inventory → render → assess → deepen → repeat until adequate or
max passes. One-shot dumps are not enough; each pass must close real gaps
in wireframes, ERD, and requirements.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROUTE_BLOCK = re.compile(r"\{[^{}]+\}", re.DOTALL)
_ROUTE_PATH = re.compile(
    r"""(?:path|re_path)\(\s*[f]?[r]?[u]?[b]?['\"]([^'\"]+)['\"]""",
)
_CLASS = re.compile(r"^class\s+(\w+)\s*\(([^)]*)\)\s*:", re.MULTILINE)
_FK = re.compile(
    r"^\s*(\w+)\s*=\s*models\.(ForeignKey|OneToOneField|ManyToManyField)\(\s*[\"']?([\w\.]+)",
    re.MULTILINE,
)
_SCALAR = re.compile(r"^\s*(\w+)\s*=\s*models\.(\w+)\(", re.MULTILINE)
_USESTATE_OBJ = re.compile(r"useState\(\s*\{([^}]+)\}", re.DOTALL)
_LABEL = re.compile(
    r"""(?:label|placeholder|aria-label)=\{?["']([^"']{2,40})["']\}?"""
    r"""|>([A-Z][^<>{]{1,40})</(?:label|Label|Button|button)>""",
    re.IGNORECASE,
)
_BUTTON_TEXT = re.compile(
    r"""<(?:button|Button)[^>]*>\s*([^<{]{2,40})\s*</(?:button|Button)>"""
    r"""|["'](?:submit|label|children)["']\s*:\s*["']([^"']{2,40})["']""",
    re.IGNORECASE,
)
_SKIP_DIR = {
    ".git",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".next",
    "dist",
    "build",
    "vendors",
    "migrations",
    "planning",
    "media",
    "staticfiles",
    "tmp",
    "venv",
    ".venv",
    "site-packages",
}
_SKIP_APPS = {"woocommerce", "translatable"}
_MODEL_BASE = re.compile(r"\b(Model|MPTTModel|TranslatableModel)\b")
_REL_KINDS = {"ForeignKey", "OneToOneField", "ManyToManyField"}
_MERMAID_TYPE = {
    "CharField": "string",
    "TextField": "string",
    "SlugField": "string",
    "EmailField": "string",
    "URLField": "string",
    "UUIDField": "string",
    "JSONField": "string",
    "FileField": "string",
    "ImageField": "string",
    "IntegerField": "int",
    "PositiveIntegerField": "int",
    "SmallIntegerField": "int",
    "BigIntegerField": "int",
    "BooleanField": "bool",
    "NullBooleanField": "bool",
    "DecimalField": "float",
    "FloatField": "float",
    "DateField": "date",
    "DateTimeField": "date",
    "TimeField": "date",
}
_PRIMARY_GROUPS = {"Core", "Account", "Commerce", "Auction", "Sport", "Logistics", "Ops", "Pages"}
_GROUP_RANK = {
    "Auction": 0,
    "Commerce": 1,
    "Logistics": 2,
    "Sport": 3,
    "Account": 4,
    "Ops": 5,
    "Core": 6,
    "Pages": 7,
    "Support": 8,
    "Blog": 9,
}
_AUTH_FIELD_TOKENS = {"email", "password", "username", "firstname", "lastname", "rememberme", "confirmpassword"}
_AUTH_ENTITY_HINTS = {"user", "profile", "account", "customer", "buyer", "seller", "member"}
_MAX_DEEP_SCREENS = 36
_MAX_MODEL_FIELDS = 8
_MAX_WIRE_CHARS = 95_000
_SUBMIT_LITERAL = re.compile(
    r"""type=["']submit["'][\s\S]{0,240}?['\"]([A-Za-z][^'\"]{1,40})['\"]""",
    re.IGNORECASE,
)


_DISMISS_LABELS = {"x", "×", "✕", "✖", "close", "dismiss"}
_FIELD_OPEN = re.compile(
    r"<(LabelText|TextField|OutlinedInput|textarea|select|input)\b([^>]*)>",
    re.IGNORECASE | re.DOTALL,
)
_ATTR_VALUE = re.compile(
    r"""\b(label|name|placeholder|type|aria-label)\s*=\s*(?:\{)?["']([^"']{1,80})["']""",
    re.IGNORECASE,
)
_HEADING_TEXT = re.compile(r"<h[1-3]\b[^>]*>\s*([^<{][^<]{1,80})", re.IGNORECASE)
_REGION_BLOCK = re.compile(r"<(header|footer)\b[^>]*>([\s\S]{0,500}?)</\1>", re.IGNORECASE)
_HERO_MARK = re.compile(
    r"""className\s*=\s*["'][^"']*\bhero\b[^"']*["'][^>]*>\s*([^<]{1,80})""",
    re.IGNORECASE,
)
_ACTION_TEXT = re.compile(
    r"<(button|Button|LongButton|SubmitButton)\b[^>]*>([\s\S]{0,160}?)</\1>",
    re.IGNORECASE,
)
_LOCAL_LINK = re.compile(
    r"""<(?:a|DefaultLink|Link)\b([^>]*)>([^<{][^<]{0,60})</(?:a|DefaultLink|Link)>""",
    re.IGNORECASE,
)
_IMPORT_DEFAULT = re.compile(r"""import\s+([A-Za-z_]\w*)\s+from\s+['"](\.[^'"]+)['"]""")
_JSX_TAG = re.compile(r"<([A-Z][A-Za-z0-9]*)\b")
_SKIP_COMPONENT = re.compile(
    r"^(?:Snackbar|Alert|Dialog|Modal|Loading\w*|Error\w*|Icon\w*|Box|Grid|Typography)$"
)


def _iter_files(root: Path, names: tuple[str, ...], *, limit: int = 40) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name not in names:
            continue
        parts = path.relative_to(root).parts
        if any(part in _SKIP_DIR or part.startswith(".") for part in parts):
            continue
        found.append(path)
        if len(found) >= limit:
            break
    return found


def _iter_suffix(root: Path, suffixes: tuple[str, ...], *, limit: int = 200) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        parts = path.relative_to(root).parts
        if any(part in _SKIP_DIR or part.startswith(".") for part in parts):
            continue
        found.append(path)
        if len(found) >= limit:
            break
    return found


def _title(name: str) -> str:
    clean = re.sub(r"[_\-]+", " ", str(name or "").strip())
    return clean[:1].upper() + clean[1:] if clean else "Screen"


def _quote(value: str) -> str:
    return str(value or "").replace('"', "'")


def _group_for_route(url: str, name: str) -> str:
    path = (url or "").split("?")[0]
    token = f"{path} {name}".lower()
    if path in {"/", ""}:
        return "Core"
    if any(key in token for key in ("login", "logout", "register", "account", "auth", "profile", "password", "activate")):
        return "Account"
    if any(key in token for key in ("auction", "bid", "lot", "hammer", "reserve")):
        return "Auction"
    if any(key in token for key in ("fixture", "match", "league", "sport", "team", "player", "score", "tournament")):
        return "Sport"
    if any(key in token for key in ("track", "shipment", "consign", "delivery", "courier", "parcel", "logistics", "fleet")):
        return "Logistics"
    if any(key in token for key in ("shop", "catalogue", "catalog", "basket", "cart", "checkout", "order", "return", "quote", "voucher", "product")):
        return "Commerce"
    if "blog" in token:
        return "Blog"
    if any(key in token for key in ("support", "faq", "contact", "privacy", "search", "help")):
        return "Support"
    if any(key in token for key in ("admin", "staff", "ops", "dashboard")):
        return "Ops"
    return "Pages"


def _parse_route_block(block: str) -> dict[str, str] | None:
    url_m = re.search(r"""url:\s*['\"]([^'\"]+)['\"]""", block)
    name_m = re.search(r"""name:\s*['\"]([^'\"]+)['\"]""", block)
    if not url_m or not name_m:
        return None
    view_m = re.search(r"""view:\s*['\"]([^'\"]+)['\"]""", block)
    return {
        "url": url_m.group(1),
        "name": name_m.group(1),
        "view": view_m.group(1) if view_m else "",
    }


def scan_frontend_routes(project_root: Path) -> list[dict[str, str]]:
    """Parse common React/JS route tables into ordered route rows."""
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in _iter_files(project_root, ("routes.jsx", "routes.js", "Routes.jsx", "Routes.js")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for block in _ROUTE_BLOCK.findall(text):
            parsed = _parse_route_block(block)
            if not parsed:
                continue
            url, name = parsed["url"], parsed["name"]
            if url in {"*", ""} or url in seen:
                continue
            if url.startswith("/api") or url.startswith("^"):
                continue
            seen.add(url)
            rows.append(
                {
                    "url": url,
                    "name": name,
                    "view": parsed.get("view") or "",
                    "title": _title(name),
                    "group": _group_for_route(url, name),
                    "source": str(path.relative_to(project_root)),
                }
            )
            if len(rows) >= 80:
                return rows
    if rows:
        return rows
    for path in _iter_files(project_root, ("urls.py",), limit=20):
        text = path.read_text(encoding="utf-8", errors="replace")
        for url in _ROUTE_PATH.findall(text):
            clean = "/" + url.lstrip("^/").rstrip("$")
            clean = clean.replace("//", "/")
            if clean in seen or clean in {"/", ""}:
                continue
            if "admin" in clean or clean.startswith("/api"):
                continue
            if "<" in clean and clean.count("/") > 4:
                continue
            seen.add(clean)
            name = clean.strip("/").replace("/", "_") or "home"
            rows.append(
                {
                    "url": clean if clean.startswith("/") else f"/{clean}",
                    "name": name,
                    "view": "",
                    "title": _title(name),
                    "group": _group_for_route(clean, name),
                    "source": str(path.relative_to(project_root)),
                }
            )
            if len(rows) >= 60:
                break
        if len(rows) >= 60:
            break
    return rows


def scan_django_models(project_root: Path, *, with_fields: bool = False) -> list[dict[str, Any]]:
    """Parse Django model classes and relations without importing Django."""
    entities: dict[str, dict[str, Any]] = {}
    candidates = _iter_files(project_root, ("models.py",), limit=80)
    candidates.sort(
        key=lambda path: (
            0 if "apps" in path.parts else 1,
            1 if any(part in _SKIP_APPS for part in path.parts) else 0,
            str(path),
        )
    )
    for path in candidates:
        if any(part in _SKIP_APPS for part in path.parts):
            continue
        app = path.parent.name
        text = path.read_text(encoding="utf-8", errors="replace")
        class_spans = [(match.start(), match.group(1), match.group(2)) for match in _CLASS.finditer(text)]
        for index, (start, name, bases) in enumerate(class_spans):
            if not _MODEL_BASE.search(bases):
                continue
            if name.startswith("_") or name.endswith("Manager"):
                continue
            end = class_spans[index + 1][0] if index + 1 < len(class_spans) else len(text)
            body = text[start:end]
            entity = entities.setdefault(
                name,
                {
                    "name": name,
                    "app": app,
                    "fields": [],
                    "relations": [],
                    "source": str(path.relative_to(project_root)),
                },
            )
            for field, kind, target in _FK.findall(body):
                target_name = target.rsplit(".", 1)[-1]
                if target_name == "self":
                    target_name = name
                entity["relations"].append({"field": field, "kind": kind, "target": target_name})
            if with_fields:
                seen_fields = {row["name"] for row in entity["fields"]}
                for field, kind in _SCALAR.findall(body):
                    if kind in _REL_KINDS or field in seen_fields:
                        continue
                    if field in {"objects", "DoesNotExist", "MultipleObjectsReturned"}:
                        continue
                    mermaid = _MERMAID_TYPE.get(kind)
                    if not mermaid:
                        continue
                    entity["fields"].append({"name": field, "kind": kind, "mermaid": mermaid})
                    seen_fields.add(field)
                    if len(entity["fields"]) >= _MAX_MODEL_FIELDS:
                        break
        if len(entities) >= 80:
            break
    return list(entities.values())


def _readme_excerpt(project_root: Path, limit: int = 2500) -> str:
    for name in ("README.md", "README", "readme.md", "AGENTS.md"):
        path = project_root / name
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")[:limit].strip()
    return ""


def _pages_roots(project_root: Path) -> list[Path]:
    roots: list[Path] = []
    for candidate in (
        project_root / "frontend" / "src" / "pages",
        project_root / "frontend" / "pages",
        project_root / "src" / "pages",
        project_root / "web" / "pages",
    ):
        if candidate.is_dir():
            roots.append(candidate)
    if roots:
        return roots
    for path in project_root.rglob("pages"):
        if not path.is_dir():
            continue
        parts = path.relative_to(project_root).parts
        if any(part in _SKIP_DIR for part in parts):
            continue
        roots.append(path)
        if len(roots) >= 4:
            break
    return roots


def _resolve_page_file(project_root: Path, view: str) -> Path | None:
    if not view:
        return None
    needle = view.replace("\\", "/").strip("/").lower()
    variants = {
        needle,
        needle.replace("/", "_"),
        needle.split("/")[-1],
    }
    for pages_root in _pages_roots(project_root):
        for path in _iter_suffix(pages_root, (".tsx", ".jsx", ".js", ".ts"), limit=400):
            rel = path.relative_to(pages_root).with_suffix("").as_posix().lower()
            flat = rel.replace("/", "_")
            stem = path.stem.lower()
            if rel in variants or flat in variants or stem == needle.split("/")[-1]:
                return path
    return None


def _clean_cue_text(value: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", value or "")
    cleaned = (
        cleaned.replace("&amp;", "&")
        .replace("&nbsp;", " ")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def _extract_screen_cues(source: str) -> dict[str, Any]:
    """Controls, headings, and regions that are actually in this source file."""
    fields: list[str] = []
    types: dict[str, str] = {}
    buttons: list[str] = []
    headings: list[str] = []
    links: list[dict[str, str]] = []
    regions: dict[str, str] = {}

    for match in _FIELD_OPEN.finditer(source):
        attrs = {key.lower(): value for key, value in _ATTR_VALUE.findall(match.group(2))}
        label = (attrs.get("label") or "").strip()
        name = (attrs.get("name") or "").strip()
        placeholder = (attrs.get("placeholder") or "").strip()
        kind = (attrs.get("type") or "").lower()
        if kind not in {"email", "password", "number", "tel", "search", "url"} and name.lower() in {"email", "password", "tel", "search", "url", "number"}:
            kind = name.lower()
        shown = label or (_title(name) if name else "")
        if not shown and placeholder and _usable_label(placeholder) and "@" not in placeholder and "•" not in placeholder:
            shown = placeholder
        if not shown or not _usable_label(shown):
            continue
        if shown not in fields:
            fields.append(shown)
        if kind in {"email", "password", "number", "tel", "search", "url"}:
            types.setdefault(shown, kind)

    for match in _HEADING_TEXT.finditer(source):
        value = _clean_cue_text(match.group(1))
        if value and "{" not in value and _usable_label(value) and value not in headings:
            headings.append(value)

    for match in _ACTION_TEXT.finditer(source):
        value = _clean_cue_text(match.group(2))
        if not value or len(value) < 2 or value.lower() in _DISMISS_LABELS:
            continue
        if value not in buttons and _usable_label(value):
            buttons.append(value)

    for match in _LOCAL_LINK.finditer(source):
        label = _clean_cue_text(match.group(2))
        if not _usable_label(label) or label.lower() in _DISMISS_LABELS:
            continue
        route_m = re.search(r"""(?:\bto|\bhref)\s*=\s*(?:\{)?["'](/[^"'#?\s]*)["']""", match.group(1) or "")
        route = route_m.group(1) if route_m else ""
        if any(link["label"] == label for link in links):
            continue
        links.append({"label": label, "route": route})

    for kind, body in _REGION_BLOCK.findall(source):
        inner = _clean_cue_text(body)
        label = inner[:48] if inner and _usable_label(inner[:40]) else kind.title()
        regions.setdefault(kind.lower(), label if _usable_label(label) else kind.title())
    hero = _HERO_MARK.search(source)
    if hero:
        inner = _clean_cue_text(hero.group(1))
        regions.setdefault("hero", inner[:48] if inner and _usable_label(inner[:40]) else "Hero")

    return {
        "fields": fields[:8],
        "types": types,
        "buttons": buttons[:6],
        "headings": headings[:8],
        "links": links[:4],
        "regions": regions,
    }


def _cue_has_content(cue: dict[str, Any]) -> bool:
    return bool(cue.get("fields") or cue.get("buttons") or cue.get("headings") or cue.get("links") or cue.get("regions"))


def _merge_cues(base: dict[str, Any], extra: dict[str, Any]) -> None:
    for key in ("fields", "buttons", "headings"):
        for value in extra.get(key) or []:
            if value not in base[key]:
                base[key].append(value)
    for label, kind in (extra.get("types") or {}).items():
        base["types"].setdefault(label, kind)
    for link in extra.get("links") or []:
        if not any(row["label"] == link["label"] for row in base["links"]):
            base["links"].append(link)
    for kind, label in (extra.get("regions") or {}).items():
        base["regions"].setdefault(kind, label)


def _component_file(page: Path, spec: str, project_root: Path) -> Path | None:
    """Resolve one relative import. Never walks outside the linked project."""
    if not spec.startswith("."):
        return None
    raw = (page.parent / spec).resolve()
    try:
        raw.relative_to(project_root.resolve())
    except ValueError:
        return None
    suffixes = (".tsx", ".jsx", ".ts", ".js")
    candidates = [raw]
    if raw.suffix.lower() not in suffixes:
        candidates.extend(raw.with_suffix(ext) for ext in suffixes)
        candidates.extend(raw / f"index{ext}" for ext in suffixes)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def scan_page_cues(project_root: Path, routes: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    """Extract form, button, and section cues from the page each route names.

    A thin page may render one local component (for example a contact block).
    That file is read once. Toast state and dismiss buttons are not fields.
    """
    cues: dict[str, dict[str, Any]] = {}
    root = project_root.resolve()
    for row in routes:
        page = _resolve_page_file(project_root, row.get("view") or "")
        if page is None and row.get("name"):
            page = _resolve_page_file(project_root, row["name"])
        if page is None:
            continue
        source = page.read_text(encoding="utf-8", errors="replace")[:80_000]
        cue = _extract_screen_cues(source)
        if not _cue_has_content(cue):
            used = set(_JSX_TAG.findall(source))
            followed = 0
            for name, spec in _IMPORT_DEFAULT.findall(source):
                if name not in used or _SKIP_COMPONENT.search(name):
                    continue
                child = _component_file(page, spec, root)
                if child is None or child == page:
                    continue
                child_text = child.read_text(encoding="utf-8", errors="replace")[:80_000]
                _merge_cues(cue, _extract_screen_cues(child_text))
                followed += 1
                if followed >= 2 or _cue_has_content(cue):
                    break
        if not _cue_has_content(cue):
            continue
        cue["fields"] = cue["fields"][:8]
        cue["buttons"] = cue["buttons"][:6]
        cue["headings"] = cue["headings"][:8]
        cue["links"] = cue["links"][:4]
        cue["path"] = str(page.relative_to(project_root))
        cues[row["url"]] = cue
    return cues


def _usable_label(value: str) -> bool:
    text = str(value or "").strip()
    if not text or len(text) >= 40:
        return False
    if re.search(r"[{}<>/=]", text):
        return False
    if re.search(r"\b(?:w|h|min|max|p|m|px|py|pt|pb|gap|text|bg|flex|grid|animate)-\S+", text):
        return False
    if re.fullmatch(r"[\w-]+", text) and "-" in text and text.lower() not in {"sign-in", "log-in", "buy-now"}:
        return False
    return True


def _bind_field(field_name: str, models: list[dict[str, Any]], *, route_name: str = "") -> str | None:
    token = re.sub(r"[^a-z0-9]", "", field_name.lower())
    if not token or len(token) < 2:
        return None
    route_token = re.sub(r"[^a-z0-9]", "", route_name.lower())
    candidates: list[tuple[int, str]] = []
    for entity in models:
        ename = entity["name"].lower()
        for field in entity.get("fields") or []:
            fname = re.sub(r"[^a-z0-9]", "", field["name"].lower())
            if not fname:
                continue
            # Exact field names only. A 4-character prefix bound "open" to ShopSettings.openai_key.
            if token != fname:
                continue
            score = 0
            if token in _AUTH_FIELD_TOKENS:
                if any(hint in ename for hint in _AUTH_ENTITY_HINTS):
                    score -= 20
                else:
                    score += 40
            if token in {"company", "phone", "reference", "status"} and ename in {"creditpurchase", "emaillogger", "ledger"}:
                score += 15
            if route_token and (ename in route_token or route_token in ename):
                score -= 25
            if any(hint in ename for hint in (token, token[:4])):
                score -= 3
            if route_token and any(noise in ename for noise in ("blog", "emailtemplate", "banner", "mailer")):
                if not (ename in route_token or route_token in ename):
                    score += 20
            candidates.append((score, f"{entity['name']}.{field['name']}"))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row[0], row[1]))
    best_score, best = candidates[0]
    if token in _AUTH_FIELD_TOKENS and best_score >= 20:
        return None
    if best_score >= 15:
        return None
    return best


def _primary_routes(routes: list[dict[str, str]], *, limit: int = 14) -> list[dict[str, str]]:
    ranked = sorted(
        routes,
        key=lambda row: (_GROUP_RANK.get(row["group"], 50), 0 if row["name"] in {"login", "register", "home"} else 1, row["url"]),
    )
    return ranked[:limit]


def build_brief(
    *,
    project_name: str,
    project_root: Path,
    routes: list[dict[str, str]],
    models: list[dict[str, Any]],
    cues: dict[str, dict[str, Any]] | None = None,
) -> str:
    groups: dict[str, list[str]] = {}
    for row in routes:
        groups.setdefault(row["group"], []).append(f"`{row['url']}` ({row['title']})")
    apps = sorted({row["app"] for row in models})
    readme = _readme_excerpt(project_root)
    top_models = sorted(models, key=lambda row: (-len(row.get("fields") or []), row["name"]))[:12]
    lines = [
        "# Outcome brief",
        "",
        "## Outcome",
        "",
        f"Operate and evolve **{project_name}** from the linked codebase at `{project_root}`.",
        "",
        "## Product surfaces observed",
        "",
    ]
    if groups:
        for group, items in groups.items():
            lines.append(f"### {group}")
            lines.extend(f"- {item}" for item in items[:14])
            lines.append("")
    else:
        lines.extend(["- No route table was detected in this project yet.", ""])
    lines.extend(
        [
            "## Domain models observed",
            "",
            f"- {len(models)} model classes across: {', '.join(f'`{app}`' for app in apps) or 'none detected'}",
            "",
        ]
    )
    if top_models and any(row.get("fields") for row in top_models):
        lines.append("### Key entities")
        for entity in top_models:
            if not entity.get("fields"):
                continue
            field_names = ", ".join(f"`{field['name']}`" for field in entity["fields"][:6])
            lines.append(f"- `{entity['name']}` (`{entity['app']}`): {field_names}")
        lines.append("")
    if cues:
        lines.extend(
            [
                "## UI surfaces inspected",
                "",
                f"- {len(cues)} route pages resolved to source components with form/button cues",
                "",
            ]
        )
    lines.extend(
        [
            "## Boundaries",
            "",
            "- In scope: journeys and domain objects present in first-party routes and models",
            "- Out of scope for this scan: inventing features, vendor dumps, and generated assets",
            "",
        ]
    )
    if readme:
        lines.extend(["## Project notes", "", readme, ""])
    return "\n".join(lines).rstrip() + "\n"


def build_prd(*, project_name: str, routes: list[dict[str, str]], models: list[dict[str, Any]] | None = None) -> str:
    lines = [
        "# Product requirements",
        "",
        "## Outcome",
        "",
        f"Deliver the live journeys for **{project_name}** as defined by the current application routes and domain models.",
        "",
        "## Users",
        "",
        "- End customer / visitor",
        "- Authenticated account holder",
        "- Staff / ops where admin or nested account tooling exists",
        "",
        "## Requirements",
        "",
    ]
    for index, row in enumerate(routes[:45], start=1):
        lines.append(f"- FR-{index:03d}: Support `{row['url']}` ({row['title']}, {row['group']})")
    if not routes:
        lines.append("- FR-001: Inventory the primary user journeys once routes are available.")
    if models:
        domain_lines = []
        for entity in sorted(models, key=lambda row: row["name"])[:20]:
            fields = entity.get("fields") or []
            if not fields:
                continue
            required = ", ".join(f"`{field['name']}`" for field in fields[:5])
            domain_lines.append(f"- Persist `{entity['name']}` with fields including {required}")
        if domain_lines:
            lines.extend(["", "## Domain constraints", ""])
            lines.extend(domain_lines)
    lines.extend(["", "## Open questions", "", "- Which routes are deprecated vs actively used?", ""])
    return "\n".join(lines)


def build_frac(
    *,
    routes: list[dict[str, str]],
    models: list[dict[str, Any]] | None = None,
    cues: dict[str, dict[str, Any]] | None = None,
) -> str:
    primary = _primary_routes(routes, limit=14)
    lines = ["# Functional requirements and acceptance criteria", ""]
    if not primary:
        lines.extend(
            [
                "## FR-001",
                "",
                "Capture acceptance criteria after the first project scan finds routes.",
                "",
                "- AC-001.1: Given a linked project with routes, when Plan scans, then FRAC entries are generated.",
                "",
            ]
        )
        return "\n".join(lines)
    models = models or []
    cues = cues or {}
    for index, row in enumerate(primary, start=1):
        cue = cues.get(row["url"]) or {}
        lines.extend(
            [
                f"## FR-{index:03d}",
                "",
                f"User can complete **{row['title']}** via `{row['url']}`.",
                "",
                f"- AC-{index:03d}.1: Given a visitor opens `{row['url']}`, when the page loads, then the {row['title']} experience is available without a server error.",
            ]
        )
        ac = 2
        for field in (cue.get("fields") or [])[:4]:
            bind = _bind_field(field, models, route_name=row["name"])
            detail = f" bound to `{bind}`" if bind else ""
            lines.append(
                f"- AC-{index:03d}.{ac}: Given the {row['title']} form, when the user provides `{_title(field)}`{detail}, then the value is accepted by the page."
            )
            ac += 1
        for button in (cue.get("buttons") or [])[:2]:
            lines.append(
                f"- AC-{index:03d}.{ac}: Given the {row['title']} page, when the user activates `{button}`, then the expected navigation or submit action runs."
            )
            ac += 1
        if not cue and row["group"] in {"Auction", "Commerce", "Logistics", "Sport"}:
            token = row["name"].lower()
            for entity in models:
                if entity["name"].lower() in token or token in entity["name"].lower():
                    if entity.get("fields"):
                        field = entity["fields"][0]["name"]
                        lines.append(
                            f"- AC-{index:03d}.{ac}: Given `{entity['name']}` data exists, when `{row['url']}` renders, then `{entity['name']}.{field}` is represented on the page."
                        )
                    break
        lines.append("")
    return "\n".join(lines)


def build_erd(models: list[dict[str, Any]]) -> str:
    if not models:
        return (
            "erDiagram\n"
            "    NO_MODELS_DETECTED {\n"
            "        string note\n"
            "    }\n"
        )
    lines = ["erDiagram"]
    for entity in sorted(models, key=lambda row: (row["app"], row["name"]))[:60]:
        safe = re.sub(r"[^A-Za-z0-9_]", "", entity["name"]) or "Entity"
        app = re.sub(r"[^A-Za-z0-9_]", "", entity["app"]) or "app"
        lines.append(f"    {safe} {{")
        fields = entity.get("fields") or []
        if fields:
            for field in fields[:_MAX_MODEL_FIELDS]:
                fname = re.sub(r"[^A-Za-z0-9_]", "", field["name"]) or "field"
                lines.append(f"        {field['mermaid']} {fname}")
        else:
            lines.append(f"        string app_{app}")
        lines.append("    }")
    known = {re.sub(r"[^A-Za-z0-9_]", "", row["name"]) for row in models}
    edges: set[str] = set()
    for entity in models:
        owner = re.sub(r"[^A-Za-z0-9_]", "", entity["name"])
        for rel in entity["relations"]:
            target = re.sub(r"[^A-Za-z0-9_]", "", rel["target"])
            if target not in known:
                continue
            kind = rel["kind"]
            field = re.sub(r"[^A-Za-z0-9_]", "", rel["field"]) or "rel"
            if kind == "ManyToManyField":
                edge = f"    {owner} }}o--o{{ {target} : {field}"
            elif kind == "OneToOneField":
                edge = f"    {owner} ||--|| {target} : {field}"
            else:
                edge = f"    {owner} }}o--|| {target} : {field}"
            edges.add(edge)
            if len(edges) >= 140:
                break
        if len(edges) >= 140:
            break
    lines.extend(sorted(edges))
    return "\n".join(lines) + "\n"


def _emit_links(screen: list[str], links: list[dict[str, str]]) -> None:
    for link in links[:4]:
        label = _quote(link.get("label") or "")
        route = str(link.get("route") or "")
        if route.startswith("/") and " " not in route and "*" not in route:
            screen.append(f'    link "{label}" route="{_quote(route)}"')
        elif label:
            screen.append(f'    link "{label}"')


def _screen_lines(
    row: dict[str, str],
    *,
    models: list[dict[str, Any]],
    cues: dict[str, dict[str, Any]],
    fr_id: str | None,
) -> list[str]:
    """One route screen from that route's own template cues.

    Missing cues stay an empty state. Sibling routes are not borrowed, and
    synthetic model fields are not drawn as if they were the page.
    """
    cue = dict(cues.get(row["url"]) or {})
    if cue.get("synthetic"):
        cue = {}
    title = _quote(row["title"])
    screen = [
        f'screen "{title}" device=desktop route="{_quote(row["url"])}"',
        "  stack",
    ]
    regions = cue.get("regions") or {}
    if regions.get("header"):
        screen.append(f'    header "{_quote(str(regions["header"])[:48])}"')
    if regions.get("hero"):
        screen.append(f'    hero "{_quote(str(regions["hero"])[:48])}"')
    screen.append(f'    heading "{title}"')
    screen.append(f'    text "Route {_quote(row["url"])} · group {_quote(row["group"])}"')
    fields = list(cue.get("fields") or [])[:6]
    buttons = list(cue.get("buttons") or [])[:3]
    headings: list[str] = []
    for heading in cue.get("headings") or []:
        if heading.lower() == str(row["title"]).lower():
            continue
        headings.append(heading)
        if len(headings) >= 6:
            break
    types = cue.get("types") or {}
    links = list(cue.get("links") or [])
    if fields:
        screen.append(f'    form "{title}"')
        for field in fields:
            bind = _bind_field(field, models, route_name=row["name"])
            kind = types.get(field) or ""
            extra = f" type={kind}" if kind else ""
            bind_attr = f" bind={bind}" if bind else ""
            screen.append(f'      input "{_quote(field)}"{extra}{bind_attr}')
        if buttons:
            req = f" requirement={fr_id}" if fr_id else ""
            screen.append(f'      button "{_quote(buttons[0])}" variant=primary{req}')
            for button in buttons[1:3]:
                screen.append(f'      button "{_quote(button)}"')
        _emit_links(screen, links)
    else:
        for heading in headings:
            screen.append(f'    section "{_quote(heading)}"')
        for index, button in enumerate(buttons):
            req = f" requirement={fr_id}" if fr_id and index == 0 else ""
            variant = " variant=primary" if index == 0 else ""
            screen.append(f'    button "{_quote(button)}"{variant}{req}')
        _emit_links(screen, links)
        if not headings and not buttons and not links:
            screen.append('    text "No fields or sections were found in this route\'s own template."')
    if regions.get("footer"):
        screen.append(f'    footer "{_quote(str(regions["footer"])[:48])}"')
    return screen


def build_flows(
    routes: list[dict[str, str]],
    *,
    project_name: str,
    models: list[dict[str, Any]] | None = None,
    cues: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Wire DSL: overview map plus one screen per route from that route's cues."""
    models = models or []
    cues = cues or {}
    if not routes:
        return (
            'screen "No routes found" device=desktop\n'
            "  stack\n"
            '    heading "No route table detected"\n'
            '    text "Plan looks for routes.jsx / routes.js or Django urls.py in the linked project."\n'
        )
    groups: dict[str, list[dict[str, str]]] = {}
    for row in routes:
        groups.setdefault(row["group"], []).append(row)

    chunks: list[str] = []
    overview = [
        f'screen "Overview · {_quote(project_name)}" device=desktop route="/"',
        "  stack",
        f'    heading "{_quote(project_name)} journey map"',
        f'    text "{len(routes)} routes observed from the project route table."',
    ]
    for group, items in groups.items():
        overview.append(f'    heading "{_quote(group)}"')
        for row in items[:12]:
            overview.append(f'    link "{_quote(row["title"])}" route="{_quote(row["url"])}"')
    chunks.append("\n".join(overview))

    fr_by_url = {}
    primary = _primary_routes(routes, limit=14)
    for index, row in enumerate(primary, start=1):
        fr_by_url[row["url"]] = f"FR-{index:03d}"

    for row in routes[:55]:
        screen = _screen_lines(
            row,
            models=models,
            cues=cues,
            fr_id=fr_by_url.get(row["url"]),
        )
        chunks.append("\n".join(screen))
    text_out = "\n\n".join(chunks) + "\n"
    if len(text_out) > _MAX_WIRE_CHARS:
        # ponytail: drop trailing screens first if wire DSL nears validator limit
        while len(chunks) > 8 and len("\n\n".join(chunks) + "\n") > _MAX_WIRE_CHARS:
            chunks.pop()
        text_out = "\n\n".join(chunks) + "\n"
    return text_out


def render_artifacts(context: dict[str, Any]) -> dict[str, str]:
    return {
        "brief": build_brief(
            project_name=context["project_name"],
            project_root=Path(context["project_root"]),
            routes=context["routes"],
            models=context["models"],
            cues=context.get("cues") or {},
        ),
        "prd": build_prd(
            project_name=context["project_name"],
            routes=context["routes"],
            models=context["models"],
        ),
        "frac": build_frac(
            routes=context["routes"],
            models=context["models"],
            cues=context.get("cues") or {},
        ),
        "architecture": build_erd(context["models"]),
        "flows": build_flows(
            context["routes"],
            project_name=context["project_name"],
            models=context["models"],
            cues=context.get("cues") or {},
        ),
    }


def assess_artifacts(artifacts: dict[str, str], context: dict[str, Any]) -> list[dict[str, str]]:
    """Return concrete gaps. Empty list means this pass is adequate for deterministic depth."""
    gaps: list[dict[str, str]] = []
    models = context.get("models") or []
    routes = context.get("routes") or []
    cues = context.get("cues") or {}
    depth = int(context.get("depth") or 0)

    entities_with_real_fields = sum(1 for row in models if row.get("fields"))
    if models and entities_with_real_fields == 0 and depth < 1:
        gaps.append({"artifact": "architecture", "code": "model_fields", "detail": "ERD entities lack scalar fields"})

    flows = artifacts.get("flows") or ""
    form_count = flows.count("\n    form ")
    bind_count = flows.count(" bind=")
    if routes and form_count == 0 and depth < 2:
        gaps.append({"artifact": "flows", "code": "page_cues", "detail": "Wireframes have no forms from page inspection"})
    elif routes and cues and bind_count == 0 and any(row.get("fields") for row in models) and depth < 3:
        # Auth-only forms may intentionally omit binds when no User model exists.
        non_auth_fields = [
            field
            for cue in cues.values()
            for field in cue.get("fields") or []
            if re.sub(r"[^a-z0-9]", "", field.lower()) not in _AUTH_FIELD_TOKENS
        ]
        if non_auth_fields:
            gaps.append({"artifact": "flows", "code": "bindings", "detail": "Deep screens lack ERD bindings"})

    frac = artifacts.get("frac") or ""
    rich_acs = frac.count("AC-") - frac.count("without a server error")
    if routes and rich_acs < 3 and depth < 2:
        gaps.append({"artifact": "frac", "code": "page_cues", "detail": "FRAC acceptance criteria are still load-only"})
    elif routes and cues and rich_acs < 3 and depth < 3:
        gaps.append({"artifact": "frac", "code": "rich_acs", "detail": "FRAC needs field/button criteria from page cues"})

    prd = artifacts.get("prd") or ""
    if models and "## Domain constraints" not in prd and any(row.get("fields") for row in models) and depth < 2:
        gaps.append({"artifact": "prd", "code": "domain_constraints", "detail": "PRD missing domain field constraints"})

    brief = artifacts.get("brief") or ""
    if models and "### Key entities" not in brief and any(row.get("fields") for row in models) and depth < 2:
        gaps.append({"artifact": "brief", "code": "key_entities", "detail": "Brief missing key entity field summary"})
    if cues and "## UI surfaces inspected" not in brief and depth < 3:
        gaps.append({"artifact": "brief", "code": "ui_surfaces", "detail": "Brief missing UI inspection summary"})

    # Domain journeys should not stay as nav-only shells once models exist.
    domain_routes = [row for row in routes if row["group"] in {"Auction", "Commerce", "Logistics", "Sport"}]
    if domain_routes and any(row.get("fields") for row in models) and depth < 3:
        shallow_domain = 0
        for row in domain_routes[:12]:
            # Heuristic: a deep screen mentions the route and a form nearby.
            marker = f'route="{row["url"]}"'
            pos = flows.find(marker)
            if pos < 0:
                shallow_domain += 1
                continue
            window = flows[pos : pos + 400]
            if "\n    form " not in window:
                shallow_domain += 1
        if shallow_domain >= max(2, len(domain_routes[:12]) // 2):
            gaps.append(
                {
                    "artifact": "flows",
                    "code": "domain_wireframes",
                    "detail": f"{shallow_domain} domain screens still lack forms",
                }
            )

    return gaps


def synthesize_domain_cues(
    routes: list[dict[str, str]],
    models: list[dict[str, Any]],
    cues: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """When a domain route has no page cues, borrow fields from matching models."""
    enriched = dict(cues)
    for row in routes:
        if row["url"] in enriched:
            continue
        if row["group"] not in {"Auction", "Commerce", "Logistics", "Sport", "Ops"}:
            continue
        token = re.sub(r"[^a-z0-9]", "", row["name"].lower())
        parts = [part for part in re.findall(r"[a-z]+", row["name"].lower()) if len(part) >= 4]
        matches = []
        for entity in models:
            ename = re.sub(r"[^a-z0-9]", "", entity["name"].lower())
            if not entity.get("fields"):
                continue
            # Require real name overlap — never "any model in a vaguely related app".
            if ename in token or token in ename or any(part in ename or ename in part for part in parts):
                score = 0 if ename in token or token in ename else 2
                matches.append((score, entity))
        if not matches:
            continue
        matches.sort(key=lambda item: (item[0], -len(item[1].get("fields") or []), item[1]["name"]))
        entity = matches[0][1]
        enriched[row["url"]] = {
            "path": entity.get("source") or "",
            "fields": [field["name"] for field in entity["fields"][:6]],
            "labels": [],
            "buttons": ["Save", "Continue"],
            "synthetic": True,
            "entity": entity["name"],
        }
    return enriched


def deepen_context(context: dict[str, Any], gaps: list[dict[str, str]]) -> dict[str, Any]:
    """Close gaps by pulling more evidence from the project. Mutates and returns context."""
    root = Path(context["project_root"])
    codes = {gap["code"] for gap in gaps}
    if "model_fields" in codes or "domain_constraints" in codes or "key_entities" in codes or "bindings" in codes:
        context["models"] = scan_django_models(root, with_fields=True)
    if "page_cues" in codes or "bindings" in codes or "rich_acs" in codes or "ui_surfaces" in codes:
        context["cues"] = scan_page_cues(root, context["routes"])
    if "domain_wireframes" in codes:
        if not any(row.get("fields") for row in context["models"]):
            context["models"] = scan_django_models(root, with_fields=True)
        if not context.get("cues"):
            context["cues"] = scan_page_cues(root, context["routes"])
        context["cues"] = synthesize_domain_cues(context["routes"], context["models"], context.get("cues") or {})
    context["depth"] = int(context.get("depth") or 0) + 1
    return context

def build_plan(
    project_root: Path | str,
    *,
    project_name: str = "",
    max_passes: int = 4,
) -> dict[str, Any]:
    """Inventory, then iterate render → assess → deepen until adequate."""
    root = Path(project_root).expanduser().resolve()
    name = project_name or root.name
    context: dict[str, Any] = {
        "project_root": str(root),
        "project_name": name,
        "routes": scan_frontend_routes(root),
        "models": scan_django_models(root, with_fields=False),
        "cues": {},
        "depth": 0,
    }
    passes: list[dict[str, Any]] = []
    artifacts = render_artifacts(context)
    for pass_index in range(max_passes):
        gaps = assess_artifacts(artifacts, context)
        passes.append(
            {
                "pass": pass_index,
                "depth": context["depth"],
                "gaps": gaps,
                "stats": {
                    "routes": len(context["routes"]),
                    "models": len(context["models"]),
                    "models_with_fields": sum(1 for row in context["models"] if row.get("fields")),
                    "cues": len(context.get("cues") or {}),
                    "flow_forms": artifacts["flows"].count("\n    form "),
                    "flow_binds": artifacts["flows"].count(" bind="),
                    "frac_acs": artifacts["frac"].count("AC-"),
                },
            }
        )
        if not gaps:
            break
        if pass_index == max_passes - 1:
            break
        before = (
            context["depth"],
            len(context.get("cues") or {}),
            sum(1 for row in context["models"] if row.get("fields")),
        )
        deepen_context(context, gaps)
        after = (
            context["depth"],
            len(context.get("cues") or {}),
            sum(1 for row in context["models"] if row.get("fields")),
        )
        artifacts = render_artifacts(context)
        if after == before:
            passes.append(
                {
                    "pass": pass_index + 1,
                    "depth": context["depth"],
                    "gaps": assess_artifacts(artifacts, context),
                    "stats": passes[-1]["stats"],
                    "note": "no further evidence found",
                }
            )
            break
    final_gaps = assess_artifacts(artifacts, {**context, "depth": 99})
    return {
        "project_root": str(root),
        "project_name": name,
        "routes": context["routes"],
        "models": context["models"],
        "cues": context.get("cues") or {},
        "artifacts": artifacts,
        "passes": passes,
        "adequate": not final_gaps,
        "residual_gaps": final_gaps,
        "summary": {
            "route_count": len(context["routes"]),
            "model_count": len(context["models"]),
            "route_groups": sorted({row["group"] for row in context["routes"]}),
            "model_apps": sorted({row["app"] for row in context["models"]}),
            "cue_count": len(context.get("cues") or {}),
            "passes": len(passes),
            "adequate": not final_gaps,
        },
    }


# ---------------------------------------------------------------------------
# HTML / views / URL sitemap cache.
# This is separate from scan_project(), which still builds plan artifacts.
# The sitemap never reads React routes or JS page modules.
# ---------------------------------------------------------------------------

_SITEMAP_VERSION = 1
_SITEMAP_EDGE_KINDS = ("parent", "navigation", "journey", "auth", "redirect", "success", "failure")
_SITEMAP_STATUSES = ("verified", "inferred", "incomplete", "missing")
_MAX_SITEMAP_SOURCES = 800
_MAX_SITEMAP_NODES = 200
_MAX_SITEMAP_EDGES = 800
_SITEMAP_READ_LIMIT = 400_000
_DJANGO_URL = re.compile(
    r"""(?:path|re_path)\(\s*(?:[rubfRUBF]{0,2})?['\"](?P<route>[^'\"]*)['\"]\s*,\s*(?P<view>[^,()\n]+(?:\([^)]*\))?)\s*(?:,\s*name\s*=\s*['\"](?P<name>[^'\"]+)['\"])?""",
)
_DEF_AT_COLUMN = re.compile(r"(?m)^(?:async\s+)?def\s+(\w+)\s*\(")
_CLASS_AT_COLUMN = re.compile(r"(?m)^class\s+(\w+)\s*(\([^)]*\))?\s*:")
_RENDER_TEMPLATE = re.compile(
    r"""(?:render|TemplateResponse)\(\s*[^,\n]+,\s*['\"]([^'\"]+\.html)['\"]"""
)
_TEMPLATE_ATTR = re.compile(r"""template_name\s*=\s*['\"]([^'\"]+\.html)['\"]""")
_REDIRECT_TARGET = re.compile(
    r"""(?:redirect|HttpResponseRedirect|HttpResponsePermanentRedirect)\(\s*(?:reverse\(\s*)?['\"]([^'\"]+)['\"]"""
)
_EXTENDS = re.compile(r"""\{%\s*extends\s+['\"]([^'\"]+)['\"]""")
_INCLUDE = re.compile(r"""\{%\s*include\s+['\"]([^'\"]+)['\"]""")
_URL_TAG = re.compile(r"""\{%\s*url\s+['\"]([^'\"]+)['\"]""")
_ANCHOR = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_HREF = re.compile(r"""href\s*=\s*['\"]([^'\"#]+)['\"]""", re.IGNORECASE)
_AUTH_DECORATOR = re.compile(
    r"@(?:[\w\.]+\.)?(?:login_required|permission_required|user_passes_test|staff_member_required)\b"
)
_AUTH_MIXIN = re.compile(r"\b(?:LoginRequiredMixin|PermissionRequiredMixin|UserPassesTestMixin)\b")
_SCAFFOLD_NAME = re.compile(
    r"(?:^|/)(?:base|layout|skeleton|header|footer|nav|navbar|sidebar)(?:[._-][^/]+)?\.html$",
    re.IGNORECASE,
)
_JOURNEY_LABEL = re.compile(r"\b(?:continue|next|proceed|start|begin)\b", re.IGNORECASE)
_VALID_IF = re.compile(r"^if\b.*\bis_valid\b")
_LOGIN_NAMES = {"login", "signin", "sign_in", "log_in"}
_rebuild_guard = threading.Lock()
_rebuild_inflight: set[str] = set()


def _sitemap_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return (slug or "node")[:48]


def _read_limited(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:_SITEMAP_READ_LIMIT]
    except OSError:
        return ""


def iter_sitemap_sources(project_root: Path) -> list[Path]:
    """HTML templates, view modules, and URL maps. Never JS/JSX route tables."""
    root = Path(project_root)
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIR and not name.startswith(".")]
        for name in filenames:
            path = Path(dirpath) / name
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            in_templates = "templates" in rel.parts and name.endswith(".html")
            view_module = name == "views.py" or (path.parent.name == "views" and name.endswith(".py"))
            if name == "urls.py" or view_module or in_templates:
                found.append(path)
            if len(found) >= _MAX_SITEMAP_SOURCES:
                return sorted(found, key=lambda item: item.relative_to(root).as_posix())
    return sorted(found, key=lambda item: item.relative_to(root).as_posix())


def _sitemap_fingerprint(project_root: Path) -> dict[str, dict[str, int]]:
    root = Path(project_root)
    fingerprint: dict[str, dict[str, int]] = {}
    for path in iter_sitemap_sources(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        fingerprint[path.relative_to(root).as_posix()] = {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}
    return fingerprint


def sitemap_cache_path(project_root: Path, cache_dir: Path | None = None) -> Path:
    root = Path(project_root).expanduser().resolve()
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:20]
    base = Path(cache_dir) if cache_dir else Path.home() / ".decisionsai" / "sitemap-cache"
    return base / f"{digest}.json"


def _normalize_route(raw: str) -> str:
    text = str(raw or "").strip().lstrip("^").rstrip("$")
    text = re.sub(r"\(\?P<(\w+)>[^)]+\)", r":\1", text)
    text = re.sub(r"<(?:\w+:)?(\w+)>", r":\1", text)
    text = text.replace("\\", "")
    if not text.startswith("/"):
        text = "/" + text
    text = re.sub(r"/{2,}", "/", text)
    if text != "/":
        text = text.rstrip("/") or "/"
    return text


def _route_matches(pattern: str, concrete: str) -> bool:
    left = [part for part in _normalize_route(pattern).split("/") if part]
    right = [part for part in _normalize_route(concrete).split("/") if part]
    if len(left) != len(right):
        return False
    for pattern_part, concrete_part in zip(left, right):
        if pattern_part.startswith(":"):
            continue
        if pattern_part != concrete_part:
            return False
    return True


def _callable_name(expr: str) -> str:
    cleaned = expr.strip()
    cleaned = re.sub(r"\.as_view\s*(\([^)]*\))?\s*$", "", cleaned)
    cleaned = cleaned.split("(")[0].strip()
    return cleaned.split(".")[-1].strip()


def _parse_url_maps(files: list[Path], root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in files:
        if path.name != "urls.py":
            continue
        for match in _DJANGO_URL.finditer(_read_limited(path)):
            view_expr = match.group("view").strip()
            if "include(" in view_expr or view_expr.startswith("include"):
                continue
            name = _callable_name(view_expr)
            if not name or name in {"include", "redirect"}:
                continue
            route = _normalize_route(match.group("route"))
            if route.startswith("/api") or "/admin" in route or route.startswith("/static"):
                continue
            url_name = (match.group("name") or name).strip()
            key = f"{route}|{url_name}"
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "route": route,
                    "url_name": url_name,
                    "view": name,
                    "source": path.relative_to(root).as_posix(),
                }
            )
            if len(rows) >= _MAX_SITEMAP_NODES:
                return rows
    return rows


def _parse_views(files: list[Path], root: Path) -> dict[str, dict[str, Any]]:
    views: dict[str, dict[str, Any]] = {}
    for path in files:
        rel = path.relative_to(root)
        if path.name != "views.py" and not (path.parent.name == "views" and path.suffix == ".py"):
            continue
        text = _read_limited(path)
        spans: list[tuple[int, int, str, str]] = []
        marks = [(match.start(), match.end(), "def", match.group(1)) for match in _DEF_AT_COLUMN.finditer(text)]
        marks += [(match.start(), match.end(), "class", match.group(1)) for match in _CLASS_AT_COLUMN.finditer(text)]
        marks.sort()
        for index, (start, end, kind, name) in enumerate(marks):
            stop = marks[index + 1][0] if index + 1 < len(marks) else len(text)
            spans.append((start, stop, kind, name))
        for start, stop, kind, name in spans:
            if name.startswith("_"):
                continue
            header = text[max(0, start - 400):start]
            body = text[start:stop]
            decorators = _decorators(header)
            auth = any(_AUTH_DECORATOR.search(item) for item in decorators) or bool(_AUTH_MIXIN.search(body if kind == "class" else header))
            template = ""
            render_match = _RENDER_TEMPLATE.search(body)
            attr_match = _TEMPLATE_ATTR.search(body)
            if render_match:
                template = render_match.group(1).strip()
            elif attr_match:
                template = attr_match.group(1).strip()
            explicit = bool(template)
            success, failure, other = _split_outcome_regions(body)
            entry = views.get(name)
            if entry and entry.get("template"):
                continue
            views[name] = {
                "name": name,
                "kind": kind,
                "template": template,
                "explicit_template": explicit,
                "auth": auth,
                "success": _REDIRECT_TARGET.findall(success),
                "failure": _REDIRECT_TARGET.findall(failure),
                "redirects": _REDIRECT_TARGET.findall(other),
                "source": rel.as_posix(),
            }
    return views


def _decorators(header: str) -> list[str]:
    found: list[str] = []
    for line in reversed(header.splitlines()):
        stripped = line.strip()
        if not stripped:
            if found:
                break
            continue
        if stripped.startswith("@"):
            found.append(stripped)
            continue
        break
    return found


def _split_outcome_regions(body: str) -> tuple[str, str, str]:
    success: list[str] = []
    failure: list[str] = []
    other: list[str] = []
    mode = "other"
    base_indent: int | None = None
    for line in body.splitlines()[1:]:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if mode != "other" and base_indent is not None and stripped and indent <= base_indent and not re.match(r"(elif|else)\b", stripped):
            mode = "other"
            base_indent = None
        if _VALID_IF.match(stripped):
            mode = "success"
            base_indent = indent
            continue
        if mode == "success" and base_indent is not None and indent == base_indent and re.match(r"(elif|else)\b", stripped):
            mode = "failure"
            continue
        if mode == "failure" and base_indent is not None and stripped and indent <= base_indent and not re.match(r"(elif|else)\b", stripped):
            mode = "other"
            base_indent = None
        if mode == "success":
            success.append(line)
        elif mode == "failure":
            failure.append(line)
        else:
            other.append(line)
    return "\n".join(success), "\n".join(failure), "\n".join(other)


def _template_index(files: list[Path], root: Path) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for path in files:
        rel = path.relative_to(root)
        if path.suffix != ".html" or "templates" not in rel.parts:
            continue
        parts = rel.parts
        marker = parts.index("templates")
        key = "/".join(parts[marker + 1:])
        if not key or key in index:
            continue
        text = _read_limited(path)
        extends = _EXTENDS.findall(text)
        index[key] = {
            "key": key,
            "path": rel.as_posix(),
            "text": text,
            "extends": extends[0] if extends else "",
            "includes": _INCLUDE.findall(text),
        }
    return index


def _resolve_template_key(name: str, index: dict[str, dict[str, str]]) -> str | None:
    cleaned = str(name or "").strip().lstrip("/")
    if not cleaned:
        return None
    if cleaned in index:
        return cleaned
    matches = [key for key in index if key.endswith("/" + cleaned)]
    if len(matches) == 1:
        return matches[0]
    return None


def _is_login_page(row: dict[str, str]) -> bool:
    return row["url_name"].lower() in _LOGIN_NAMES or row["route"].rstrip("/").endswith("/login") or row["route"] in {"/login", "/signin"}


def _unique_id(prefix: str, key: str, used: set[str]) -> str:
    base = f"{prefix}-{_sitemap_slug(key)}"
    candidate = base
    serial = 2
    while candidate in used:
        candidate = f"{base}-{serial}"
        serial += 1
    used.add(candidate)
    return candidate


def _add_edge(edges: list[dict[str, str]], seen: set[tuple[str, str, str]], source: str, target: str, kind: str, label: str = "") -> None:
    if not source or not target or source == target or kind not in _SITEMAP_EDGE_KINDS:
        return
    key = (source, target, kind)
    if key in seen or len(edges) >= _MAX_SITEMAP_EDGES:
        return
    seen.add(key)
    edges.append({"from": source, "to": target, "kind": kind, "label": label[:80]})


def _coverage(nodes: list[dict[str, Any]], template_count: int) -> tuple[str, str]:
    if template_count == 0 and not nodes:
        return (
            "missing",
            "No HTML templates found. Sitemap was not inferred from React routes or JavaScript page files.",
        )
    if template_count == 0:
        return (
            "incomplete",
            "No HTML templates found. URL and view names are recorded as incomplete or missing, not invented from React routes.",
        )
    if not nodes:
        return ("incomplete", "Only scaffolding templates were found. No content page is wired to a view and URL.")
    statuses = {str(node.get("status")) for node in nodes}
    if statuses & {"missing", "incomplete"}:
        return ("incomplete", "Some pages are missing a template, view, or URL.")
    if "inferred" in statuses:
        return ("inferred", "Some templates were inferred from the view name because the view did not name a template.")
    return ("verified", "")


def build_html_sitemap(project_root: Path | str) -> dict[str, Any]:
    """Build a sitemap from Django HTML templates, views, and URL maps only."""
    root = Path(project_root).expanduser().resolve()
    files = iter_sitemap_sources(root) if root.is_dir() else []
    urls = _parse_url_maps(files, root)
    views = _parse_views(files, root)
    templates = _template_index(files, root)
    extended_by: dict[str, int] = {}
    included_by: dict[str, int] = {}
    for item in templates.values():
        parent = _resolve_template_key(item["extends"], templates) if item["extends"] else None
        if parent:
            extended_by[parent] = extended_by.get(parent, 0) + 1
        for include in item["includes"]:
            key = _resolve_template_key(include, templates)
            if key:
                included_by[key] = included_by.get(key, 0) + 1

    rendered_keys: set[str] = set()
    for view in views.values():
        key = _resolve_template_key(view["template"], templates) if view["template"] else None
        if key:
            rendered_keys.add(key)

    scaffolding: list[dict[str, str]] = []
    used_ids: set[str] = set()
    for key, item in sorted(templates.items()):
        rendered = key in rendered_keys
        scaffold = (not rendered) and (
            bool(_SCAFFOLD_NAME.search(key)) or extended_by.get(key, 0) > 0 or included_by.get(key, 0) > 0
        )
        if not scaffold:
            continue
        scaffolding.append(
            {
                "id": _unique_id("scaffold", key, used_ids),
                "kind": "base_template",
                "label": Path(key).name,
                "path": item["path"],
                "template": key,
                "status": "verified",
            }
        )
    scaffold_templates = {item["template"] for item in scaffolding}

    pages: list[dict[str, Any]] = []
    for row in urls:
        view = views.get(row["view"])
        template_name = view["template"] if view else ""
        explicit = bool(view and view.get("explicit_template") and template_name)
        inferred = False
        if view and not template_name:
            guessed = _resolve_template_key(f"{row['view']}.html", templates)
            if guessed:
                template_name = guessed
                inferred = True
        template_key = _resolve_template_key(template_name, templates) if template_name else None
        if template_key in scaffold_templates:
            template_key = None
        if view and template_name and template_key is None:
            status = "missing"
        elif view and template_key and not inferred:
            status = "verified"
        elif view and template_key and inferred:
            status = "inferred"
        elif view or template_key:
            status = "incomplete"
        else:
            status = "missing"
        pages.append(
            {
                "route": row["route"],
                "url_name": row["url_name"],
                "view": row["view"],
                "template": template_key or template_name,
                "template_found": bool(template_key),
                "status": status,
                "auth": bool(view and view.get("auth")),
                "view_meta": view or {},
                "source": row["source"],
            }
        )

    template_users: dict[str, list[int]] = {}
    for index, page in enumerate(pages):
        if page["template_found"]:
            template_users.setdefault(page["template"], []).append(index)

    nodes: list[dict[str, Any]] = []
    page_ids: dict[int, str] = {}
    template_node_ids: dict[str, str] = {}
    by_name: dict[str, str] = {}
    by_route: dict[str, str] = {}

    for key, users in sorted(template_users.items()):
        if len(users) < 2:
            continue
        node_id = _unique_id("template", key, used_ids)
        template_node_ids[key] = node_id
        nodes.append(
            {
                "id": node_id,
                "kind": "page_template",
                "label": _title(Path(key).stem),
                "route": "",
                "template": key,
                "view": "",
                "url_name": "",
                "status": "verified",
                "auth": False,
            }
        )
    for key, item in sorted(templates.items()):
        if key in scaffold_templates or key in template_node_ids or key in template_users:
            continue
        node_id = _unique_id("template", key, used_ids)
        template_node_ids[key] = node_id
        nodes.append(
            {
                "id": node_id,
                "kind": "page_template",
                "label": _title(Path(key).stem),
                "route": "",
                "template": key,
                "view": "",
                "url_name": "",
                "status": "incomplete",
                "auth": False,
            }
        )

    for index, page in enumerate(pages):
        if len(nodes) >= _MAX_SITEMAP_NODES:
            break
        node_id = _unique_id("page", page["url_name"] or page["route"], used_ids)
        page_ids[index] = node_id
        by_name.setdefault(page["url_name"], node_id)
        by_route.setdefault(page["route"], node_id)
        nodes.append(
            {
                "id": node_id,
                "kind": "page",
                "label": _title(page["url_name"] or page["route"].strip("/") or "home"),
                "route": page["route"],
                "template": page["template"],
                "view": page["view"],
                "url_name": page["url_name"],
                "status": page["status"] if page["status"] in _SITEMAP_STATUSES else "incomplete",
                "auth": page["auth"],
            }
        )

    def resolve_target(token: str) -> str | None:
        cleaned = str(token or "").strip()
        if not cleaned:
            return None
        if cleaned.startswith("/"):
            normalized = _normalize_route(cleaned)
            if normalized in by_route:
                return by_route[normalized]
            for route, node_id in by_route.items():
                if _route_matches(route, normalized):
                    return node_id
            return None
        return by_name.get(cleaned)

    def ensure_missing(token: str) -> str | None:
        if len(nodes) >= _MAX_SITEMAP_NODES:
            return None
        existing = resolve_target(token)
        if existing:
            return existing
        label = token if token.startswith("/") else _title(token)
        node_id = _unique_id("missing", token, used_ids)
        route = _normalize_route(token) if token.startswith("/") else ""
        nodes.append(
            {
                "id": node_id,
                "kind": "page",
                "label": label,
                "route": route,
                "template": "",
                "view": "",
                "url_name": "" if token.startswith("/") else token,
                "status": "missing",
                "auth": False,
            }
        )
        if route:
            by_route.setdefault(route, node_id)
        elif token:
            by_name.setdefault(token, node_id)
        return node_id

    edges: list[dict[str, str]] = []
    edge_seen: set[tuple[str, str, str]] = set()
    for key, node_id in template_node_ids.items():
        for index in template_users.get(key, []):
            _add_edge(edges, edge_seen, node_id, page_ids.get(index, ""), "parent", key)
    routes = {page["route"] for page in pages}
    for index, page in enumerate(pages):
        parent = _parent_route(page["route"], routes)
        if parent and parent in by_route and index in page_ids:
            _add_edge(edges, edge_seen, by_route[parent], page_ids[index], "parent", parent)
        template_key = page["template"] if page["template_found"] else ""
        item = templates.get(template_key)
        if not item:
            continue
        extends = _resolve_template_key(item["extends"], templates) if item["extends"] else None
        if extends and extends in template_node_ids and index in page_ids:
            _add_edge(edges, edge_seen, template_node_ids[extends], page_ids[index], "parent", extends)
        for target, label in _template_links(item["text"]):
            kind = "journey" if _JOURNEY_LABEL.search(label or "") else "navigation"
            destination = resolve_target(target[1]) or ensure_missing(target[1] if target[0] == "name" else target[1])
            if index in page_ids:
                _add_edge(edges, edge_seen, page_ids[index], destination or "", kind, label)

    login_ids = [page_ids[index] for index, page in enumerate(pages) if _is_login_page(page) and index in page_ids]
    for index, page in enumerate(pages):
        view = page["view_meta"]
        source = page_ids.get(index, "")
        if not source or not view:
            continue
        for token in view.get("success") or []:
            _add_edge(edges, edge_seen, source, ensure_missing(token) or "", "success", token)
        for token in view.get("failure") or []:
            _add_edge(edges, edge_seen, source, ensure_missing(token) or "", "failure", token)
        for token in view.get("redirects") or []:
            _add_edge(edges, edge_seen, source, ensure_missing(token) or "", "redirect", token)
        if page["auth"] and login_ids and source not in login_ids:
            for login_id in login_ids:
                _add_edge(edges, edge_seen, login_id, source, "auth", "authentication boundary")

    coverage, note = _coverage(nodes, len(templates))
    return {
        "version": _SITEMAP_VERSION,
        "source": "html-views-urls",
        "coverage": coverage,
        "note": note,
        "nodes": nodes,
        "edges": edges,
        "scaffolding": scaffolding,
    }


def _parent_route(route: str, routes: set[str]) -> str | None:
    parts = [part for part in _normalize_route(route).split("/") if part and not part.startswith(":")]
    for length in range(len(parts) - 1, 0, -1):
        candidate = "/" + "/".join(parts[:length])
        if candidate in routes:
            return candidate
    return None


def _template_links(text: str) -> list[tuple[tuple[str, str], str]]:
    links: list[tuple[tuple[str, str], str]] = []
    seen: set[tuple[str, str]] = set()
    for attrs, inner in _ANCHOR.findall(text):
        label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner)).strip()
        url_match = _URL_TAG.search(attrs) or _URL_TAG.search(inner)
        href_match = _HREF.search(attrs)
        target: tuple[str, str] | None = None
        if url_match:
            target = ("name", url_match.group(1))
        elif href_match:
            href = href_match.group(1).split("?", 1)[0]
            if href.startswith("/") and not href.startswith("//"):
                target = ("path", href)
        if target:
            links.append((target, label))
            seen.add(target)
    for name in _URL_TAG.findall(text):
        target = ("name", name)
        if target not in seen:
            seen.add(target)
            links.append((target, name))
    return links


def _write_sitemap_cache(path: Path, project_root: Path, model: dict[str, Any], fingerprint: dict[str, dict[str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _SITEMAP_VERSION,
        "project_root": str(project_root),
        "fingerprint": fingerprint,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_sitemap_cache(path: Path, project_root: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(payload, dict) or payload.get("project_root") != str(project_root):
        return None
    model = payload.get("model")
    if not isinstance(model, dict):
        return None
    payload["model"] = model
    return payload


def rebuild_sitemap_cache(project_root: Path | str, cache_path: Path | None = None) -> dict[str, Any]:
    """Write the sitemap cache from the current HTML templates, views, and URL maps."""
    root = Path(project_root).expanduser().resolve()
    path = Path(cache_path) if cache_path else sitemap_cache_path(root)
    model = build_html_sitemap(root)
    model["built_at"] = datetime.now(timezone.utc).isoformat()
    _write_sitemap_cache(path, root, model, _sitemap_fingerprint(root))
    return model


def _schedule_sitemap_rebuild(project_root: Path, cache_path: Path) -> None:
    """Rebuild off the request path. Callers do not use the scan button."""
    key = str(cache_path)
    with _rebuild_guard:
        if key in _rebuild_inflight:
            return
        _rebuild_inflight.add(key)

    def run() -> None:
        try:
            rebuild_sitemap_cache(project_root, cache_path=cache_path)
        except OSError:
            return
        finally:
            with _rebuild_guard:
                _rebuild_inflight.discard(key)

    threading.Thread(target=run, name="sitemap-rebuild", daemon=True).start()


def ensure_sitemap_cache(project_root: Path | str, cache_path: Path | None = None) -> dict[str, Any]:
    """Return the cached sitemap, building it on first scan and scheduling a rebuild on drift."""
    root = Path(project_root).expanduser().resolve()
    path = Path(cache_path) if cache_path else sitemap_cache_path(root)
    fingerprint = _sitemap_fingerprint(root)
    cached = _read_sitemap_cache(path, root) if path.is_file() else None
    if cached is None:
        return rebuild_sitemap_cache(root, cache_path=path)
    if cached.get("fingerprint") == fingerprint:
        return cached["model"]
    _schedule_sitemap_rebuild(root, path)
    return cached["model"]




def scan_project(project_root: Path | str, *, project_name: str = "", max_passes: int = 4) -> dict[str, Any]:
    """Public entry used by Plan materialize — always runs the iterative builder."""
    return build_plan(project_root, project_name=project_name, max_passes=max_passes)
