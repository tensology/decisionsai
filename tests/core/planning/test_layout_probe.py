"""The layout probe reduces a rendered page to layout-faithful stencil DSL lines."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from distr.core.planning import layout_probe as lp


def _rec(k, p, tag, box, **extra):
    base = {"k": k, "p": p, "t": tag, "d": extra.pop("d", "block"), "r": box}
    base.update(extra)
    return base


def _page(vw, cols):
    """A header, then a grid of cards, then a footer; the card width follows the viewport."""
    card_w = (vw - 40 - 16 * (cols - 1)) // cols
    records = [
        _rec(1, 0, "body", [0, 0, vw, 700], bg=[255, 255, 255, 1]),
        _rec(2, 1, "header", [0, 0, vw, 64], d="flex", fd="row", jc="space-between", ai="center",
             bg=[20, 40, 90, 1], pad=[0, 20, 0, 20]),
        _rec(3, 2, "a", [20, 20, 120, 24], href="/", txt="Shop", tb=1, tl=1, fz=16, lh=24, tx=[20, 100]),
        _rec(4, 1, "main", [20, 80, vw - 40, 500], d="grid", gtc=" ".join([f"{card_w}px"] * cols), rg=16, cg=16),
    ]
    key = 10
    for i in range(4):
        row, col = divmod(i, cols)
        x = 20 + col * (card_w + 16)
        y = 80 + row * 216
        records.append(_rec(key, 4, "div", [x, y, card_w, 200], bg=[240, 242, 247, 1], rad=8))
        records.append(_rec(key + 1, key, "img", [x, y, card_w, 120], media=1))
        records.append(_rec(key + 2, key, "a", [x + 8, y + 130, card_w - 16, 20], href="/p/%d" % i, txt="Item %d" % i,
                            tb=1, tl=1, fz=14, lh=20, tx=[x + 8, x + 80]))
        key += 3
    records.append(_rec(99, 1, "footer", [0, 640, vw, 60], bg=[0, 0, 0, 1]))
    return {"vw": vw, "vh": 900, "pw": vw, "ph": 700, "bg": [255, 255, 255, 1], "records": records}


RAW = {"desktop": _page(1440, 4), "mobile": _page(390, 2)}


def test_lines_describe_regions_columns_and_repeats():
    lines = lp.layout_to_lines(RAW, title="Shop", route="/", origin="https://x.test")
    head = lines[0]
    assert head.startswith('screen "Shop" device=desktop route="/" layout=v2 vw=1440 ph=700')
    text = "\n".join(lines)
    assert "header" in text and "footer" in text
    assert "justify=between" in text
    assert "columns=4" in text or "tracks=" in text
    assert "rep=" in text  # identical cards merge into one repeated node


def test_phone_differences_become_at768_overrides():
    text = "\n".join(lp.layout_to_lines(RAW, title="Shop", route="/", origin="https://x.test"))
    assert "at768=" in text


def test_dark_pages_are_drawn_light():
    dark = json.loads(json.dumps(RAW))
    dark["desktop"]["bg"] = [10, 12, 30, 1]
    dark["desktop"]["records"][0]["bg"] = [10, 12, 30, 1]
    lines = lp.layout_to_lines(dark, title="Dark", route="/", origin="https://x.test")
    assert " bg=white" in lines[0] or " bg=light" in lines[0]


def test_node_budget_bounds_the_output():
    small = lp.layout_to_lines(RAW, title="Shop", route="/", budget=8)
    assert len(small) <= 30
    assert len(lp.stored_layouts(RAW, route="/")) == len(lp.TIERS)


def test_a_bare_reply_gets_an_honest_state():
    bare = {"desktop": {"vw": 1440, "ph": 900, "records": [_rec(1, 0, "body", [0, 0, 1440, 900]),
                                                          _rec(2, 1, "pre", [8, 8, 300, 20], txt='{"status": "ERROR"}', tb=1)]},
            "mobile": {"vw": 390, "ph": 900, "records": []}}
    lines = lp.layout_to_lines(bare, title="API", route="/api")
    assert 'note="' in lines[0] and "layout=v2" in lines[0]
    assert "ERROR" not in "\n".join(lines)  # the reply body is never copied into the wire


def test_no_records_gives_no_layout():
    assert lp.layout_to_lines({"desktop": {}, "mobile": {}}, title="x") == []
    assert lp.layout_to_lines(None, title="x") == []


def test_saved_lines_are_sanitised():
    good = lp.layout_to_lines(RAW, title="Shop", route="/")
    assert lp.sanitize_lines(good) == good
    assert lp.sanitize_lines(["stack", "  box"]) == []
    assert lp.sanitize_lines(good[:1] + ["  box \x07"]) == []
    assert lp.sanitize_lines(good[:1] + ["  box " + "x" * 800]) == []
    assert lp.sanitize_lines("nope") == []


@pytest.mark.skipif(shutil.which("node") is None, reason="node is needed for the shared validator")
def test_every_emitted_line_passes_the_real_validator():
    from distr.core.planning import validation

    lines = lp.layout_to_lines(RAW, title="Shop", route="/", origin="https://x.test")
    assert validation.validate_wireframe("\n".join(lines) + "\n") == []


def _centred_login(vw, ph):
    """A full-height flex box whose single child sits in the middle of the screen: the height belongs to the box."""
    body = _rec(1, 0, "body", [0, 0, vw, ph], bg=[255, 255, 255, 1])
    outer = _rec(2, 1, "div", [0, 0, vw, ph], d="flex", fd="row")
    inner = _rec(3, 2, "div", [0, 0, vw, ph], d="flex", fd="row", jc="center", ai="center")
    card = _rec(4, 3, "div", [vw // 2 - 150, ph // 2 - 100, 300, 200], bg=[245, 245, 245, 1])
    field = _rec(5, 4, "input", [vw // 2 - 140, ph // 2 - 20, 280, 38], bg=[255, 255, 255, 1], it="text")
    return {"vw": vw, "vh": ph, "pw": vw, "ph": ph, "bg": [255, 255, 255, 1], "records": [body, outer, inner, card, field]}


def test_a_full_screen_box_keeps_its_height_on_each_viewport():
    raw = {"desktop": _centred_login(1440, 900), "mobile": _centred_login(390, 844)}
    text = "\n".join(lp.layout_to_lines(raw, title="Login", route="/login", origin="https://x.test"))
    assert "minh=900" in text and "minh:844" in text


def _sticky_page(sticky_pos):
    head = _rec(2, 1, "header", [0, 0, 1440, 100], ps=sticky_pos, bg=[0, 0, 0, 1])
    main = _rec(3, 1, "main", [0, 100, 1440, 400], bg=[255, 255, 255, 1])
    body = _rec(1, 0, "body", [0, 0, 1440, 500], bg=[255, 255, 255, 1])
    logo = _rec(4, 2, "a", [20, 30, 120, 24], href="/", txt="Shop", tb=1, tl=1, fz=16, lh=24, tx=[20, 100])
    text = _rec(5, 3, "p", [20, 100, 600, 24], txt="Welcome to the shop", tb=1, fz=16, lh=24, tx=[20, 600])
    return {"vw": 1440, "vh": 900, "pw": 1440, "ph": 500, "bg": [255, 255, 255, 1], "records": [body, head, main, logo, text]}


def test_a_sticky_header_still_takes_up_room_so_nothing_below_it_is_pushed_down():
    sticky = "\n".join(lp.layout_to_lines({"desktop": _sticky_page("sticky"), "mobile": _sticky_page("sticky")}, title="S", route="/"))
    assert "mar=\"100 0 0 0\"" not in sticky
    fixed = "\n".join(lp.layout_to_lines({"desktop": _sticky_page("fixed"), "mobile": _sticky_page("fixed")}, title="S", route="/"))
    assert "mar=\"100 0 0 0\"" in fixed          # a fixed header leaves the flow, so the page below must be spaced down
