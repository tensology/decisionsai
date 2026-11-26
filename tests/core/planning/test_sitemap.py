"""Section D: the sitemap is where page templates become pages."""

import pytest

from distr.core.planning import library, sitemap
from distr.core.planning.page import Page, parse_page


def _page(name, route=""):
    return parse_page({"name": name, "title": name.title(), "route": route, "blocks": ["kpi-row"]})


# ------------------------------------------------------------------ building

def test_drops_a_template_into_a_page():
    sm = sitemap.Sitemap(title="Ops")
    node = sm.drop_template(library.get("dashboard-page"), title="Dashboard", route="/")
    assert node is not None
    assert node.name == "dashboard"
    assert node.route == "/"
    assert sm.count() == 1


def test_pages_can_be_nested():
    sm = sitemap.Sitemap()
    sm.drop_template(library.get("dashboard-page"), title="Dashboard", route="/")
    sm.drop_template(library.get("settings-page"), title="Settings", route="/settings", parent="dashboard")
    assert sm.count() == 2
    assert sm.find("dashboard").children[0].name == "settings"


def test_a_missing_parent_falls_back_to_the_top_level():
    sm = sitemap.Sitemap()
    sm.drop_template(library.get("settings-page"), title="Settings", parent="no-such-page")
    assert sm.roots[0].name == "settings"


def test_the_same_page_is_not_added_twice():
    sm = sitemap.Sitemap()
    sm.drop_template(library.get("settings-page"), title="Settings")
    assert sm.drop_template(library.get("settings-page"), title="Settings") is None
    assert sm.count() == 1


def test_adding_a_page_over_the_cap_is_refused(monkeypatch):
    monkeypatch.setattr(sitemap, "MAX_PAGES", 2)
    sm = sitemap.Sitemap()
    assert sm.add(_page("a")) is not None
    assert sm.add(_page("b")) is not None
    assert sm.add(_page("c")) is None
    assert sm.count() == 2


def test_adding_junk_is_refused():
    sm = sitemap.Sitemap()
    assert sm.add(None) is None
    assert sm.add("not a page") is None
    assert sm.count() == 0


# ------------------------------------------------------------------ routes

def test_routes_are_the_links_between_pages():
    sm = sitemap.Sitemap()
    sm.add(_page("a", "/"))
    sm.add(_page("b", "/b"))
    sm.add(_page("c", "/c"))
    assert sm.routes() == ["/", "/b", "/c"]


def test_routes_are_deduplicated_and_blanks_dropped():
    sm = sitemap.Sitemap()
    sm.add(_page("a", "/same"))
    sm.add(_page("b", "/same"))
    sm.add(_page("c", ""))
    assert sm.routes() == ["/same"]


# ------------------------------------------------------------------ removal

def test_removing_a_page_takes_its_subtree():
    sm = sitemap.Sitemap()
    sm.add(_page("a", "/"))
    sm.add(_page("b", "/b"), parent="a")
    sm.add(_page("c", "/c"), parent="b")
    assert sm.count() == 3
    assert sm.remove("b") is True
    assert sm.count() == 1
    assert sm.find("c") is None


def test_removing_something_absent_is_false():
    sm = sitemap.Sitemap()
    sm.add(_page("a"))
    assert sm.remove("nope") is False
    assert sm.remove("") is False
    assert sm.count() == 1


# ------------------------------------------------------------------ output

def test_to_wire_renders_every_page():
    sm = sitemap.Sitemap()
    sm.add(_page("a", "/a"))
    sm.add(_page("b", "/b"))
    wire, missing = sm.to_wire()
    assert missing == []
    assert wire.count("screen ") == 2


def test_to_wire_reports_missing_references():
    sm = sitemap.Sitemap()
    sm.add(parse_page({"name": "a", "title": "A", "blocks": ["no-such-widget"]}))
    wire, missing = sm.to_wire()
    assert missing == ["no-such-widget"]
    assert wire.startswith('screen "A"')


# ------------------------------------------------------------------ parsing

def test_parses_a_sitemap():
    sm = sitemap.parse_sitemap({
        "title": "Ops",
        "roots": [
            {"page": {"name": "a", "title": "A", "route": "/"}, "children": [
                {"page": {"name": "b", "title": "B", "route": "/b"}},
            ]},
        ],
    })
    assert sm.title == "Ops"
    assert sm.count() == 2
    assert sm.routes() == ["/", "/b"]


@pytest.mark.parametrize("raw", [None, "x", [], 5, {}])
def test_junk_sitemaps_parse_to_empty(raw):
    assert sitemap.parse_sitemap(raw).count() == 0


def test_a_junk_page_is_skipped_not_fatal():
    sm = sitemap.parse_sitemap({"roots": [{"page": {"name": "a", "title": "A"}}, "junk", 5, None]})
    assert sm.count() == 1


def test_round_trip_as_dict():
    sm = sitemap.Sitemap(title="Ops")
    sm.add(_page("a", "/a"))
    sm.add(_page("b", "/b"), parent="a")
    again = sitemap.parse_sitemap(sm.as_dict())
    assert again.title == "Ops"
    assert again.count() == 2
    assert again.find("a").children[0].name == "b"
