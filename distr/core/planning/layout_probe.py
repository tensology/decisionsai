"""Reduce a rendered page's computed layout to stencil DSL (layout v2).

A stencil is a layout-faithful wireframe of the real page: the same regions, rows,
columns, grid tracks, gaps, widths, alignment and order. Text becomes grey lines and
images become grey blocks; colours and typography stay in the base template.

Pipeline (all in headless Firefox, never Brave):

1. ``read_page`` runs ``layout_probe.js`` at desktop width and again at phone width.
   Each run returns one raw record per visible element: its box plus the CSS that
   decides its layout (display, flex/grid rules, gaps, padding, margins, position).
2. ``build_tree`` joins the two reads by element key and prunes the DOM to the
   elements that carry layout: plain single-child wrappers collapse into their child,
   invisible empties drop, and identical siblings merge into one node with ``rep=N``.
3. ``to_lines`` writes the tree as wire DSL. Desktop rules are the base; where the phone
   read differs, the node carries an ``at768="key:value;..."`` override. A node that is
   hidden on one of the two widths carries ``show=desktop`` or ``show=mobile``.

Everything is bounded: a record cap in the probe, a node budget here, and no network
beyond the one page load. Failure means no layout, never a guessed one.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

PROBE_JS = Path(__file__).with_name("layout_probe.js").read_text(encoding="utf-8")
DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}
BREAKPOINT = 768
DEFAULT_BUDGET = 300
LEAF_TAGS = {"img", "svg", "canvas", "video", "picture", "iframe", "input", "select", "textarea", "hr"}
MEDIA_TAGS = {"img", "svg", "canvas", "video", "picture", "iframe"}
SEMANTIC = {"header": "header", "nav": "nav", "main": "main", "aside": "sidebar", "footer": "footer",
            "section": "section", "article": "section", "form": "form", "ul": "list", "ol": "list",
            "li": "item", "dialog": "dialog"}


def read_page(page: Any) -> dict[str, Any]:
    """Probe a loaded page at desktop width, then phone width. Never raises."""
    out: dict[str, Any] = {"desktop": {}, "mobile": {}}
    try:
        out["desktop"] = page.evaluate(PROBE_JS) or {}
    except Exception:
        return out
    try:
        page.set_viewport_size(dict(MOBILE))
        page.wait_for_timeout(700)
        out["mobile"] = page.evaluate(PROBE_JS) or {}
    except Exception:
        out["mobile"] = {}
    finally:
        try:
            page.set_viewport_size(dict(DESKTOP))
        except Exception:
            pass
    return out


# ---------------------------------------------------------------- tree

class Node:
    __slots__ = ("key", "d", "m", "kids", "kind", "rep", "cut", "parent")

    def __init__(self, key: int, d: dict | None, m: dict | None):
        self.key, self.d, self.m = key, d, m
        self.kids: list[Node] = []
        self.kind = ""
        self.rep = 1
        self.cut = False
        self.parent: Node | None = None

    def rec(self, vp: str) -> dict | None:
        return self.d if vp == "d" else self.m

    @property
    def base(self) -> dict:
        return self.d or self.m or {}


def _box(rec: dict) -> list[float]:
    return rec.get("r") or [0, 0, 0, 0]


def _border(rec: dict) -> list[float]:
    return rec.get("bd") or rec.get("bdw") or [0, 0, 0, 0]


def _decorated(rec: dict) -> bool:
    return bool(rec.get("bg") or rec.get("bi") or rec.get("bd") or rec.get("sh"))


def _inline_level(rec: dict) -> bool:
    return str(rec.get("d") or "").startswith("inline")


def build_tree(desktop: dict, mobile: dict | None = None) -> Node | None:
    records_d = {r["k"]: r for r in (desktop or {}).get("records", [])}
    records_m = {r["k"]: r for r in (mobile or {}).get("records", [])}
    if not records_d:
        return None
    nodes = {k: Node(k, records_d.get(k), records_m.get(k)) for k in sorted(set(records_d) | set(records_m))}
    roots: list[Node] = []
    for key, node in nodes.items():
        parent = nodes.get((node.d or node.m or {}).get("p"))
        if parent is None or parent is node:
            roots.append(node)
        else:
            parent.kids.append(node)
            node.parent = parent
    if not roots:
        return None
    root = roots[0]
    for extra in roots[1:]:
        root.kids.append(extra)
        extra.parent = root
    root.kids.sort(key=lambda n: n.key)
    for node in nodes.values():
        node.kids.sort(key=lambda n: n.key)
    # The page root is the body: it spans the document, and its margin and padding are the page's own.
    for vp, raw in (("d", desktop), ("m", mobile)):
        rec = root.rec(vp)
        if rec and raw:
            rec["r"] = [0, 0, raw.get("vw") or rec["r"][2], raw.get("ph") or rec["r"][3]]
            rec["pad"] = [a + b for a, b in zip(rec.get("pad") or [0] * 4, rec.get("mar") or [0] * 4)]
            rec["mar"] = [0, 0, 0, 0]
    return root


def _positioned(rec: dict | None) -> bool:
    """Out of normal flow or pinned: position that changes where a box sits."""
    return bool(rec) and rec.get("ps") in ("absolute", "fixed", "sticky")


def _is_leaf(node: Node) -> bool:
    rec = node.base
    if rec.get("t") in LEAF_TAGS:
        return True
    if rec.get("t") == "button" and not node.kids:
        return True
    return bool(rec.get("tb")) and not any(_text_blocky(c) for c in node.kids)


def _text_blocky(node: Node) -> bool:
    return not _inline_level(node.base) and node.base.get("t") not in ("img", "svg", "br")


def _prune(node: Node, root: bool = False) -> None:
    if _is_leaf(node) and not root:
        node.kids = []
    for kid in node.kids:
        _prune(kid)
    out: list[Node] = []
    for kid in node.kids:
        out.extend(_absorb(kid, node))
    node.kids = out
    for kid in out:
        kid.parent = node


def _overlay(node: Node) -> bool:
    """A fixed panel as tall as the viewport is a scrim or drawer, not page layout."""
    for rec in (node.d, node.m):
        if rec and rec.get("ps") == "fixed" and _box(rec)[3] >= 800 and _box(rec)[2] >= 280:
            return True
    return False


def _absorb(kid: Node, parent: Node) -> list[Node]:
    rec = kid.base
    if _overlay(kid):
        return []
    if rec.get("d") == "contents" and not kid.m or (rec.get("d") == "contents" and (not kid.d or kid.d.get("d") == "contents")):
        return list(kid.kids)
    for vp in ("d", "m"):
        r = kid.rec(vp)
        if r and r.get("d", "").startswith("table-") and r["d"] in ("table-row-group", "table-header-group", "table-footer-group"):
            return list(kid.kids)
    if not kid.kids:
        if _is_leaf(kid):
            return [kid] if _has_size(kid) else []
        if _has_size(kid) and any(_decorated(r) for r in (kid.d, kid.m) if r):
            return [kid]
        return []
    if len(kid.kids) == 1 and _plain_wrapper(kid):
        child = kid.kids[0]
        for vp in ("d", "m"):
            wrap, inner = kid.rec(vp), child.rec(vp)
            if not wrap or not inner:
                continue
            inner["mar"] = [a + b for a, b in zip(inner.get("mar") or [0] * 4, wrap.get("mar") or [0] * 4)]
            for field in ("fg", "gc", "as"):
                if wrap.get(field) is not None and inner.get(field) is None:
                    inner[field] = wrap[field]
        return [child]
    return [kid]


def _has_size(node: Node) -> bool:
    for rec in (node.d, node.m):
        if rec and _box(rec)[2] > 1 and _box(rec)[3] > 1:
            return True
    return False


def _plain_wrapper(node: Node) -> bool:
    """A box that only holds one child and adds nothing to the layout."""
    child = node.kids[0]
    for vp in ("d", "m"):
        rec, inner = node.rec(vp), child.rec(vp)
        if (rec is None) != (inner is None):
            return False
        if rec is None:
            continue
        if _decorated(rec) or _positioned(rec) or any(c_.get("ps") == "absolute" for c_ in [k.base for k in node.kids]) or any(rec.get("pad") or [0]) or any(_border(rec)) or rec.get("ov") in ("hidden", "auto", "scroll", "clip"):
            return False
        if rec.get("t") in SEMANTIC or rec.get("t") in ("form", "a", "table", "tr", "td", "th"):
            return False
        if _hint_kind(rec):
            return False
        if rec.get("mw") is not None and rec["mw"] < _box(rec)[2] + 2:
            return False
        if rec.get("ta") in ("center", "right", "end") and _box(inner)[2] < _box(rec)[2] - 2:
            return False
        w, h = _box(rec)[2:]
        iw, ih = _box(inner)[2:]
        mt, _, mb, _ = inner.get("mar") or [0] * 4
        if abs(h - ih) > 3 and abs(h - (ih + mt + mb)) > 3:
            return False
        if iw < w - 2:
            ml, mr = (rec.get("mar") or [0] * 4)[3], (rec.get("mar") or [0] * 4)[1]
            if abs(ml - mr) <= 1 and ml > 2:
                return False
            if "flex" in rec.get("d", "") or "grid" in rec.get("d", ""):
                return False
    return True


def _hint_kind(rec: dict) -> str:
    hint = str(rec.get("hint") or "").lower()
    w, h = _box(rec)[2:]
    if re.search(r"\bhero\b|hero[-_]|[-_]hero|jumbotron|masthead", hint) and h > 200:
        return "hero"
    if re.search(r"\bcard\b|card[-_]|[-_]card|panel\b", hint) and _decorated(rec):
        return "card"
    if re.search(r"sidebar|side-bar|\baside\b", hint):
        return "sidebar"
    if re.search(r"\bmodal\b|\bdialog\b", hint):
        return "dialog"
    return ""


# ---------------------------------------------------------------- repeats

def _signature(node: Node, parent_cw: float) -> tuple:
    rec = node.base
    w = _box(rec)[2]
    if not node.kids:
        wide = parent_cw > 0 and w >= parent_cw * 0.9
        if rec.get("it"):
            # Distinct form fields are distinct stencil fields, never one repeated field.
            return (_kind_hint(node), rec.get("it"), rec.get("lab"))
        return (_kind_hint(node), "fill" if wide else int(w // 32))
    return (_kind_hint(node), len(node.kids), int(w // 24), tuple(_signature(k, max(1.0, w)) for k in node.kids))


def _kind_hint(node: Node) -> str:
    rec = node.base
    return str(rec.get("t") or "") + ("T" if rec.get("tb") else "") + ("M" if rec.get("media") else "")


def _merge_repeats(node: Node) -> None:
    for kid in node.kids:
        _merge_repeats(kid)
    if len(node.kids) < 2:
        return
    rec = node.base
    cw = _box(rec)[2]
    merged: list[Node] = []
    for kid in node.kids:
        prev = merged[-1] if merged else None
        if (prev is not None and _signature(prev, cw) == _signature(kid, cw)
                and (prev.d is None) == (kid.d is None) and (prev.m is None) == (kid.m is None)
                and not (_positioned(prev.base) or _positioned(kid.base))):
            prev.rep += kid.rep
            continue
        merged.append(kid)
    node.kids = merged


def _count(node: Node) -> int:
    return 1 + sum(_count(k) for k in node.kids)


def _limit(root: Node, budget: int) -> None:
    """Largest-first budget: big regions are drawn in detail, small deep detail becomes grey blocks.

    Every sibling of a drawn node is still drawn, so the page structure stays whole.
    """
    import heapq

    def area(node: Node) -> float:
        w, h = _box(node.base)[2:]
        return float(w) * float(h)

    heap: list[tuple[float, int, Node]] = []
    order = 0
    for kid in root.kids:
        heapq.heappush(heap, (-area(kid), order, kid))
        order += 1
    # Every undrawn child on the frontier still costs a line, so count the heap too.
    kept = 1
    while heap:
        _, _, node = heapq.heappop(heap)
        kept += 1
        if node.kids and kept + len(heap) + len(node.kids) > budget:
            node.cut = True
            node.kids = []
            continue
        for kid in node.kids:
            heapq.heappush(heap, (-area(kid), order, kid))
            order += 1


# ---------------------------------------------------------------- attributes

_JUSTIFY = {"flex-start": "start", "start": "start", "normal": "", "left": "start", "center": "center",
            "flex-end": "end", "end": "end", "right": "end", "space-between": "between",
            "space-around": "around", "space-evenly": "evenly", "stretch": ""}
_ALIGN = {"normal": "", "stretch": "", "flex-start": "start", "start": "start", "center": "center",
          "flex-end": "end", "end": "end", "baseline": "baseline", "first baseline": "baseline", "self-start": "start", "self-end": "end"}


def _n(value: float) -> str:
    return str(int(round(value)))


def _lum(color: list, invert: bool = False) -> float:
    r, g, b = (color[0] or 0) / 255, (color[1] or 0) / 255, (color[2] or 0) / 255
    value = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return 1 - value if invert else value


def _fill(rec: dict, page_fill: str, invert: bool = False) -> str:
    bg = rec.get("bg")
    if not bg:
        return "image" if rec.get("bi") else ""
    lum = _lum(bg, invert)
    spread = max(bg[:3]) - min(bg[:3])
    if lum > 0.975:
        name = "white"
    elif lum > 0.88:
        name = "light"
    elif lum > 0.62:
        name = "mid"
    elif spread > 60:
        name = "accent"
    else:
        name = "dark"
    if rec.get("bi") and name in ("white", "light"):
        name = "image"
    return "" if name == page_fill and not _border_letters(rec) else name


def _tone(rec: dict, invert: bool = False) -> str:
    color = rec.get("fg2") or [0, 0, 0, 1]
    lum = _lum(color, invert)
    if lum >= 0.7:
        return "light"
    if lum >= 0.38:
        return "muted"
    return ""


def _border_letters(rec: dict) -> str:
    bd = rec.get("bd")
    if not bd:
        return ""
    return "".join(letter for letter, width in zip("trbl", bd) if width)


def _spacing(values: list[float]) -> str:
    t, r, b, l = [int(round(v)) for v in values]
    if t == r == b == l:
        return "" if t == 0 else str(t)
    if t == b and r == l:
        return f"{t} {r}"
    return f"{t} {r} {b} {l}"


def _flow(node: Node, vp: str, kids: list[Node]) -> dict[str, Any]:
    rec = node.rec(vp) or {}
    display = str(rec.get("d") or "block")
    info: dict[str, Any] = {"flow": "block"}
    if "flex" in display:
        info["flow"] = "col" if str(rec.get("fd") or "").startswith("column") else "row"
        if rec.get("fw") and rec["fw"] != "nowrap":
            info["wrap"] = True
        info["justify"] = _JUSTIFY.get(str(rec.get("jc") or "normal"), "")
        info["align"] = _ALIGN.get(str(rec.get("ai") or "normal"), "")
        if info["flow"] == "row":
            # Growing children share the free space by their grow factor. When their widths do not
            # follow it (a different basis), each keeps its own measured width instead.
            shares = [(_box(k.rec(vp))[2] / k.rec(vp)["fg"]) for k in kids
                      if k.rec(vp) and k.rec(vp).get("fg") and not _positioned(k.rec(vp))]
            info["grow_ok"] = not shares or max(shares) <= min(shares) * 1.04 + 2
    elif "grid" in display:
        info["flow"] = "grid"
        info["align"] = _ALIGN.get(str(rec.get("ai") or "normal"), "")
    elif display == "table-row":
        info["flow"] = "row"
    else:
        live = [k for k in kids if k.rec(vp) and not k.rec(vp).get("ps") in ("absolute", "fixed")]
        if len(live) >= 2 and all(_inline_level(k.rec(vp)) for k in live):
            first = live[0].rec(vp)
            line_top = _box(first)[1]
            if sum(1 for k in live if abs(_box(k.rec(vp))[1] - line_top) < max(6, _box(first)[3] * 0.6)) >= 2:
                info["flow"] = "row"
                info["wrap"] = True
                ta = rec.get("ta")
                info["justify"] = {"center": "center", "right": "end", "end": "end"}.get(str(ta or ""), "")
                info["align"] = "center"
    rg, cg = rec.get("rg"), rec.get("cg")
    if info["flow"] == "block":
        gap_c = gap_r = 0
    else:
        gap_r, gap_c = rg or 0, cg or 0
    if info["flow"] == "row" and display != "table-row" and "flex" not in display:
        live = sorted((k.rec(vp) for k in kids if k.rec(vp) and not k.rec(vp).get("ps") in ("absolute", "fixed")),
                      key=lambda r: (_box(r)[1], _box(r)[0]))
        gaps = [_box(b)[0] - (_box(a)[0] + _box(a)[2]) for a, b in zip(live, live[1:]) if abs(_box(a)[1] - _box(b)[1]) < 6]
        gap_c = max(0, min(gaps)) if gaps else 0
    info["gap_r"], info["gap_c"] = gap_r, gap_c
    if info["flow"] == "grid":
        info["tracks"] = _tracks(str(rec.get("gtc") or ""), gap_c)
    return info


def _tracks(template: str, gap: float) -> list[float]:
    values = [float(v[:-2]) for v in template.split() if re.fullmatch(r"-?\d+(?:\.\d+)?px", v)]
    return values


def _content_box(rec: dict) -> tuple[float, float, float, float]:
    x, y, w, h = _box(rec)
    pad = rec.get("pad") or [0] * 4
    bd = _border(rec)
    return (x + bd[3] + pad[3], y + bd[0] + pad[0], max(1.0, w - bd[1] - bd[3] - pad[1] - pad[3]), max(1.0, h - bd[0] - bd[2] - pad[0] - pad[2]))


def _kind(node: Node, rec: dict, flow: str) -> str:
    tag = rec.get("t") or ""
    if tag in ("h1", "h2", "h3", "h4", "h5", "h6") and rec.get("tb"):
        return "heading"
    if (tag == "button" and not node.kids) or (tag == "input" and rec.get("it") in ("submit", "button", "reset", "image")):
        return "button"
    if tag == "input" and rec.get("it") == "checkbox":
        return "checkbox"
    if tag == "input" and rec.get("it") == "radio":
        return "radio"
    if tag == "textarea":
        return "textarea"
    if tag == "select":
        return "select"
    if tag == "input":
        return "input"
    if tag == "hr":
        return "divider"
    if (rec.get("media") or rec.get("ic")) and not node.kids:
        return "image"
    if tag == "a" and rec.get("tb") and not node.kids:
        deco = _decorated(rec) and any(_border(rec)) or rec.get("bg")
        return "button" if deco else "link"
    if rec.get("tb") and not node.kids:
        return "text"
    if tag == "a" and not node.kids:
        return "link"
    hint = _hint_kind(rec)
    if hint and tag not in SEMANTIC:
        return hint
    if tag in SEMANTIC:
        return SEMANTIC[tag]
    if not node.kids:
        return "box"
    return {"row": "row", "grid": "grid"}.get(flow, "stack")


def _lengthy(rec: dict, parent_rec: dict, pinfo: dict, vp: str, siblings_live: int) -> dict[str, str]:
    """Width, margins and flex/grid placement of a node inside its parent."""
    out: dict[str, str] = {}
    x, y, w, h = _box(rec)
    cx, cy, cw, ch = _content_box(parent_rec)
    mt, mr, mb, ml = rec.get("mar") or [0] * 4
    flow = pinfo["flow"]
    margins = [mt, mr, mb, ml]
    available = cw - mr - ml
    fill = abs(w - available) <= 2.5 or abs(w - cw) <= 2.5
    centred = (flow in ("block", "col") and not fill and w < cw - 2 and abs(ml - mr) <= 1.5 and ml > 2
               and abs(ml - (cw - w) / 2) <= 2.5)
    # Inline content centred by text-align has no margins, but sits in the middle all the same.
    middled = (flow in ("block", "col") and not fill and w < cw - 12 and x - cx > 8
               and abs((x + w / 2) - (cx + cw / 2)) <= 2.5 and ml <= 2 and mr <= 2)
    flush_right = (flow in ("block", "col") and not fill and w < cw - 12 and x - cx > 8
                   and abs((x + w) - (cx + cw)) <= 2.5 and ml <= 2 and mr <= 2)
    if flow == "row":
        if rec.get("fg") and pinfo.get("grow_ok", True):
            out["grow"] = _n(rec["fg"]) if rec["fg"] != 1 else "1"
        elif rec.get("fg"):
            out["w"] = f"{w / max(1.0, cw) * 100:.1f}%".replace(".0%", "%") if w < cw - 2 else "fill"
        else:
            out["w"] = _pct_or_px(w, cw)
    elif flow == "col" and (not fill or pinfo.get("align", "") not in ("", "stretch")):
        out["w"] = _pct_or_px(w, cw)
        if (middled or centred) and pinfo.get("align", "") in ("", "stretch"):
            margins = [mt, "auto", mb, "auto"]
        elif flush_right and pinfo.get("align", "") in ("", "stretch"):
            margins = [mt, mr, mb, "auto"]
    elif flow == "grid":
        tracks = pinfo.get("tracks") or []
        if not fill and not (tracks and any(abs(t - w) <= 2.5 for t in tracks)) and abs(w - cw) > 2.5:
            out["w"] = _pct_or_px(w, cw)
        span = str(rec.get("gc") or "")
        match = re.search(r"span (\d+)", span)
        if match and int(match.group(1)) > 1:
            out["span"] = match.group(1)
    elif flow == "block" and not fill:
        out["w"] = _pct_or_px(w, cw)
        if centred or middled:
            margins = [mt, "auto", mb, "auto"]
        elif flush_right:
            margins = [mt, mr, mb, "auto"]
    if rec.get("mw") is not None and abs(w - rec["mw"]) <= 1.5 and rec["mw"] < cw + 1 and "w" not in out and flow in ("block", "col"):
        out["maxw"] = _n(rec["mw"])
        if abs(ml - mr) <= 1.5 and ml > 2:
            margins = [mt, "auto", mb, "auto"]
    if rec.get("as") and rec["as"] not in ("auto", "normal", "stretch"):
        out["self"] = _ALIGN.get(str(rec["as"]), "")
        if not out["self"]:
            del out["self"]
    if any(m not in (0, 0.0) for m in margins):
        out["mar"] = " ".join(m if isinstance(m, str) else _n(m) for m in margins)
    return out


_CLIPS = ("hidden", "auto", "scroll", "clip")


def _overflows(node: Node, rec: dict, vp: str) -> bool:
    """True when a clipping box has children reaching past its edges (scrolled or cut off)."""
    if rec.get("ov") not in _CLIPS or rec.get("ps") in ("absolute", "fixed"):
        return False
    x, y, w, h = _box(rec)
    for kid in node.kids:
        k = kid.rec(vp)
        if not k or k.get("ps") in ("absolute", "fixed"):
            continue
        kx, ky, kw, kh = _box(k)
        if ky < y - 2 or ky + kh > y + h + 2 or kx < x - 2 or kx + kw > x + w + 2:
            return True
    return False


def _scrolled_start(a: dict, node: Node, parent: Node, prec: dict, pinfo: dict, vp: str) -> None:
    """The first visible child of a scrolled box starts above the box: a negative margin puts it back."""
    if not parent.kids or parent.kids[0] is not node or not _overflows(parent, prec, vp):
        return
    rec = node.rec(vp) or {}
    x, y, _w, _h = _box(rec)
    cx, cy, _cw, _ch = _content_box(prec)
    mar = (a.get("mar") or "0 0 0 0").split(" ")
    if len(mar) != 4:
        return
    offset = (x - cx) if pinfo["flow"] == "row" else (y - cy)
    if offset < -1:
        index = 3 if pinfo["flow"] == "row" else 0
        mar[index] = _n(offset)
        a["mar"] = " ".join(mar)


def _pct_or_px(w: float, cw: float) -> str:
    if cw > 0:
        pct = w / cw * 100
        if abs(w - cw) <= 2.5:
            return "fill"
        if pct >= 8 and abs(pct - round(pct)) < 0.2 and round(pct) < 100 and round(pct) % 5 == 0:
            return f"{int(round(pct))}%"
        for target in (100 / 3, 200 / 3):
            if abs(pct - target) < 0.3:
                return "33.33%" if target < 50 else "66.67%"
    return _n(w)


def _dark_theme(raw: dict) -> bool:
    """A dark page is drawn with its luminance inverted: the stencil is always light grey on white."""
    bg = (raw or {}).get("bg") or [255, 255, 255, 1]
    return _lum(bg) < 0.4


def _page_fill(raw: dict) -> str:
    bg = (raw or {}).get("bg") or [255, 255, 255, 1]
    return _fill({"bg": bg}, "", _dark_theme(raw))


def node_attrs(node: Node, vp: str, parent: Node, pinfo: dict, ctx: dict, siblings_live: int) -> dict[str, str] | None:
    """The layout attributes of one node at one viewport (None when it is not shown there)."""
    rec = node.rec(vp)
    if rec is None:
        return None
    prec = parent.rec(vp) or parent.base
    kids = node.kids
    a: dict[str, str] = {}
    x, y, w, h = _box(rec)
    kind = node.kind
    leaf = not kids
    ps = rec.get("ps")
    cx, cy, cw, ch = _content_box(prec)
    if ps in ("absolute", "fixed"):
        a["pos"] = "abs" if ps == "absolute" else "fixed"
        if ps == "absolute":
            px_, py_, pw_, ph_ = _box(prec)
            bd = _border(prec)
            a["top"] = _n(y - (py_ + bd[0]))
            a["left"] = _n(x - (px_ + bd[3]))
        else:
            a["top"] = _n(y)
            a["left"] = _n(x)
        a["w"] = _n(w)
        if leaf or kind in ("header", "nav", "footer", "section", "box"):
            a["h"] = _n(h)
    else:
        if ps == "sticky":
            a["pos"] = "sticky"
            a["top"] = _n(max(0, y - _box(prec)[1] if False else 0))
        a.update(_lengthy(rec, prec, pinfo, vp, siblings_live))
        _scrolled_start(a, node, parent, prec, pinfo, vp)
    # Surface
    fill = _fill(rec, ctx["page_fill"], ctx["invert"])
    if fill:
        a["fill"] = fill
    letters = _border_letters(rec)
    if letters:
        a["line"] = letters
    if rec.get("rad"):
        a["radius"] = _n(min(99, rec["rad"]))
    if rec.get("sh"):
        a["shadow"] = "true"
    pad = _spacing(rec.get("pad") or [0] * 4)
    if pad and not (leaf and kind in ("input", "select", "textarea", "image", "checkbox", "radio", "divider")):
        a["pad"] = pad
    # Container rules
    if not leaf:
        info = _flow(node, vp, kids)
        node_flow = info["flow"]
        if node_flow != "block" or kind in ("row", "grid"):
            a["flow"] = node_flow if not (kind == "row" and node_flow == "row" or kind == "grid" and node_flow == "grid") else ""
            if not a["flow"]:
                del a["flow"]
        elif kind in ("stack",) or kind in SEMANTIC.values():
            pass
        if node_flow == "grid":
            tracks = info.get("tracks") or []
            spec = _track_spec(tracks, cw if False else _content_box(rec)[2], info["gap_c"])
            if spec.startswith("columns:"):
                a["columns"] = spec.split(":", 1)[1]
            elif spec:
                a["tracks"] = spec
        if node_flow in ("row", "grid", "col") or info["gap_r"] or info["gap_c"]:
            gr, gc = info["gap_r"], info["gap_c"]
            if node_flow == "row" and gc:
                a["gap"] = _n(min(gc, 400))
                if gr and abs(gr - gc) > 1:
                    a["rgap"] = _n(min(gr, 400))
            elif node_flow == "col" and gr:
                a["gap"] = _n(min(gr, 400))
            elif node_flow == "grid":
                if gc and abs(gr - gc) <= 1:
                    a["gap"] = _n(min(gc, 400))
                else:
                    if gc:
                        a["cgap"] = _n(min(gc, 400))
                    if gr:
                        a["rgap"] = _n(min(gr, 400))
        if info.get("wrap"):
            a["wrap"] = "true"
        if info.get("justify"):
            a["justify"] = info["justify"]
        if info.get("align"):
            a["align"] = info["align"]
        # Height beyond the content is an explicit height/min-height, unless a flex/grid parent stretched it.
        stretched = pinfo["flow"] in ("row", "grid") and pinfo.get("align", "") in ("", "stretch")
        grows = bool(rec.get("fg")) and pinfo["flow"] == "col"
        if not stretched and not grows and not ps == "absolute":
            content_bottom = max(((_box(k.rec(vp))[1] + _box(k.rec(vp))[3] + ((k.rec(vp).get("mar") or [0] * 4)[2]))
                                  for k in kids if k.rec(vp) and k.rec(vp).get("ps") not in ("absolute", "fixed")), default=y)
            inner_bottom = y + h - (rec.get("pad") or [0] * 4)[2] - _border(rec)[2]
            if inner_bottom - content_bottom > 3 and h > 8:
                a["minh"] = _n(h)
        if _overflows(node, rec, vp):
            # A scroll or clipped box shows a window onto longer content: fix its height and clip.
            a["clip"] = "true"
            a["h"] = _n(h)
            a.pop("minh", None)
    # Leaf rules
    if leaf:
        lh = rec.get("lh") or 20
        fs = rec.get("fz") or 14
        pad_v = (rec.get("pad") or [0] * 4)[0] + (rec.get("pad") or [0] * 4)[2]
        if kind in ("text", "heading", "link", "button") and rec.get("tb"):
            lines = max(1, min(40, rec.get("tl") or int(round((h - pad_v) / max(1, lh)))))
            tx = rec.get("tx")
            if kind == "button":
                if tx and w > 0:
                    a["tw"] = _n(max(20, min(100, (tx[1] - tx[0]) / w * 100)))
                a["h"] = _n(h)
            else:
                if lines > 1:
                    a["lines"] = str(lines)
                elif tx and w > 0 and (tx[1] - tx[0]) < w * 0.92 and a.get("w", "fill") == "fill" and kind != "link":
                    a["tw"] = _n(max(5, (tx[1] - tx[0]) / w * 100))
                if abs(h - (lines * lh + pad_v)) > 3:
                    a["h"] = _n(h)
            if round(fs) != 14:
                a["fs"] = _n(fs)
            if round(lh) != round(round(fs) * 1.4):
                a["lh"] = _n(lh)
            if rec.get("fb"):
                a["bold"] = "true"
            if rec.get("ta") in ("center", "right", "end"):
                a["ta"] = "center" if rec["ta"] == "center" else "right"
            tone = _tone(rec, ctx["invert"])
            if tone:
                a["tone"] = tone
        elif kind in ("image", "box", "input", "select", "textarea", "checkbox", "radio", "divider", "button", "text", "heading", "link"):
            if kind == "image" and (a.get("w", "") == "fill" or a.get("w", "").endswith("%")):
                a["ratio"] = f"{w / max(1.0, h):.2f}".rstrip("0").rstrip(".")
            elif kind == "image" and "w" not in a and pinfo["flow"] in ("block", "grid", "col"):
                a["ratio"] = f"{w / max(1.0, h):.2f}".rstrip("0").rstrip(".")
                a["w"] = "fill"
            else:
                a["h"] = _n(h)
            if kind == "button":
                tone = _tone(rec, ctx["invert"])
                if tone:
                    a["tone"] = tone
    if a.get("w") == "fill" and (pinfo["flow"] in ("block", "grid") or (pinfo["flow"] == "col" and pinfo.get("align", "") in ("", "stretch"))):
        del a["w"]
    if node.cut:
        a["approx"] = "true"
        a.setdefault("h", _n(h))
    return a


def _track_spec(tracks: list[float], inner_w: float, gap: float) -> str:
    if not tracks:
        return ""
    n = len(tracks)
    if all(abs(t - tracks[0]) <= 1.5 for t in tracks):
        return f"columns:{min(n, 12)}" if n <= 12 else ",".join(["1fr"] * 12)
    fixed = [t <= 360 for t in tracks]
    if any(fixed) and not all(fixed):
        return ",".join(f"{_n(t)}" if f else "1fr" for t, f in zip(tracks, fixed))
    small = min(tracks)
    ratios = [t / small for t in tracks]
    if all(abs(r - round(r)) < 0.12 for r in ratios) and max(ratios) <= 8:
        return ",".join(f"{int(round(r))}fr" for r in ratios)
    return ",".join(_n(t) for t in tracks)


# ---------------------------------------------------------------- DSL

_QUOTE = re.compile(r'["\\]')


def _q(text: str, limit: int = 60) -> str:
    cleaned = " ".join(str(text or "").split())[:limit]
    cleaned = "".join(ch for ch in cleaned if ch >= " " and ch != "\x7f")
    return '"' + _QUOTE.sub(lambda m: "\\" + m.group(0), cleaned) + '"'


_AUTH_OUT = re.compile(r"log ?out|sign ?out|my account|my orders|my profile|dashboard", re.I)
_AUTH_IN = re.compile(r"log ?in|sign ?in|register|sign ?up|create account", re.I)

_ORDER = ("w", "grow", "span", "maxw", "h", "minh", "ratio", "mar", "pad", "flow", "columns", "tracks", "gap", "rgap", "cgap",
          "wrap", "justify", "align", "self", "pos", "top", "left", "fill", "line", "radius", "shadow",
          "lines", "tw", "fs", "lh", "bold", "ta", "tone", "approx", "clip")


def _attr_text(attrs: dict[str, str]) -> str:
    parts = []
    for key in _ORDER:
        if key in attrs and attrs[key] != "":
            value = attrs[key]
            parts.append(f'{key}="{value}"' if re.search(r"\s", value) else f"{key}={value}")
    return " ".join(parts)


def _diff(desk: dict[str, str], mob: dict[str, str]) -> str:
    changes = []
    for key in _ORDER:
        if key in ("pos",):
            continue
        a, b = desk.get(key, ""), mob.get(key, "")
        if a == b:
            continue
        if key in ("w", "h", "minh", "maxw", "top", "left", "fs", "lh", "tw") and a.lstrip("-").isdigit() and b.lstrip("-").isdigit():
            if abs(int(a) - int(b)) <= max(4, int(abs(int(a)) * 0.08)) and key not in ("fs",):
                continue
        changes.append(f"{key}:{b or '-'}")
    return ";".join(changes)


def to_lines(
    root: Node,
    raw: dict[str, Any],
    *,
    title: str,
    route: str = "",
    origin: str = "",
    decorate: Callable[[str, str], dict[str, str]] | None = None,
) -> list[str]:
    """Wire DSL lines for one page."""
    desktop, mobile = raw.get("desktop") or {}, raw.get("mobile") or {}
    page_fill = _page_fill(desktop)
    ctx = {"page_fill": page_fill, "origin": origin, "decorate": decorate, "invert": _dark_theme(desktop)}
    rec = root.rec("d") or root.base
    head = [f"screen {_q(title, 80)} device=desktop" + (f" route={_q(route, 200)}" if route else "")
            + f" layout=v2 vw={int(desktop.get('vw') or 1440)} ph={int(desktop.get('ph') or 900)}"
            + (f" bg={page_fill}" if page_fill else "")]
    root_info = _flow(root, "d", root.kids)
    pad = _spacing(rec.get("pad") or [0] * 4)
    root_attrs = {}
    if pad:
        root_attrs["pad"] = pad
    root_flow = root_info["flow"]
    if root_flow != "block":
        root_attrs["flow"] = root_flow
        if root_info.get("justify"):
            root_attrs["justify"] = root_info["justify"]
        if root_info.get("align"):
            root_attrs["align"] = root_info["align"]
        if root_info["gap_r"] or root_info["gap_c"]:
            root_attrs["gap"] = _n(max(root_info["gap_r"], root_info["gap_c"]))
    if root_attrs:
        head[0] += " " + _attr_text(root_attrs)
    body: list[str] = []
    for kid in root.kids:
        _emit(kid, root, 1, body, ctx, root_info, {"d": root_info, "m": _flow(root, "m", root.kids) if root.m else root_info})
    return head + body


def _label_of(node: Node, kind: str) -> str:
    rec = node.base
    if kind in ("input", "select", "textarea", "checkbox", "radio"):
        return str(rec.get("lab") or rec.get("ph") or "")
    if kind == "image":
        return str(rec.get("alt") or "")
    return str(rec.get("txt") or "")


def _emit(node: Node, parent: Node, depth: int, out: list[str], ctx: dict, pinfo_d: dict, pinfos: dict) -> None:
    rec_d, rec_m = node.d, node.m
    live_d = sum(1 for k in parent.kids if k.d)
    live_m = sum(1 for k in parent.kids if k.m)
    base_vp = "d" if rec_d else "m"
    rec = node.rec(base_vp)
    info_here = _flow(node, base_vp, node.kids) if node.kids else {"flow": "block"}
    node.kind = _kind(node, rec, info_here["flow"])
    desk = node_attrs(node, "d", parent, pinfos["d"], ctx, live_d) if rec_d else None
    mob = node_attrs(node, "m", parent, pinfos.get("m") or pinfos["d"], ctx, live_m) if rec_m and pinfos.get("m") else None
    if desk is None and mob is None:
        return
    attrs = dict(desk if desk is not None else mob)
    resp = ""
    show = ""
    if desk is None:
        show = "mobile"
    elif mob is None and node.parent is not None and (parent.m or not parent.d):
        show = "desktop"
    elif mob is not None and desk is not None:
        resp = _diff(desk, mob)
    label = _label_of(node, node.kind)
    leaf_text = node.kind in ("text", "heading", "link", "button", "input", "select", "textarea", "checkbox", "radio", "image")
    extra: dict[str, str] = {}
    if node.kind == "link":
        href = str(rec.get("href") or "")
        origin = ctx.get("origin") or ""
        if origin and href.startswith(origin):
            href = href[len(origin):] or "/"
        if re.fullmatch(r"/(?!/)[^\s\\\"']*", href or "") and len(href) < 200:
            extra["route"] = href
    if node.kind == "button" and rec.get("t") == "a":
        href = str(rec.get("href") or "")
        origin = ctx.get("origin") or ""
        if origin and href.startswith(origin):
            href = href[len(origin):] or "/"
        if re.fullmatch(r"/(?!/)[^\s\\\"']*", href or "") and len(href) < 200:
            extra["route"] = href
    if node.kind in ("link", "button") and not show:
        blob = f"{label} {rec.get('href') or ''}"
        if _AUTH_OUT.search(blob):
            show = "auth"
        elif _AUTH_IN.search(blob) and node.base.get("t") == "a":
            show = "guest"
    if ctx.get("decorate") and leaf_text:
        extra.update(ctx["decorate"](node.kind, label) or {})
    if rec.get("it") and node.kind == "input" and rec["it"] in ("email", "password", "tel", "number", "search", "url", "text"):
        extra["type"] = rec["it"]
    attrs_text = _attr_text(attrs)
    line = "  " * depth + node.kind
    if leaf_text or node.kind in ("text", "heading", "link", "button") and label:
        line += " " + _q(label, 48)
    if node.rep > 1:
        attrs_text = (attrs_text + f" rep={node.rep}").strip()
    if show:
        attrs_text = (attrs_text + f" show={show}").strip()
    if extra:
        attrs_text = (attrs_text + " " + " ".join(f"{k}={_q(v, 200)}" if not re.fullmatch(r"[\w./-]+", v) else f"{k}={v}" for k, v in extra.items())).strip()
    if resp:
        attrs_text = (attrs_text + f' at{BREAKPOINT}="{resp}"').strip()
    out.append((line + " " + attrs_text).rstrip())
    if node.kids:
        child_infos = {"d": _flow(node, "d", node.kids) if node.d else None, "m": _flow(node, "m", node.kids) if node.m else None}
        child_infos["d"] = child_infos["d"] or child_infos["m"]
        for kid in node.kids:
            _emit(kid, node, depth + 1, out, ctx, child_infos["d"], child_infos)


def layout_to_lines(
    raw: dict[str, Any],
    *,
    title: str,
    route: str = "",
    origin: str = "",
    budget: int = DEFAULT_BUDGET,
    decorate: Callable[[str, str], dict[str, str]] | None = None,
) -> list[str]:
    """Wire lines for a probed page, or [] when the probe has nothing usable."""
    desktop = (raw or {}).get("desktop") or {}
    if not desktop.get("records"):
        return []
    root = build_tree(desktop, (raw or {}).get("mobile") or {})
    if root is None:
        return []
    _prune(root, root=True)
    if _unreadable(root):
        return honest_lines(title, route, "No layout to show: this address returned a bare error or data reply instead of a page.", desktop.get("ph"))
    _merge_repeats(root)
    _limit(root, max(8, int(budget)))
    return to_lines(root, raw, title=title, route=route, origin=origin, decorate=decorate)


def _unreadable(root: Node) -> bool:
    """A page with next to nothing to draw: a JSON reply, an error body, a blank document (no links, images or fields)."""
    count = 0
    stack = list(root.kids)
    while stack:
        node = stack.pop()
        count += 1
        rec = node.base
        if rec.get("media") or rec.get("it") or rec.get("href"):
            return False
        stack.extend(node.kids)
        if count > 3:
            return False
    return True


def honest_lines(title: str, route: str = "", reason: str = "", ph: Any = None) -> list[str]:
    """A stencil that says so when a page could not be captured, instead of a layout that was never seen."""
    head = f'screen "{_clean_label(title)}" device=desktop'
    if route:
        head += f' route="{_clean_label(route)}"'
    note = _clean_label(reason or "This page could not be captured, so no layout is shown.")[:160]
    return [f'{head} layout=v2 vw=1440 ph=900 bg=white note="{note}"',
            '  box h=320 w=60% mar="48 auto 0 auto" fill=light line=trbl radius=8 approx=true']


def _clean_label(value: Any) -> str:
    return re.sub(r'["\\\x00-\x1f\x7f]', " ", str(value or "")).strip()


TIERS = (300, 150, 80, 40, 20)


def stored_layouts(raw: dict[str, Any], *, route: str = "", origin: str = "") -> dict[str, list[str]]:
    """The page's stencil at each node budget, ready to save beside its screenshot.

    The wire holds every page in one document, so the scanner picks the richest tier that fits.
    """
    out: dict[str, list[str]] = {}
    for tier in TIERS:
        lines = layout_to_lines(raw, title="Page", route=route, origin=origin, budget=tier)
        if lines:
            out[str(tier)] = lines
    return out


def sanitize_lines(value: Any) -> list[str]:
    """Saved layout lines are untrusted input to the wire: keep only well-formed ones."""
    if not isinstance(value, list) or not value or not isinstance(value[0], str):
        return []
    if not value[0].startswith("screen ") or " layout=v2" not in value[0]:
        return []
    out = []
    for line in value[:2000]:
        if not isinstance(line, str) or len(line) > 700 or any(ord(ch) < 32 or ord(ch) == 127 for ch in line):
            return []
        out.append(line.rstrip())
    return out
