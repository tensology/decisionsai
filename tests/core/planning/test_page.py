"""Section C: a page places components; the base template governs them."""

import pytest

from distr.core.planning import base_template as bt
from distr.core.planning import library, page


# ------------------------------------------------------------------ parsing

def test_parses_a_page_with_blocks():
    p = page.parse_page({
        "name": "Ops Dashboard", "title": "Ops", "route": "/ops",
        "base": "default-base", "blocks": ["kpi-row", {"ref": "data-table", "span": 8}],
        "overrides": {"tokens": {"color.accent": "#000"}},
    })
    assert p.name == "ops-dashboard"
    assert [b.ref for b in p.blocks] == ["kpi-row", "data-table"]
    assert p.blocks[1].span == 8
    assert p.base == "default-base"


@pytest.mark.parametrize("raw", [None, "x", [], {}, {"blocks": []}])
def test_unusable_pages_are_rejected(raw):
    assert page.parse_page(raw) is None


def test_a_bare_ref_string_is_a_block():
    assert page.parse_block("kpi-row").ref == "kpi-row"


@pytest.mark.parametrize("raw", [None, 5, {}, {"ref": ""}, {"ref": "   "}])
def test_unusable_blocks_are_rejected(raw):
    assert page.parse_block(raw) is None


def test_an_out_of_range_span_is_dropped_not_guessed():
    assert page.parse_block({"ref": "a", "span": 99}).span is None
    assert page.parse_block({"ref": "a", "span": "junk"}).span is None
    assert page.parse_block({"ref": "a", "span": 6}).span == 6


def test_a_page_survives_junk_blocks():
    p = page.parse_page({"title": "T", "blocks": ["good", None, {"ref": ""}, 7, "alsogood"]})
    assert [b.ref for b in p.blocks] == ["good", "alsogood"]


def test_blocks_are_capped():
    p = page.parse_page({"title": "T", "blocks": ["a"] * (page.MAX_BLOCKS + 20)})
    assert len(p.blocks) == page.MAX_BLOCKS


# ------------------------------------------------------------------ template -> page

def test_a_page_from_a_template_carries_the_template():
    tpl = library.get("dashboard-page")
    p = page.page_from_template(tpl, title="Ops dashboard", route="/ops")
    assert p.title == "Ops dashboard"
    assert p.route == "/ops"
    assert [b.ref for b in p.blocks] == ["dashboard-page"]


def test_a_template_contributes_its_body_not_a_nested_screen():
    """A page-template has its own screen header; the page supplies the screen."""
    tpl = library.get("dashboard-page")
    wire, missing = page.compose_wire(page.page_from_template(tpl, title="Ops"))
    assert missing == []
    assert wire.count("screen ") == 1
    assert wire.startswith('screen "Ops"')
    assert "Dashboard" in wire          # the template's content survived


# ------------------------------------------------------------------ composition

def test_compose_pairs_blocks_with_library_entries():
    p = page.parse_page({"title": "T", "blocks": ["kpi-row", "data-table"]})
    wire, missing = page.compose_wire(p)
    assert missing == []
    assert "Total" in wire and "Name" in wire


def test_a_missing_reference_is_reported_not_silently_dropped():
    p = page.parse_page({"title": "T", "blocks": ["kpi-row", "no-such-widget"]})
    wire, missing = page.compose_wire(p)
    assert missing == ["no-such-widget"]
    assert "Total" in wire              # the good block still rendered


def test_a_library_failure_does_not_break_composition():
    class Exploding:
        def get(self, name):
            raise RuntimeError("library down")

        def entries(self, **kwargs):
            return []

    wire, missing = page.compose_wire(page.parse_page({"title": "T", "blocks": ["a"]}), library_module=Exploding())
    assert missing == ["a"]
    assert wire.startswith('screen "T"')


def test_the_screen_header_carries_id_and_route():
    p = page.parse_page({"name": "ops", "title": "Ops", "route": "/ops", "blocks": []})
    wire, _ = page.compose_wire(p)
    assert wire.startswith('screen "Ops" id=ops route=/ops')


# ------------------------------------------------------------------ inheritance

def test_a_page_renders_from_the_base_when_it_overrides_nothing():
    """No overrides means the page IS the base, whichever base is configured."""
    p = page.parse_page({"title": "T", "blocks": []})
    assert page.effective_base(p) == bt.load()


def test_a_page_override_changes_only_that_page():
    plain = page.effective_base(page.parse_page({"title": "A", "blocks": []}))
    styled = page.effective_base(page.parse_page({
        "title": "B", "blocks": [], "overrides": {"tokens": {"color.accent": "#000000"}},
    }))
    assert plain.tokens["color.accent"] != "#000000"
    assert styled.tokens["color.accent"] == "#000000"
    # everything else still inherited
    assert styled.tokens["color.ink"] == plain.tokens["color.ink"]


def test_a_page_may_override_a_contract():
    p = page.parse_page({"title": "T", "blocks": [], "overrides": {"contracts": {"form.field": "label"}}})
    assert page.effective_base(p).contracts["form.field"] == "label"


def test_round_trip_as_dict():
    p = page.parse_page({"title": "T", "blocks": ["kpi-row"], "overrides": {"tokens": {"a": "b"}}})
    again = page.parse_page(p.as_dict())
    assert again.blocks == p.blocks
    assert again.overrides == p.overrides
