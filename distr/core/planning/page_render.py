"""Dry-run page render: Decisions' own planning route turns a stencil plus the base template into a page.

The stencil (layout DSL) says where things go; the base template says how they look. This sends both, and
the page's controls, through ``planning.runtime`` (the proposal-only route: the model gets no tools and no
repository access) and writes the HTML it returns to a scratch folder. It never writes into a project:
``render_page`` refuses an output folder inside any project's folder.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from distr.core.planning import base_style, flow_map

logger = logging.getLogger(__name__)

MAX_HTML = 400_000
MAX_STENCIL_CHARS = 26_000

DSL_GUIDE = """Reading the stencil (Decisions layout DSL, indentation = nesting):
- Types: stack/section/main/header/nav/footer/sidebar/form/list/item/card/dialog/hero/box are containers; row = flex row; grid = CSS grid; text/heading/link/button/input/select/textarea/checkbox/radio/image/divider are leaves.
- flow=col|row|grid|block sets the container's layout; columns=N is N equal grid columns; tracks="a,b,c" lists grid tracks (px, %, fr, auto); gap/rgap/cgap are gaps in px; wrap=true wraps; justify=start|center|end|between|around|evenly; align=start|center|end|stretch|baseline.
- w=px|N%|fill is the element's width (fill = 100%); grow=N is flex-grow (flex: N 1 0%); maxw/minh are max-width/min-height in px; h is height in px; ratio=W/H is the aspect ratio of an image box; span=N spans N grid columns.
- mar="t r b l" and pad="t r b l" are px (mar may use auto: centring); pos=abs|fixed|sticky with top/left in px; fill=white|light|mid|dark|accent|image is a surface (map to the base template's colours: white = page surface, light/mid = panels, dark = footers/dark bars, accent = brand/button colour, image = a photo); line=trbl are borders on those sides; radius is px; shadow=true is a soft shadow.
- Text leaves: the quoted label is the real text. tw=N is the text's width as % of the box, lines=N is the line count, fs=px font size, lh=px line height, bold=true, ta=center|right, tone=muted|light (light = text on a dark fill).
- rep=N means this element repeats N times (identical siblings): render N copies with plausible different content.
- at768="key:value;..." overrides those keys on screens 768px wide or narrower; "key:-" removes that key there. show=desktop hides the element at <=768px; show=mobile hides it above 768px; show=auth|guest: signed-in or signed-out only (show the guest version).
- approx=true marks a box that stands for deeper content the stencil did not expand; clip=true means overflow hidden (a scrolled window onto longer content).
- route= on links/buttons is where they go: use it as the href. `bind=`, `variant=`, `requirement=` are annotations.
The stencil's page is drawn 1440px wide on desktop."""

SYSTEM = (
    "You are the Build agent of DecisionsAI doing a DRY-RUN page render. You receive one page's layout stencil, the site's base template "
    "(typography, colours, form conventions) and the page's controls. Write ONE self-contained HTML document that reproduces the stencil's layout "
    "exactly (same regions, order, nesting, columns, widths, gaps, alignment, heights) using the base template for every colour, font and form style. "
    'Reply with ONLY a JSON object {"html": "<the full document>"} and nothing else.\n'
    "Rules: inline <style> only; no JavaScript, no external URLs, no web fonts (use the base template's font-family with generic fallbacks); "
    "images are grey boxes (a div with the stencil's size or aspect ratio and a light grey background), never <img> with a URL; "
    "text uses the stencil's labels, at the stencil's sizes; reproduce the phone overrides with one @media (max-width:768px) block; "
    "links use the route as href; forms use the controls list for action/method/field names. Do not add anything the stencil does not contain."
)


def forbidden_roots() -> list[Path]:
    """Every project folder: a dry-run render may never write inside one."""
    roots: list[Path] = []
    try:
        from distr.core.db import get_session
        from distr.core.db.projects import Project

        with get_session() as session:
            for project in session.query(Project).all():
                folder = str(getattr(project, "folder_location", "") or "").strip()
                if folder:
                    roots.append(Path(folder).expanduser().resolve())
    except Exception:
        logger.debug("page_render: project folders not listed", exc_info=True)
    return roots


def check_output_dir(out_dir: Path | str, extra_forbidden: list[Path] | None = None) -> Path:
    target = Path(out_dir).expanduser().resolve()
    for root in [*forbidden_roots(), *(extra_forbidden or [])]:
        if target == root or root in target.parents:
            raise ValueError(f"Dry-run output must not be inside a project folder ({root.name}).")
    target.mkdir(parents=True, exist_ok=True)
    return target


def pick_tier(layouts: dict[str, list[str]], limit: int = MAX_STENCIL_CHARS) -> list[str]:
    """The richest stored stencil that fits the prompt."""
    for tier in sorted(layouts, key=int, reverse=True):
        if len("\n".join(layouts[tier])) <= limit:
            return layouts[tier]
    smallest = layouts[min(layouts, key=int)] if layouts else []
    return smallest


def build_messages(*, route: str, stencil: list[str], style: dict[str, Any], controls: dict[str, Any] | None) -> list[dict[str, str]]:
    css = base_style.to_css(style) or "(no base template captured)"
    tokens = base_style.tokens(style)
    contracts = base_style.contracts(style)
    controls_md = flow_map.controls_markdown(route, controls, None) if controls else ""
    user = "\n\n".join([
        f"Page route: {route}",
        DSL_GUIDE,
        "BASE TEMPLATE CSS (use these values):\n```css\n" + css[:4000] + "\n```",
        "Base tokens: " + json.dumps(tokens, sort_keys=True)[:1500] + "\nForm conventions: " + json.dumps(contracts, sort_keys=True),
        "PAGE CONTROLS:\n" + (controls_md[:7000] or "(none recorded)"),
        "LAYOUT STENCIL:\n```\n" + "\n".join(stencil)[:MAX_STENCIL_CHARS] + "\n```",
        "Return only the JSON object.",
    ])
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


_BAD_ATTR = re.compile(r"""\s(?:on\w+|srcset)\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.I)
_EXTERNAL = re.compile(r"""(?:src|data|poster|action)\s*=\s*(["'])\s*(?:https?:)?//[^"']*\1""", re.I)


def sanitise_html(html: str) -> str:
    """Static markup only: no scripts, handlers, remote resources or imports."""
    html = re.sub(r"<script\b.*?</script\s*>", "", html, flags=re.S | re.I)
    html = re.sub(r"<(?:link|iframe|object|embed|base|meta\s+http-equiv)\b[^>]*>", "", html, flags=re.I)
    html = _BAD_ATTR.sub("", html)
    html = _EXTERNAL.sub("", html)
    html = re.sub(r"@import[^;]+;", "", html, flags=re.I)
    html = re.sub(r"url\(\s*['\"]?\s*(?:https?:)?//[^)]*\)", "none", html, flags=re.I)
    html = re.sub(r"javascript:", "#", html, flags=re.I)
    return html[:MAX_HTML]


def parse_reply(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json|html)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
        html = data.get("html") if isinstance(data, dict) else ""
    except ValueError:
        match = re.search(r"<!doctype html.*</html\s*>", text, flags=re.S | re.I)
        html = match.group(0) if match else ""
    if not isinstance(html, str) or "<" not in html:
        raise ValueError("The model did not return an HTML document.")
    return sanitise_html(html)


def render_page(*, project_id: int, project_name: str, route: str, name: str, stencil: list[str], style: dict[str, Any],
                controls: dict[str, Any] | None, out_dir: Path | str, provider: str, model: str, settings: dict[str, Any],
                note: str = "") -> dict[str, Any]:
    """Generate one page into ``out_dir`` (a scratch folder). Returns the file paths and what was sent."""
    from distr.core.planning import runtime

    target = check_output_dir(out_dir)
    safe = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")[:60] or "page"
    messages = build_messages(route=route, stencil=stencil, style=style, controls=controls)
    if note:
        messages[-1]["content"] += "\n\nFix these problems from the previous attempt:\n" + note
    reply = "".join(runtime.create_stream(provider, model, messages, settings, project_id=project_id, project_name=project_name,
                                         chat_id=0, turn_id=f"dryrun-{uuid.uuid4().hex[:8]}"))
    html = parse_reply(reply)
    path = target / f"{safe}.html"
    path.write_text(html, encoding="utf-8")
    (target / f"{safe}.prompt.json").write_text(json.dumps(messages, indent=1), encoding="utf-8")
    return {"html": str(path), "chars": len(html), "prompt_chars": sum(len(m["content"]) for m in messages)}
