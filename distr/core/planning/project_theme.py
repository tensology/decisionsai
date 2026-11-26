"""Derive a base template from the project's own design decisions.

The library shipped a base template I invented. The project already has one - its
real colours, type and shell - and that is what a stencil should inherit, so the
render looks like the product rather than a generic wireframe.

Sources, in order of reliability:

1. **CSS custom properties** (`:root { --accent: #2563eb }`) - the clearest signal,
   and most design systems have them.
2. **A Tailwind config** - `theme.extend.colors` and `fontFamily`.
3. **The base template's shell** - which landmarks it declares.

Best-effort and fail-closed: nothing found means no derived base template, and the
shipped default is used unchanged. A wrong theme is worse than a generic one.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

MAX_FILES = 300
MAX_CSS_BYTES = 400_000
_SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", "__pycache__", ".next", ".nuxt",
    "venv", ".venv", "coverage", "out", "target", "vendor", "vendors",
}

# The project's name for a decision -> ours. Ordered, first match wins.
_COLOUR_ALIASES = {
    "color.accent": ("--accent", "--primary", "--brand", "--color-primary", "--accent-color"),
    "color.ink": ("--text", "--foreground", "--ink", "--color-text", "--body-color"),
    "color.muted": ("--muted", "--muted-foreground", "--subtle", "--text-muted"),
    "color.line": ("--border", "--line", "--border-color", "--divider"),
    "color.surface": ("--card", "--surface", "--panel", "--bg-card"),
    "color.background": ("--background", "--bg", "--page-bg", "--bg-light-start"),
    "color.danger": ("--danger", "--error", "--destructive", "--red"),
}

_VAR = re.compile(r"--([a-z0-9-]+)\s*:\s*([^;{}]{1,80});", re.IGNORECASE)
_FONT_FAMILY = re.compile(r"font-family\s*:\s*([^;{}]{3,120});", re.IGNORECASE)
_HEX = re.compile(r"^#[0-9a-f]{3,8}$", re.IGNORECASE)
_RGB = re.compile(r"^rgba?\([0-9 ,.%]{3,40}\)$", re.IGNORECASE)
_HSL = re.compile(r"^hsla?\([0-9 ,.%deg]{3,40}\)$", re.IGNORECASE)


def _walk(root: Path) -> list[Path]:
    out: list[Path] = []
    try:
        for path in root.rglob("*"):
            if len(out) >= MAX_FILES:
                break
            if not path.is_file():
                continue
            if any(part in _SKIP_DIRS or part.startswith(".") for part in path.relative_to(root).parts):
                continue
            out.append(path)
    except Exception:
        logger.debug("project-theme: could not walk %s", root, exc_info=True)
    return out


def _is_colour(value: str) -> bool:
    text = (value or "").strip()
    return bool(_HEX.match(text) or _RGB.match(text) or _HSL.match(text))


def _css_variables(root: Path, files: Iterable[Path]) -> dict[str, str]:
    """Every `--name: value` the project declares, plus a font family."""
    found: dict[str, str] = {}
    # Prefer a tokens file when there is one; it is usually the source of truth.
    ordered = sorted(
        (p for p in files if p.suffix.lower() == ".css"),
        key=lambda p: (0 if "token" in p.name.lower() else 1, str(p)),
    )
    for path in ordered:
        try:
            if path.stat().st_size > MAX_CSS_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, value in _VAR.findall(text):
            found.setdefault(f"--{name.lower()}", value.strip())
        if "--font" not in found:
            match = _FONT_FAMILY.search(text)
            if match and "var(" not in match.group(1):
                found["--font"] = match.group(1).strip()
    return found


def _tailwind(root: Path, files: Iterable[Path]) -> dict[str, Any]:
    """`theme.extend.colors` / `fontFamily` from a Tailwind config, if present."""
    out: dict[str, Any] = {}
    for path in files:
        if not path.name.startswith("tailwind.config"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:60_000]
        except OSError:
            continue
        for key in ("accent", "primary", "brand", "background", "foreground", "card", "border", "muted"):
            match = re.search(rf"""\b{key}\s*:\s*['"](#[0-9a-fA-F]{{3,8}})['"]""", text)
            if match:
                out.setdefault(key, match.group(1))
        fonts = re.search(r"fontFamily\s*:\s*\{([^}]{0,300})\}", text)
        if fonts:
            out.setdefault("fontFamily", fonts.group(1))
    return out


def _tokens(vars_: dict[str, str], tailwind: dict[str, Any]) -> dict[str, Any]:
    tokens: dict[str, Any] = {}
    for ours, aliases in _COLOUR_ALIASES.items():
        for alias in aliases:
            value = vars_.get(alias)
            if value and _is_colour(value):
                tokens[ours] = value
                break
    # Tailwind is a fallback for anything CSS did not state.
    for key, ours in (
        ("accent", "color.accent"), ("primary", "color.accent"), ("brand", "color.accent"),
        ("foreground", "color.ink"), ("muted", "color.muted"), ("border", "color.line"),
        ("card", "color.surface"), ("background", "color.background"),
    ):
        value = tailwind.get(key)
        if value and ours not in tokens and _is_colour(str(value)):
            tokens[ours] = str(value)
    font = vars_.get("--font")
    if font:
        tokens["font.family"] = font[:120]
    return tokens


def derive_base_template(root: str | Path) -> dict[str, Any] | None:
    """A base-template manifest from the project's own design decisions.

    Returns None when the project states nothing usable - a wrong theme is worse
    than a generic one.
    """
    project = Path(str(root or "")).expanduser()
    if not str(root or "").strip() or not project.is_dir():
        return None

    files = _walk(project)
    vars_ = _css_variables(project, files)
    tailwind = _tailwind(project, files)
    tokens = _tokens(vars_, tailwind)

    if len(tokens) < 2:
        logger.info("project-theme: %s states too little to derive a base template", project)
        return None

    name = re.sub(r"[^a-z0-9]+", "-", project.name.lower()).strip("-") or "project"
    return {
        "name": f"{name}-base",
        "kind": "base-template",
        "title": f"{project.name} base template",
        "scope": "global",
        "tags": ["base", "tokens", "derived"],
        "description": (
            "Derived from this project's own CSS custom properties"
            + (" and Tailwind config" if tailwind else "")
            + ". Review it; it is a reading of the project, not a spec."
        ),
        "wire": f'screen "{project.name} base"\n  heading "{project.name}"\n  text "Tokens read from the project."',
        "config": {"tokens": tokens},
    }


def save_as_project_entry(root: str | Path, planning_root: str | Path) -> dict[str, Any]:
    """Derive the base template and write it into the project's planning library."""
    manifest = derive_base_template(root)
    if manifest is None:
        return {"saved": False, "reason": "nothing to derive"}
    try:
        from distr.core.planning import plan_store
        from distr.core.planning.library import parse_manifest

        entry = parse_manifest(manifest, source="user")
        if entry is None:
            return {"saved": False, "reason": "derived manifest was unusable"}
        result = plan_store.save_project_entry(planning_root, entry)
        return {**result, "name": entry.name, "tokens": manifest["config"]["tokens"]}
    except Exception as exc:
        logger.warning("project-theme: could not save the derived base template", exc_info=True)
        return {"saved": False, "reason": str(exc)}
