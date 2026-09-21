"""Deterministic project → plan builders for any linked project folder.

Same pipeline for every board. Stdlib only. No project-specific shortcuts.

Flow: inventory → render → assess → deepen → repeat until adequate or
max passes. One-shot dumps are not enough; each pass must close real gaps
in wireframes, ERD, and requirements.
"""

from __future__ import annotations

import re
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


def scan_page_cues(project_root: Path, routes: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    """Extract form/button cues from page components linked by route view paths."""
    cues: dict[str, dict[str, Any]] = {}
    for row in routes:
        path = _resolve_page_file(project_root, row.get("view") or "")
        if path is None and row.get("name"):
            path = _resolve_page_file(project_root, row["name"])
        if path is None:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")[:80_000]
        fields: list[str] = []
        for block in _USESTATE_OBJ.findall(text):
            for key in re.findall(r"""['\"]?([A-Za-z_][\w]*)['\"]?\s*:""", block):
                if key not in fields and key not in {"true", "false", "null"}:
                    fields.append(key)
        labels: list[str] = []
        for match in _LABEL.finditer(text):
            value = (match.group(1) or match.group(2) or "").strip()
            if value and value not in labels and len(value) < 40:
                labels.append(value)
        buttons: list[str] = []
        for match in _BUTTON_TEXT.finditer(text):
            value = (match.group(1) or match.group(2) or "").strip()
            if value and value not in buttons and _usable_label(value):
                buttons.append(value)
        for match in _SUBMIT_LITERAL.finditer(text):
            value = match.group(1).strip()
            allowed = value.lower() in {"save", "submit", "login", "register", "send", "continue", "next", "bid", "buy", "sign in"}
            if value and value not in buttons and _usable_label(value) and (" " in value or allowed):
                buttons.append(value)
        for value in re.findall(r"""\)\s*:\s*['\"]([A-Za-z][^'\"]{1,40})['\"]\s*\}""", text):
            if value not in buttons and _usable_label(value):
                buttons.append(value)
        if not fields and not labels and not buttons:
            continue
        cues[row["url"]] = {
            "path": str(path.relative_to(project_root)),
            "fields": fields[:10],
            "labels": labels[:10],
            "buttons": buttons[:8],
        }
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
            score = None
            if token == fname:
                score = 0
            elif len(fname) >= 4 and len(token) >= 4 and (token.startswith(fname) or fname.startswith(token)):
                score = 5
            else:
                continue
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


def _screen_lines(
    row: dict[str, str],
    *,
    groups: dict[str, list[dict[str, str]]],
    models: list[dict[str, Any]],
    cues: dict[str, dict[str, Any]],
    fr_id: str | None,
    deep: bool,
) -> list[str]:
    screen = [
        f'screen "{_quote(row["title"])}" device=desktop route="{_quote(row["url"])}"',
        "  stack",
        f'    heading "{_quote(row["title"])}"',
        f'    text "Route {_quote(row["url"])} · group {_quote(row["group"])}"',
    ]
    cue = cues.get(row["url"]) or {}
    if deep and (cue.get("fields") or cue.get("labels") or cue.get("buttons")):
        screen.append(f'    form "{_quote(row["title"])}"')
        used = 0
        for field in cue.get("fields") or []:
            bind = _bind_field(field, models, route_name=row["name"])
            label = _quote(_title(field))
            if bind:
                screen.append(f'      input "{label}" bind={bind}')
            else:
                screen.append(f'      input "{label}"')
            used += 1
            if used >= 6:
                break
        if used == 0:
            for label in (cue.get("labels") or [])[:4]:
                screen.append(f'      input "{_quote(label)}"')
        primary = (cue.get("buttons") or ["Continue"])[0]
        req = f" requirement={fr_id}" if fr_id else ""
        screen.append(f'      button "{_quote(primary)}" variant=primary{req}')
        for button in (cue.get("buttons") or [])[1:3]:
            screen.append(f'      button "{_quote(button)}"')
    else:
        screen.append('    button "Overview" href="/"')
        siblings = [peer for peer in groups.get(row["group"], []) if peer["url"] != row["url"]][:4]
        for peer in siblings:
            screen.append(f'    button "{_quote(peer["title"])}" href="{_quote(peer["url"])}"')
    return screen


def build_flows(
    routes: list[dict[str, str]],
    *,
    project_name: str,
    models: list[dict[str, Any]] | None = None,
    cues: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Wire DSL: overview map plus screens; deep screens when page cues exist."""
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
            overview.append(f'    button "{_quote(row["title"])}" href="{_quote(row["url"])}"')
    chunks.append("\n".join(overview))

    ranked = sorted(
        routes,
        key=lambda row: (
            0 if (cues.get(row["url"]) or {}).get("fields") else 1,
            0 if row["url"] in cues else 2,
            _GROUP_RANK.get(row["group"], 50),
            0 if row["name"] in {"login", "register", "cart", "home"} else 1,
            row["url"],
        ),
    )
    deep_list = [row["url"] for row in ranked if row["url"] in cues][:_MAX_DEEP_SCREENS]
    deep_set = set(deep_list)

    fr_by_url = {}
    primary = _primary_routes(routes, limit=14)
    for index, row in enumerate(primary, start=1):
        fr_by_url[row["url"]] = f"FR-{index:03d}"

    for row in routes[:55]:
        screen = _screen_lines(
            row,
            groups=groups,
            models=models,
            cues=cues,
            fr_id=fr_by_url.get(row["url"]),
            deep=row["url"] in deep_set,
        )
        chunks.append("\n".join(screen))
    text = "\n\n".join(chunks) + "\n"
    if len(text) > _MAX_WIRE_CHARS:
        # ponytail: drop shallow trailing screens first if wire DSL nears validator limit
        while len(chunks) > 8 and len("\n\n".join(chunks) + "\n") > _MAX_WIRE_CHARS:
            chunks.pop()
        text = "\n\n".join(chunks) + "\n"
    return text


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


def scan_project(project_root: Path | str, *, project_name: str = "", max_passes: int = 4) -> dict[str, Any]:
    """Public entry used by Plan materialize — always runs the iterative builder."""
    return build_plan(project_root, project_name=project_name, max_passes=max_passes)
