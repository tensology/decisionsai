"""Which stack does this project actually build its pages in?

The scan looked at one place and stencilled whatever it found. For Tensology that
was the Django side - 43 files returning JSON against 2 calling render(), and a
template index holding only emails/, admin/ and unfold/. Its pages are the 164
.tsx files in frontend/. So the scan produced 81 blank boxes from an API while the
app sat next to it, unread.

This module answers two questions before anything is stencilled:

1. **Which stacks are present?** Detected from project markers, not guessed.
2. **Do they serve pages, or only data?** A Django project whose views answer with
   JSON is an API: its pages are elsewhere.

Detection is a hint for routing, never a verdict - an unknown project still gets
the generic path.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

MAX_FILES = 4000
_SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", "__pycache__", ".next", ".nuxt",
    "venv", ".venv", "coverage", "out", "target", "vendor", "vendors",
}

# What each stack looks like on disk.
_DJANGO_MARKERS = ("manage.py",)
_DJANGO_HINTS = ("settings.py", "urls.py", "wsgi.py")
_RUST_MARKERS = ("Cargo.toml",)
_QT_SUFFIXES = (".ui", ".qml", ".pro")
_JS_SUFFIXES = (".jsx", ".tsx", ".vue", ".svelte")
_HTML_SUFFIXES = (".html",)

_JSON_RETURN = re.compile(r"\bJsonResponse\b|\bJSONResponse\b|rest_framework\.responses\.Response|\bapi_response\s*\(")
_RENDER_RETURN = re.compile(r"\brender\s*\(|\bTemplateResponse\s*\(|\brender_to_string\s*\(")


@dataclass
class Stack:
    """One stack found in a project, and whether it serves pages."""

    name: str
    pages: int = 0          # files that look like pages
    data_only: int = 0      # files that answer with data
    root: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def serves_pages(self) -> bool:
        return self.pages > 0 and self.pages >= self.data_only

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "pages": self.pages,
            "data_only": self.data_only,
            "serves_pages": self.serves_pages,
            "root": self.root,
            "notes": list(self.notes),
        }


def _walk(root: Path, *, limit: int = MAX_FILES) -> list[Path]:
    out: list[Path] = []
    try:
        for path in root.rglob("*"):
            if len(out) >= limit:
                break
            if not path.is_file():
                continue
            if any(part in _SKIP_DIRS or part.startswith(".") for part in path.relative_to(root).parts):
                continue
            out.append(path)
    except Exception:
        # A hint generator must never take the scan down with it.
        logger.debug("stack-detect: could not walk %s", root, exc_info=True)
    return out


def _read(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _detect_django(root: Path, files: Iterable[Path]) -> Stack | None:
    names = {p.name for p in files}
    has_marker = any(m in names for m in _DJANGO_MARKERS) or any(
        p.name in _DJANGO_HINTS and "templates" not in p.parts for p in files
    )
    if not has_marker:
        return None
    stack = Stack(name="django", root=str(root))
    for path in files:
        if path.suffix != ".py":
            continue
        text = _read(path)
        if not re.search(r"\bdef\s+\w+\s*\(\s*request", text) and "class " not in text:
            continue
        if _JSON_RETURN.search(text) and not _RENDER_RETURN.search(text):
            stack.data_only += 1
        elif _RENDER_RETURN.search(text) or re.search(r"\.html\b", text):
            stack.pages += 1
    if stack.data_only and not stack.pages:
        stack.notes.append(
            "Every view answers with data - this is an API. Its pages are in another stack."
        )
    return stack


def _detect_js(root: Path, files: Iterable[Path]) -> Stack | None:
    components = [p for p in files if p.suffix.lower() in _JS_SUFFIXES]
    if not components:
        return None
    stack = Stack(name="react", pages=len(components), root=str(root))
    for pkg in (p for p in files if p.name == "package.json"):
        try:
            data = json.loads(_read(pkg) or "{}")
        except ValueError:
            continue
        deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
        if "vue" in deps:
            stack.name = "vue"
        elif "svelte" in deps:
            stack.name = "svelte"
        elif "react" in deps or "next" in deps:
            stack.name = "react"
        break
    return stack


def _detect_rust(root: Path, files: Iterable[Path]) -> Stack | None:
    if not any(p.name in _RUST_MARKERS for p in files):
        return None
    stack = Stack(name="rust", root=str(root))
    for path in files:
        if path.suffix in (".html", ".tera", ".hbs"):
            stack.pages += 1
        elif path.suffix == ".rs" and _RENDER_RETURN.search(_read(path)):
            stack.pages += 1
    return stack


def _detect_qt(root: Path, files: Iterable[Path]) -> Stack | None:
    widgets = [p for p in files if p.suffix.lower() in _QT_SUFFIXES]
    if not widgets:
        return None
    return Stack(name="qt", pages=len(widgets), root=str(root),
                 notes=["A Qt widget tree is deterministic to read - .ui is XML."])


_QT_PY = re.compile(r"\b(?:QMainWindow|QWidget|QDialog)\b")
_QT_IMPORT = re.compile(r"\b(?:PyQt6|PyQt5|PySide6|PySide2)\b")


def _detect_pyqt(root: Path, files: Iterable[Path]) -> Stack | None:
    """A Python desktop app: widgets are the pages."""
    widgets = 0
    for path in files:
        if path.suffix != ".py":
            continue
        text = _read(path, 60_000)
        if _QT_IMPORT.search(text) and _QT_PY.search(text):
            widgets += 1
    if not widgets:
        return None
    return Stack(name="pyqt", pages=widgets, root=str(root),
                 notes=["Desktop app: each window is a page; widgets are the components."])


def _detect_html(root: Path, files: Iterable[Path], *, django_present: bool) -> Stack | None:
    pages = [p for p in files if p.suffix.lower() in _HTML_SUFFIXES and "templates" in p.parts]
    if not pages:
        return None
    if django_present:
        # Django templates are the django stack's; do not double count them.
        return None
    return Stack(name="html", pages=len(pages), root=str(root))


def detect_stacks(root: str | Path) -> list[Stack]:
    """Every stack present in a project, most page-bearing first. Never raises."""
    project = Path(str(root or "")).expanduser()
    if not str(root or "").strip() or not project.is_dir():
        return []
    files = _walk(project)

    stacks: list[Stack] = []
    for detect in (_detect_django,):
        found = detect(project, files)
        if found:
            stacks.append(found)
    django_present = any(s.name == "django" for s in stacks)
    for detect in (_detect_js, _detect_rust, _detect_qt, _detect_pyqt):
        found = detect(project, files)
        if found:
            stacks.append(found)
    html = _detect_html(project, files, django_present=django_present)
    if html:
        stacks.append(html)

    stacks.sort(key=lambda s: (not s.serves_pages, -s.pages, s.name))
    return stacks


def page_stack(root: str | Path) -> Stack | None:
    """The stack most likely to hold this project's pages, or None."""
    for stack in detect_stacks(root):
        if stack.serves_pages:
            return stack
    return None


def summary(stacks: list[Stack]) -> dict[str, Any]:
    """A one-line verdict for the UI, so a blank plan can explain itself."""
    if not stacks:
        return {"stacks": [], "verdict": "No recognised stack found; the generic scan will be used."}
    pages = page_stack_from(stacks)
    if pages is None:
        names = ", ".join(s.name for s in stacks)
        return {
            "stacks": [s.as_dict() for s in stacks],
            "verdict": f"Found {names}, but none of them serves pages. This project is data-only.",
        }
    others = [s.name for s in stacks if s is not pages]
    verdict = f"Pages come from {pages.name} ({pages.pages} files)."
    if others:
        verdict += f" Also present: {', '.join(others)}."
    return {"stacks": [s.as_dict() for s in stacks], "verdict": verdict}


def page_stack_from(stacks: list[Stack]) -> Stack | None:
    for stack in stacks:
        if stack.serves_pages:
            return stack
    return None
