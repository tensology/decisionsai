"""Section G: tickets are component-level diffs, not prose."""

import pytest

from distr.core.planning import library, plan_tickets, sitemap
from distr.core.planning.page import parse_page


def _sitemap_with(*pages):
    sm = sitemap.Sitemap()
    for page in pages:
        sm.add(page)
    return sm


def test_a_new_page_produces_add_tickets_per_component():
    sm = _sitemap_with(parse_page({"name": "a", "title": "A", "route": "/a", "blocks": ["kpi-row", "data-table"]}))
    tickets = plan_tickets.tickets_for_sitemap(sm)
    assert [(t["action"], t["component"]) for t in tickets] == [
        ("add", "kpi-row"),
        ("add", "data-table"),
    ]


def test_a_present_component_is_not_a_ticket():
    """Present and unchanged is nothing to do; an update here would be noise."""
    sm = _sitemap_with(parse_page({"name": "a", "title": "A", "blocks": ["kpi-row"]}))
    assert plan_tickets.tickets_for_sitemap(sm, current={"a": ["kpi-row"]}) == []


def test_a_component_the_page_no_longer_has_is_a_removal():
    sm = _sitemap_with(parse_page({"name": "a", "title": "A", "blocks": ["kpi-row"]}))
    tickets = plan_tickets.tickets_for_sitemap(sm, current={"a": ["kpi-row", "stale"]})
    assert [(t["action"], t["component"]) for t in tickets] == [("remove", "stale")]


def test_a_changed_component_is_an_update():
    sm = _sitemap_with(parse_page({"name": "a", "title": "A", "blocks": ["kpi-row"]}))
    tickets = plan_tickets.tickets_for_sitemap(sm, current={"a": ["kpi-row"]}, changed=["kpi-row"])
    assert [(t["action"], t["component"]) for t in tickets] == [("update", "kpi-row")]


def test_a_template_expands_into_its_declared_components():
    """A page built from a template is diffed at component level, not by name."""
    sm = sitemap.Sitemap()
    sm.drop_template(library.get("dashboard-page"), title="Dashboard", route="/")
    components = {t["component"] for t in plan_tickets.tickets_for_sitemap(sm)}
    assert components == {"app-shell", "kpi-row", "data-table"}
    assert "dashboard-page" not in components


def test_the_ticket_names_the_library_entry():
    sm = _sitemap_with(parse_page({"name": "a", "title": "A", "blocks": ["kpi-row"]}))
    ticket = plan_tickets.tickets_for_sitemap(sm)[0]
    assert "`kpi-row`" in ticket["body"]
    assert "KPI row" in ticket["title"]


def test_the_ticket_carries_the_library_wire():
    sm = _sitemap_with(parse_page({"name": "a", "title": "A", "blocks": ["kpi-row"]}))
    body = plan_tickets.tickets_for_sitemap(sm)[0]["body"]
    assert "Library wire:" in body
    assert "```" in body


def test_a_reference_missing_from_the_library_is_its_own_ticket():
    """A plan referencing something the library lacks is a gap, not an omission."""
    sm = _sitemap_with(parse_page({"name": "a", "title": "A", "blocks": ["no-such-widget"]}))
    ticket = plan_tickets.tickets_for_sitemap(sm)[0]
    assert ticket["action"] == "add"
    assert "NOT FOUND" in ticket["body"]
    assert "missing from the library" in ticket["title"]


def test_the_route_is_included_when_the_page_has_one():
    sm = _sitemap_with(parse_page({"name": "a", "title": "A", "route": "/a", "blocks": ["kpi-row"]}))
    assert "`/a`" in plan_tickets.tickets_for_sitemap(sm)[0]["body"]


def test_a_product_scoped_component_carries_its_scope():
    sm = _sitemap_with(parse_page({"name": "a", "title": "A", "blocks": ["kpi-row"]}))
    ticket = plan_tickets.tickets_for_sitemap(sm)[0]
    assert ticket["scope"] == "global"


def test_a_broken_library_does_not_break_ticketing():
    class Exploding:
        def get(self, name):
            raise RuntimeError("library down")

    sm = _sitemap_with(parse_page({"name": "a", "title": "A", "blocks": ["kpi-row"]}))
    tickets = plan_tickets.tickets_for_sitemap(sm, library_module=Exploding())
    assert tickets and tickets[0]["action"] == "add"


def test_an_empty_sitemap_produces_nothing():
    assert plan_tickets.tickets_for_sitemap(sitemap.Sitemap()) == []


def test_junk_current_is_tolerated():
    sm = _sitemap_with(parse_page({"name": "a", "title": "A", "blocks": ["kpi-row"]}))
    assert plan_tickets.tickets_for_sitemap(sm, current="nonsense")
    assert plan_tickets.tickets_for_sitemap(sm, current={"": None})


def test_tickets_are_capped(monkeypatch):
    monkeypatch.setattr(plan_tickets, "MAX_TICKETS", 2)
    sm = _sitemap_with(parse_page({"name": "a", "title": "A", "blocks": ["kpi-row", "data-table", "hero"]}))
    assert len(plan_tickets.tickets_for_sitemap(sm)) == 2


# ------------------------------------------------------------------ markdown

def test_tickets_render_as_markdown_grouped_by_page():
    sm = _sitemap_with(
        parse_page({"name": "a", "title": "A", "blocks": ["kpi-row"]}),
        parse_page({"name": "b", "title": "B", "blocks": ["hero"]}),
    )
    md = plan_tickets.tickets_markdown(plan_tickets.tickets_for_sitemap(sm), heading="Build the dashboard")
    assert md.startswith("# Build the dashboard")
    assert "## Page: a" in md and "## Page: b" in md
    assert "`kpi-row`" in md


def test_markdown_of_nothing_is_still_a_document():
    md = plan_tickets.tickets_markdown([])
    assert md.startswith("# Plan tickets")
