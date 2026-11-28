"""The base template's stencil: the typography, colours and form conventions a site's pages share.

The layout stencil (``layout_probe``) is grey on purpose: it says where things go. This says how they
look. It is read from the same captured pages, takes the most common value for each role (so one odd
page does not set the standard), and comes out as design tokens, form-element contracts, CSS the page
renderer can use, and a small style-guide stencil.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

_HEADING = re.compile(r"^h[1-6]$")


def _hex(color: Any) -> str:
    if not isinstance(color, (list, tuple)) or len(color) < 3:
        return ""
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(v))) for v in color[:3]))


def _mode(values: list[Any], default: Any = None) -> Any:
    values = [v for v in values if v not in (None, "", ())]
    return Counter(values).most_common(1)[0][0] if values else default


def _spacing(pad: Any) -> str:
    if not pad or not any(pad):
        return ""
    t, r, b, l = (round(v) for v in pad)
    return f"{t}px {r}px {b}px {l}px"


def summarise(raw: dict[str, Any]) -> dict[str, Any]:
    """The look of one page at desktop width, by role."""
    desktop = (raw or {}).get("desktop") or {}
    records = desktop.get("records") or []
    if not records:
        return {}
    body = next((r for r in records if r.get("t") == "body"), {})
    area = Counter()
    for r in records:
        if r.get("bg") and r.get("t") != "body":
            w, h = (r.get("r") or [0, 0, 0, 0])[2:4]
            area[_hex(r["bg"])] += max(0, w) * max(0, h) / 1000
    text_colors = Counter(_hex(r.get("fg2")) for r in records if r.get("tb") and r.get("fg2"))
    sizes = Counter(round(r["fz"]) for r in records if r.get("tb") and r.get("fz"))

    def role(tag_ok):
        return [r for r in records if tag_ok(r)]

    def typo(rows):
        if not rows:
            return {}
        return {"family": _mode([r.get("ff") for r in rows], ""), "size": _mode([round(r.get("fz") or 0) for r in rows], 0),
                "weight": _mode([r.get("fw") or 400 for r in rows], 400), "line": _mode([round(r.get("lh") or 0) for r in rows], 0),
                "color": _hex(_mode([tuple(r.get("fg2") or ()) for r in rows], ()) or None)}

    headings = {}
    for level in range(1, 7):
        rows = role(lambda r, tag=f"h{level}": r.get("t") == tag and r.get("tb"))
        if rows:
            headings[f"h{level}"] = typo(rows)
    paragraphs = typo(role(lambda r: r.get("t") in ("p", "li", "span", "div") and r.get("tb") and not r.get("fb") and (r.get("fz") or 0) < 20))
    links = typo(role(lambda r: r.get("t") == "a" and r.get("tb") and r.get("txt")))
    buttons_rows = role(lambda r: (r.get("t") == "button" or (r.get("t") == "a" and r.get("bg") and (r.get("pad") or [0])[0] >= 6 and r.get("tb"))
                                   or (r.get("t") == "input" and r.get("it") in ("submit", "button")))
                        and 14 <= (r.get("r") or [0, 0, 0, 0])[3] <= 80 and (r.get("r") or [0, 0, 0, 0])[2] <= 420 and r.get("bg"))
    button = {}
    if buttons_rows:
        button = {**typo(buttons_rows), "bg": _hex(_mode([tuple(r.get("bg") or ()) for r in buttons_rows], ()) or None),
                  "radius": _mode([round(r.get("rad") or 0) for r in buttons_rows], 0), "pad": _mode([_spacing(r.get("pad")) for r in buttons_rows], ""),
                  "height": _mode([round((r.get("r") or [0, 0, 0, 0])[3]) for r in buttons_rows], 0)}
    field_rows = role(lambda r: r.get("t") in ("input", "select", "textarea") and r.get("it") not in ("hidden", "submit", "button", "checkbox", "radio", "image"))
    field = {}
    if field_rows:
        bd = [tuple(r.get("bd") or r.get("bdw") or ()) for r in field_rows]
        field = {**typo(field_rows), "bg": _hex(_mode([tuple(r.get("bg") or ()) for r in field_rows], ()) or None),
                 "radius": _mode([round(r.get("rad") or 0) for r in field_rows], 0), "pad": _mode([_spacing(r.get("pad")) for r in field_rows], ""),
                 "height": _mode([round((r.get("r") or [0, 0, 0, 0])[3]) for r in field_rows if r.get("t") != "textarea"], 0),
                 "border": _mode([max(b) if b else 0 for b in bd], 0), "shadow": any(r.get("sh") for r in field_rows)}
    # Label placement: a label sitting above its field (legend style) or beside it.
    labels = role(lambda r: r.get("t") in ("label", "legend") and r.get("tb"))
    placement = ""
    if labels and field_rows:
        above = beside = 0
        for lab in labels[:40]:
            lx, ly, lw, lh = lab.get("r") or [0, 0, 0, 0]
            for fld in field_rows:
                fx, fy, fw, fh = fld.get("r") or [0, 0, 0, 0]
                if abs((fy - (ly + lh)) ) <= 14 and abs(fx - lx) <= 24 and fy >= ly:
                    above += 1
                    break
                if abs(fy - ly) <= 14 and fx >= lx + lw - 4 and fx - (lx + lw) <= 60:
                    beside += 1
                    break
        placement = "above" if above >= beside and above else ("beside" if beside else "")
    page_bg = _hex((desktop.get("bg") or [255, 255, 255]))
    return {"font": _mode([r.get("ff") for r in role(lambda r: r.get("tb"))], body.get("ff") or ""), "size": sizes.most_common(1)[0][0] if sizes else 14,
            "text": paragraphs.get("color") or (text_colors.most_common(1)[0][0] if text_colors else ""), "page_bg": page_bg,
            "surfaces": [c for c, _ in area.most_common(6)], "headings": headings, "paragraph": paragraphs, "link": links,
            "button": button, "field": field, "label": placement,
            "radius": _mode([round(r.get("rad") or 0) for r in records if r.get("rad") and r.get("bg")], 0),
            "gap": _mode([round(r.get("rg") or r.get("cg") or 0) for r in records if (r.get("rg") or r.get("cg"))], 0)}


def aggregate(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """The most common look across a site's captured pages."""
    summaries = [s for s in summaries if s]
    if not summaries:
        return {}
    out: dict[str, Any] = {"pages": len(summaries)}
    for key in ("font", "size", "text", "page_bg", "label", "radius", "gap"):
        out[key] = _mode([s.get(key) for s in summaries], "")
    out["surfaces"] = [c for c, _ in Counter(c for s in summaries for c in s.get("surfaces", [])).most_common(6)]
    for key in ("paragraph", "link", "button", "field"):
        rows = [s.get(key) for s in summaries if s.get(key)]
        out[key] = {k: _mode([row.get(k) for row in rows], "") for k in (rows[0] if rows else {})} if rows else {}
    heads = {}
    for level in range(1, 7):
        rows = [s["headings"][f"h{level}"] for s in summaries if f"h{level}" in (s.get("headings") or {})]
        if rows:
            heads[f"h{level}"] = {k: _mode([row.get(k) for row in rows], "") for k in rows[0]}
    out["headings"] = heads
    return out


def tokens(style: dict[str, Any]) -> dict[str, str]:
    """Design tokens for ``base_template`` (colour, type scale, radii)."""
    if not style:
        return {}
    out = {"font.family": f'"{style["font"]}", system-ui, sans-serif' if style.get("font") else "",
           "color.ink": style.get("text") or "", "color.background": style.get("page_bg") or ""}
    link = style.get("link") or {}
    button = style.get("button") or {}
    out["color.accent"] = button.get("bg") or link.get("color") or ""
    field = style.get("field") or {}
    out["radius.1"] = f'{field.get("radius")}px' if field.get("radius") not in ("", None) else ""
    out["radius.2"] = f'{button.get("radius")}px' if button.get("radius") not in ("", None) else ""
    sizes = sorted({v.get("size") for v in (style.get("headings") or {}).values() if v.get("size")} | {style.get("size") or 0})
    for index, size in enumerate([s for s in sizes if s][:5], start=1):
        out[f"font.size.{index}"] = f"{size}px"
    return {k: v for k, v in out.items() if v}


def contracts(style: dict[str, Any]) -> dict[str, Any]:
    field = style.get("field") or {}
    return {"form.field": "legend" if style.get("label") == "above" else ("label" if style.get("label") else ""),
            "form.input_height": field.get("height") or 0, "form.input_border": field.get("border") or 0}


def to_css(style: dict[str, Any]) -> str:
    """Compact CSS a page renderer can start from: type, colours, buttons, fields."""
    if not style:
        return ""
    lines = [f'body{{margin:0;font:{style.get("size") or 14}px/1.45 "{style.get("font") or "system-ui"}",system-ui,sans-serif;'
             f'color:{style.get("text") or "#222"};background:{style.get("page_bg") or "#fff"}}}']
    for tag, row in (style.get("headings") or {}).items():
        lines.append(f'{tag}{{font:{row.get("weight") or 600} {row.get("size") or 20}px/{row.get("line") or 1.2}px "{row.get("family") or style.get("font") or "system-ui"}",system-ui,sans-serif;color:{row.get("color") or "inherit"}}}')
    link = style.get("link") or {}
    if link.get("color"):
        lines.append(f'a{{color:{link["color"]};text-decoration:none}}')
    b = style.get("button") or {}
    if b:
        lines.append(f'button,.btn{{font:{b.get("weight") or 500} {b.get("size") or 14}px "{b.get("family") or style.get("font") or "system-ui"}",system-ui,sans-serif;'
                     f'color:{b.get("color") or "#fff"};background:{b.get("bg") or "#3b6ef5"};border:0;border-radius:{b.get("radius") or 0}px;'
                     f'padding:{b.get("pad") or "10px 16px"};min-height:{b.get("height") or 36}px}}')
    f = style.get("field") or {}
    if f:
        lines.append(f'input,select,textarea{{font:{f.get("size") or 14}px "{f.get("family") or style.get("font") or "system-ui"}",system-ui,sans-serif;'
                     f'color:{f.get("color") or "inherit"};background:{f.get("bg") or "#fff"};border:{f.get("border") or 1}px solid #cfd5df;'
                     f'border-radius:{f.get("radius") or 0}px;padding:{f.get("pad") or "8px 10px"};min-height:{f.get("height") or 36}px;box-sizing:border-box}}')
    return "\n".join(lines)


def stencil_lines(style: dict[str, Any], project: str = "Site") -> list[str]:
    """A style-guide screen in the layout DSL: the type scale, a button, and a form field, at real sizes."""
    if not style:
        return []
    name = re.sub(r'["\\\x00-\x1f\x7f]', " ", project)[:60]
    lines = [f'screen "Base template · {name}" device=desktop layer=guidance layout=v2 vw=1000 ph=900 bg=white',
             '  stack pad="24 32" gap=16 flow=col']
    for tag, row in (style.get("headings") or {}).items():
        size = max(8, min(120, int(row.get("size") or 20)))
        lh = max(8, min(200, int(row.get("line") or round(size * 1.2))))
        lines.append(f'    heading "{tag}" tw=40 fs={size} lh={lh}' + (" bold=true" if int(row.get("weight") or 400) >= 600 else ""))
    para = style.get("paragraph") or {}
    size = max(8, min(60, int(para.get("size") or style.get("size") or 14)))
    lines.append(f'    text "Body text" lines=3 fs={size} lh={max(size, int(para.get("line") or round(size * 1.45)))}')
    lines.append(f'    link "Link" w=60 fs={size}')
    b = style.get("button") or {}
    if b:
        lines.append(f'    button "Button" w=120 h={max(20, min(80, int(b.get("height") or 36)))} fill=accent radius={min(99, int(b.get("radius") or 0))} tone=light ta=center')
    f = style.get("field") or {}
    if f:
        fh = max(20, min(80, int(f.get("height") or 36)))
        field_line = f'input "Field" h={fh} fill=white line=trbl radius={min(99, int(f.get("radius") or 0))} type=text'
        if style.get("label") == "above":
            lines += ['    stack gap=6', '      text "Label" w=80 fs=12', f'      {field_line}']
        else:
            lines += ['    row gap=12 align=center', '      text "Label" w=80 fs=12', f'      {field_line.replace("h=", "grow=1 h=", 1)}']
    return lines
