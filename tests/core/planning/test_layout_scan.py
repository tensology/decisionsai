"""Captured page layouts flow from the saved shot metadata into the plan's wire."""
from __future__ import annotations

import json

from distr.core.planning import layout_probe as lp
from distr.core.planning import project_scan, validation
from tests.core.planning.test_layout_probe import RAW


def _project(tmp_path):
    folder = tmp_path / "planning"
    folder.mkdir()
    layouts = lp.stored_layouts(RAW, route="/shop")
    (folder / "shop.json").write_text(json.dumps({"url": "https://x.test/shop", "layouts": layouts}))
    (folder / "bad.json").write_text(json.dumps({"url": "https://x.test/bad", "layouts": {"20": ["stack", "  box"]}}))
    (folder / "junk.json").write_text("not json")
    return tmp_path


def test_saved_layouts_are_read_by_route_and_bad_ones_skipped(tmp_path):
    found = project_scan._captured_page_layouts(_project(tmp_path))
    assert list(found) == ["/shop"]
    assert set(found["/shop"]) == {str(t) for t in lp.TIERS}


def test_a_captured_route_is_drawn_from_its_layout_and_others_keep_the_schematic(tmp_path):
    layouts = project_scan._captured_page_layouts(_project(tmp_path))
    routes = [{"url": "/shop", "name": "shop", "title": "Shop", "group": "Pages"},
              {"url": "/about", "name": "about", "title": "About", "group": "Pages"}]
    wire = project_scan.build_flows(routes, project_name="X", layouts=layouts)
    assert 'screen "Shop" device=desktop route="/shop" layout=v2' in wire
    assert 'screen "About" device=desktop route="/about"\n' in wire
    assert validation.validate_wireframe(wire) == []


def test_many_pages_fall_back_to_a_smaller_tier_to_fit_the_wire_cap(tmp_path):
    layouts = project_scan._captured_page_layouts(_project(tmp_path))
    rich = {"/shop": layouts["/shop"]}
    routes = [{"url": f"/p{i}", "name": f"p{i}", "title": f"P{i}", "group": "Pages"} for i in range(40)]
    big = {f"/p{i}": {"300": rich["/shop"]["300"]} for i in range(40)}
    # Only the 300 tier exists for each page: the scanner must still stay under the cap.
    wire = project_scan.build_flows(routes, project_name="X", layouts=big)
    assert len(wire) <= project_scan._MAX_WIRE_CHARS
