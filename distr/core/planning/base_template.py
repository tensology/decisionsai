"""The base template: every design decision a page inherits.

**The base owns every decision. A page owns only placement.**

Three parts:

- ``tokens``    - colour, type scale, spacing, radii, shadows. Rendered as CSS
                  custom properties, so one edit propagates to every page.
- ``contracts`` - component behaviour: whether an input renders with a *legend*
                  or a label, how a table looks, whether ``content`` renders
                  markdown. Rendered as CSS rules.
- ``grid``      - the layout system pages place into.

Inheritance is **inherit-everything, override-by-exception**: ``resolve()`` deep
merges a page's overrides onto the base, so a page that changes nothing is
purely governed by the base.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

logger = logging.getLogger(__name__)

# A sensible starting point so a missing or broken base template still renders.
DEFAULT_TOKENS: dict[str, Any] = {
    "color.ink": "#1f2430",
    "color.muted": "#5c6577",
    "color.accent": "#3b6ef5",
    "color.line": "#d9dee8",
    "color.surface": "#ffffff",
    "color.background": "#f6f7f9",
    "color.danger": "#c0392b",
    "font.family": "system-ui, -apple-system, Segoe UI, sans-serif",
    "font.size.1": "12px",
    "font.size.2": "14px",
    "font.size.3": "16px",
    "font.size.4": "20px",
    "font.size.5": "28px",
    "font.weight.normal": "400",
    "font.weight.strong": "600",
    "space.1": "4px",
    "space.2": "8px",
    "space.3": "16px",
    "space.4": "24px",
    "space.5": "32px",
    "radius.1": "4px",
    "radius.2": "8px",
    "shadow.1": "0 1px 2px rgba(16,24,40,0.06)",
    "shadow.2": "0 6px 16px rgba(16,24,40,0.10)",
}

DEFAULT_CONTRACTS: dict[str, Any] = {
    # The form standard: an input renders with a legend rather than a label.
    "form.field": "legend",          # legend | label
    "form.required_marker": "*",
    "form.inline_help": True,
    "table.density": "comfortable",  # compact | comfortable
    "table.striped": False,
    "table.header_weight": "strong",
    "content.markdown": True,
    "button.primary_style": "filled",  # filled | outline
    "nav.style": "top",                # top | side
    "head.show": True,
    "footer.show": True,
}

DEFAULT_GRID: dict[str, Any] = {
    "columns": 12,
    "gap": "16px",
    "max_width": "1200px",
    "page_padding": "24px",
    "breakpoints": {"sm": "640px", "md": "900px", "lg": "1200px"},
}

CONTRACT_CHOICES = {
    "form.field": ("legend", "label"),
    "table.density": ("compact", "comfortable"),
    "button.primary_style": ("filled", "outline"),
    "nav.style": ("top", "side"),
}


@dataclass(frozen=True)
class BaseTemplate:
    """Tokens + contracts + grid. Immutable; ``resolve`` returns a new one."""

    name: str = "default-base"
    tokens: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_TOKENS))
    contracts: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_CONTRACTS))
    grid: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_GRID))

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tokens": dict(self.tokens),
            "contracts": dict(self.contracts),
            "grid": dict(self.grid),
        }


def default_base() -> BaseTemplate:
    return BaseTemplate()


def _merge(base: dict[str, Any], over: Any) -> dict[str, Any]:
    """Deep merge ``over`` onto ``base``. Unknown keys pass through untouched."""
    out = dict(base)
    if not isinstance(over, dict):
        return out
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _coerce_contracts(raw: Any, base: dict[str, Any]) -> dict[str, Any]:
    """Apply contract overrides, ignoring values outside the allowed set."""
    merged = _merge(base, raw)
    if not isinstance(raw, dict):
        return merged
    for key, allowed in CONTRACT_CHOICES.items():
        if key in raw:
            value = str(raw[key]).strip().lower()
            if value in allowed:
                merged[key] = value
            else:
                logger.warning(
                    "base-template: %s=%r is not one of %s; keeping %r",
                    key, raw[key], ", ".join(allowed), base.get(key),
                )
                merged[key] = base.get(key)
    return merged


def _coerce_grid(raw: Any, base: dict[str, Any]) -> dict[str, Any]:
    merged = _merge(base, raw)
    if isinstance(raw, dict) and "columns" in raw:
        try:
            columns = int(raw["columns"])
        except (TypeError, ValueError):
            columns = None
        if columns is None or not (1 <= columns <= 24):
            logger.warning("base-template: grid.columns=%r is not 1..24; keeping %r", raw["columns"], base.get("columns"))
            merged["columns"] = base.get("columns")
        else:
            merged["columns"] = columns
    return merged


def parse_base(raw: Any, *, name: str = "default-base") -> BaseTemplate:
    """Build a base template from a manifest ``config`` block.

    Missing or malformed parts fall back to the defaults, so a partially written
    base template still renders. Never raises.
    """
    config = raw if isinstance(raw, dict) else {}
    base = default_base()
    tokens = _merge(base.tokens, config.get("tokens"))
    contracts = _coerce_contracts(config.get("contracts"), base.contracts)
    grid = _coerce_grid(config.get("grid"), base.grid)
    return BaseTemplate(name=name or base.name, tokens=tokens, contracts=contracts, grid=grid)


def from_entry(entry: Any) -> BaseTemplate:
    """Build a base template from a library entry (``kind == 'base-template'``)."""
    if entry is None:
        return default_base()
    try:
        config = getattr(entry, "config", None) or {}
        return parse_base(config, name=getattr(entry, "name", "") or "default-base")
    except Exception:
        logger.warning("base-template: could not read entry; using the default", exc_info=True)
        return default_base()


def load(name: str | None = None, *, library_module: Any = None) -> BaseTemplate:
    """Load a base template by library name, falling back to the default."""
    try:
        if library_module is None:
            from distr.core.planning import library as library_module  # type: ignore[no-redef]

        if name:
            entry = library_module.get(name)
            if entry is not None:
                return from_entry(entry)
            logger.warning("base-template: %r not found; using the shipped default", name)
        # No name given (or not found): use the first base template the library
        # offers, so a page always has something to inherit from.
        for entry in library_module.entries(kind="base-template"):
            return from_entry(entry)
    except Exception:
        logger.warning("base-template: load failed; using the default", exc_info=True)
    return default_base()


def resolve(base: BaseTemplate, overrides: Any) -> BaseTemplate:
    """Apply a page's overrides to the base. Override-by-exception.

    A page that overrides nothing returns the base unchanged; a page that
    overrides one token changes only that token.
    """
    if not isinstance(overrides, dict) or not overrides:
        return base
    return BaseTemplate(
        name=base.name,
        tokens=_merge(base.tokens, overrides.get("tokens")),
        contracts=_coerce_contracts(overrides.get("contracts"), base.contracts),
        grid=_coerce_grid(overrides.get("grid"), base.grid),
    )


def _css_var_name(key: str) -> str:
    return "--wf-" + key.replace(".", "-").replace("_", "-")


def to_css(base: BaseTemplate) -> str:
    """Render the base as CSS for the wireframe frame: vars, contracts, grid.

    This is the only place a design decision becomes pixels, so a change here
    reaches every page that inherits the base.
    """
    lines: list[str] = [":root {"]
    for key in sorted(base.tokens):
        lines.append(f"  {_css_var_name(key)}: {base.tokens[key]};")
    lines.append("}")

    grid = base.grid
    lines.append("")
    lines.append(".wf-grid {")
    lines.append(f"  display: grid; grid-template-columns: repeat({grid.get('columns', 12)}, minmax(0, 1fr));")
    lines.append(f"  gap: {grid.get('gap', '16px')};")
    lines.append(f"  max-width: {grid.get('max_width', '1200px')};")
    lines.append(f"  padding: {grid.get('page_padding', '24px')};")
    lines.append("  margin-inline: auto;")
    lines.append("}")
    breakpoints = grid.get("breakpoints") if isinstance(grid.get("breakpoints"), dict) else {}
    for name, width in sorted(breakpoints.items()):
        lines.append(f"@media (min-width: {width}) {{ .wf-grid--{name} {{ grid-template-columns: repeat({grid.get('columns', 12)}, minmax(0, 1fr)); }} }}")

    contracts = base.contracts
    lines.append("")
    lines.append("body { background: var(--wf-color-background); color: var(--wf-color-ink);")
    lines.append(f"  font-family: var(--wf-font-family); font-size: var(--wf-font-size-3); }}")
    lines.append("table { border-collapse: collapse; width: 100%; box-shadow: var(--wf-shadow-1); }")
    lines.append("th { font-weight: var(--wf-font-weight-strong); text-align: left; }")
    density = "6px 10px" if contracts.get("table.density") == "compact" else "10px 14px"
    lines.append(f"th, td {{ padding: {density}; border-bottom: 1px solid var(--wf-color-line); }}")
    if contracts.get("table.striped"):
        lines.append("tbody tr:nth-child(even) { background: var(--wf-color-background); }")

    # The form standard: a legend, or a label.
    if contracts.get("form.field") == "legend":
        lines.append("fieldset { border: 1px solid var(--wf-color-line); border-radius: var(--wf-radius-2);")
        lines.append("  padding: var(--wf-space-3); margin: 0 0 var(--wf-space-3); }")
        lines.append("legend { font-weight: var(--wf-font-weight-strong); padding-inline: var(--wf-space-1); }")
    else:
        lines.append("fieldset { border: 0; padding: 0; margin: 0 0 var(--wf-space-3); }")
        lines.append("label { display: block; font-weight: var(--wf-font-weight-strong); margin-bottom: var(--wf-space-1); }")

    if contracts.get("content.markdown"):
        lines.append("pre, code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;")
        lines.append("  background: var(--wf-color-background); border-radius: var(--wf-radius-1); }")
        lines.append("blockquote { border-left: 3px solid var(--wf-color-line); margin: 0; padding-left: var(--wf-space-3); }")

    lines.append("")
    lines.append(".wf-button { border-radius: var(--wf-radius-1); padding: 6px 14px; }")
    if contracts.get("button.primary_style") == "outline":
        lines.append(".wf-button--primary { background: transparent; border: 1px solid var(--wf-color-accent); color: var(--wf-color-accent); }")
    else:
        lines.append(".wf-button--primary { background: var(--wf-color-accent); color: var(--wf-color-surface); border: 0; }")
    return "\n".join(lines) + "\n"


def for_page(name: str | None = None, overrides: Any = None) -> BaseTemplate:
    """Convenience: load a base template and apply a page's overrides."""
    return resolve(load(name), overrides)
