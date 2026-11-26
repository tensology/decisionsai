"""P2: read a page's cue from its rendered DOM, for what source cannot describe."""

import pytest

from distr.core.planning import dom_cues, project_scan as ps


# ------------------------------------------------------------------ shaping

def test_shapes_a_browser_result_into_a_cue():
    cue = dom_cues._normalise({
        "headings": ["Account settings", "Notifications", ""],
        "fields": ["Email", "Email", "a"],
        "buttons": ["Save", "Cancel"],
        "links": [{"label": "About us", "route": "/about"}, {"label": "Bad", "route": "https://x"}],
        "regions": {"header": "Site header", "bogus": "no"},
        "columns": ["Name", "Status"],
        "types": {"Email": "email", "nope": "made-up"},
    })
    assert cue["headings"] == ["Account settings", "Notifications"]
    assert cue["fields"] == ["Email"]           # deduped, 1-char dropped
    assert cue["buttons"] == ["Save", "Cancel"]
    assert cue["links"] == [{"label": "About us", "route": "/about"}]
    assert cue["regions"] == {"header": "Site header"}
    assert cue["columns"] == ["Name", "Status"]
    assert cue["types"] == {"Email": "email"}
    assert cue["observed"] is True


@pytest.mark.parametrize("raw", [None, "x", [], 5, {}])
def test_junk_becomes_an_empty_cue(raw):
    cue = dom_cues._normalise(raw)
    assert cue in ({}, {"observed": True})


def test_a_missing_route_or_base_is_not_an_error():
    assert dom_cues.read_route("", "/x") == {}
    assert dom_cues.read_route("http://x", "") == {}
    assert dom_cues.read_routes("", ["/x"]) == {}
    assert dom_cues.read_routes("http://x", []) == {}


# ------------------------------------------------------------------ merging

def test_a_rendered_cue_fills_a_sources_gaps():
    source = {"fields": ["Email"], "headings": [], "buttons": [], "types": {"Email": "email"}}
    observed = {"headings": ["Home"], "buttons": ["Get started"], "regions": {"nav": "Nav"}}
    merged = dom_cues.merge_into(source, observed)
    assert merged["fields"] == ["Email"]
    assert merged["headings"] == ["Home"]
    assert merged["buttons"] == ["Get started"]
    assert merged["regions"] == {"nav": "Nav"}
    assert merged["observed"] is True


def test_source_wins_where_it_spoke():
    source = {"headings": ["Real heading"], "fields": []}
    observed = {"headings": ["Rendered heading"]}
    merged = dom_cues.merge_into(source, observed)
    assert merged["headings"][0] == "Real heading"
    assert "Rendered heading" in merged["headings"]


def test_merging_nothing_is_a_no_op():
    source = {"headings": ["A"]}
    assert dom_cues.merge_into(source, {}) == source


# ------------------------------------------------------------------ enrichment

def test_enrichment_only_renders_thin_pages(monkeypatch):
    """The expensive path must not render pages source already described."""
    asked = []

    monkeypatch.setattr(dom_cues, "available", lambda: True)
    monkeypatch.setattr(
        dom_cues, "read_routes",
        lambda base, routes, **k: (asked.extend(routes), {r: {"headings": ["From DOM"]} for r in routes})[1],
    )
    context = {"template_cues": {
        "/rich": {"headings": ["A", "B", "C"], "fields": ["x"]},   # already described
        "/thin": {"headings": [], "fields": []},                    # needs the DOM
    }}
    changed = ps.enrich_cues_from_dom(context, "http://example.test")
    assert changed == 1
    assert asked == ["/thin"]
    assert context["template_cues"]["/thin"]["observed"] is True
    assert context["template_cues"]["/rich"].get("observed") is not True


def test_enrichment_without_a_base_url_does_nothing():
    assert ps.enrich_cues_from_dom({"template_cues": {}}, "") == 0


def test_enrichment_never_raises(monkeypatch):
    monkeypatch.setattr(dom_cues, "available", lambda: True)

    def boom(*a, **k):
        raise RuntimeError("browser gone")

    monkeypatch.setattr(dom_cues, "read_routes", boom)
    context = {"template_cues": {"/thin": {}}}
    assert ps.enrich_cues_from_dom(context, "http://example.test") == 0


def test_enrichment_skips_when_no_browser(monkeypatch):
    monkeypatch.setattr(dom_cues, "available", lambda: False)
    assert ps.enrich_cues_from_dom({"template_cues": {"/x": {}}}, "http://example.test") == 0
