"""Derive candidate library entries from a project's own components.

The rule the operator set: **the AI consults the library first**. So derivation
does two things:

1. **Skip what the library already covers.** A component whose name matches an
   existing entry is not re-derived, and it is reported as covered rather than
   silently ignored - that is the "consider the library first" rule made real.
2. **Emit candidates for the rest**, marked ``derived`` so they can be reviewed
   before anyone trusts them. A component name and its props are a hint, not a
   design.

Derived entries are product-scoped (``product:<slug>``) by default, so a bespoke
component never pollutes the general library.

Best-effort throughout: an unreadable project yields no candidates, never an
error.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# Only look at component-ish files; this is a hint generator, not a full parse.
_COMPONENT_SUFFIXES = (".jsx", ".tsx", ".vue", ".svelte")
MAX_FILES = 400
MAX_ENTRIES = 60
MAX_NAME = 64

# A component name that is really a page or a layout is not a widget.
_PAGE_HINTS = ("page", "route", "layout", "shell", "screen")
_SKIP_DIRS = {
    "node_modules", "dist", "build", ".git", "__pycache__", "coverage",
    "vendor", "vendors", ".next", ".nuxt", "out", "tmp",
}

_COMPONENT_DECL = re.compile(
    r"""(?:^|\n)\s*(?:export\s+)?(?:default\s+)?(?:function|const|class)\s+([A-Z][A-Za-z0-9]{2,40})\b""",
)
_PROPS = re.compile(r"(?:interface|type)\s+([A-Z][A-Za-z0-9]{2,40})Props\b", re.MULTILINE)


def _slug(value: Any, limit: int = MAX_NAME) -> str:
    raw = " ".join(str(value or "").split()).lower()
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in raw)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-_")[:limit]


def _camel_to_kebab(name: str) -> str:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", str(name or ""))
    return _slug(spaced)


def _iter_component_files(root: Path) -> list[Path]:
    out: list[Path] = []
    try:
        for path in sorted(root.rglob("*")):
            if len(out) >= MAX_FILES:
                break
            if not path.is_file() or path.suffix.lower() not in _COMPONENT_SUFFIXES:
                continue
            if any(part in _SKIP_DIRS or part.startswith(".") for part in path.relative_to(root).parts):
                continue
            out.append(path)
    except OSError:
        logger.debug("derive-library: could not walk %s", root, exc_info=True)
    return out


def _components_in(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:200_000]
    except OSError:
        return []
    names = [m.group(1) for m in _COMPONENT_DECL.finditer(text)]
    # ``Props`` sits outside the capture group, so the group is already the
    # component name - do not strip it again.
    names.extend(m.group(1) for m in _PROPS.finditer(text))
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def _is_widget(name: str) -> bool:
    lowered = name.lower()
    return not any(hint in lowered for hint in _PAGE_HINTS)


def derive_entries(
    root: str | Path,
    *,
    product: str | None = None,
    library_module: Any = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Candidate library entries from a project.

    Returns ``{"entries": [...], "covered": [...], "scanned": N}``. ``covered``
    lists component names the library already has an entry for - the library-first
    rule, made visible instead of silently skipped.
    """
    result: dict[str, Any] = {"entries": [], "covered": [], "scanned": 0}
    if limit is None:
        limit = MAX_ENTRIES
    # An empty string is Path(".") - the current directory. Scanning the repo
    # because a caller passed nothing is never what was meant.
    if root is None or not str(root).strip():
        logger.debug("derive-library: no project root given")
        return result
    project = Path(str(root)).expanduser()
    if not project.is_dir():
        logger.debug("derive-library: %s is not a directory", project)
        return result

    if library_module is None:
        from distr.core.planning import library as library_module  # type: ignore[no-redef]

    try:
        existing = {entry.name for entry in library_module.discover()}
    except Exception:
        logger.debug("derive-library: could not read the library", exc_info=True)
        existing = set()

    scope = f"product:{_slug(product)}" if product else "global"
    files = _iter_component_files(project)
    result["scanned"] = len(files)

    seen: set[str] = set()
    for path in files:
        if len(result["entries"]) >= limit:
            break
        for component in _components_in(path):
            if not _is_widget(component):
                continue
            name = _camel_to_kebab(component)
            if not name or name in seen:
                continue
            seen.add(name)

            # Library first: never re-derive something the library already covers.
            if name in existing:
                result["covered"].append(name)
                continue

            result["entries"].append({
                "name": name,
                "kind": "widget",
                "title": re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", component),
                "scope": scope,
                "tags": ["derived"],
                "description": f"Derived from {path.relative_to(project).as_posix()}. Review before use.",
                "wire": f'content "{name}"',
                "derived": True,
                "source_file": path.relative_to(project).as_posix(),
            })
            if len(result["entries"]) >= limit:
                break

    return result


def write_candidates(result: dict[str, Any], target_root: str | Path) -> list[str]:
    """Write derived entries as library folders. Returns the names written.

    Written to a ``derived/`` subtree so they are easy to review, and easy to
    delete wholesale if they turn out to be noise.
    """
    import json

    written: list[str] = []
    target = Path(str(target_root or "")).expanduser()
    for entry in (result or {}).get("entries") or []:
        name = _slug(entry.get("name"))
        if not name:
            continue
        folder = target / "derived" / name
        try:
            folder.mkdir(parents=True, exist_ok=True)
            payload = {k: v for k, v in entry.items() if k != "derived"}
            (folder / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            written.append(name)
        except OSError:
            logger.warning("derive-library: could not write %s", folder, exc_info=True)
    return written
