"""The plan IS the workspace's flows wire - not a parallel sitemap.

Everything here reads and writes the same plan item the canvas renders, so a drop
lands on a real page and there is no second model to reconcile.
"""

import pytest

from distr.core.planning import plan_store

FLOWS = '''# @generated scan:overview
screen "Overview" device=desktop layer=guidance
  stack
    heading "Journey"
    link "Locale" route="/locale"
    link "Identity" route="/identity"

screen "Locale" device=desktop route="/locale"
  stack
    heading "Locale"
    text "Pick your language."

screen "Identity" device=desktop route="/identity"
  stack
    heading "Identity"
'''


def _entry(name="kpi-row", kind="widget", wire='row\n  content "Total"'):
    from distr.core.planning.library import parse_manifest

    return parse_manifest({"name": name, "kind": kind, "wire": wire, "title": name.title()})


@pytest.fixture()
def flows(monkeypatch):
    """A workspace whose flows item is the given wire."""
    state = {"content": FLOWS, "revision": 1}

    class Service:
        @staticmethod
        def get_workspace(workspace_id, **k):
            return {"id": workspace_id, "items": [
                {"id": 7, "item_type": "flows", "content": state["content"],
                 "content_format": "wire", "revision_count": state["revision"]},
            ]}

        @staticmethod
        def update_item(item_id, *, content=None, expected_revision=None, source=""):
            state["content"] = content
            state["revision"] = int(expected_revision or 1) + 1
            return {"id": item_id, "content": content}

    import distr.core.planning.service as service
    monkeypatch.setattr(service, "get_workspace", Service.get_workspace)
    monkeypatch.setattr(service, "update_item", Service.update_item)
    return state


# ------------------------------------------------------------------ reading

def test_pages_come_from_the_flows_wire(flows):
    pages = plan_store.list_pages(1)
    assert [p["name"] for p in pages] == ["overview", "locale", "identity"]
    assert pages[1]["route"] == "/locale"
    assert pages[1]["title"] == "Locale"


def test_no_flows_item_means_no_pages(monkeypatch):
    import distr.core.planning.service as service
    monkeypatch.setattr(service, "get_workspace", lambda *a, **k: {"items": []})
    assert plan_store.list_pages(1) == []


def test_a_broken_service_does_not_raise(monkeypatch):
    import distr.core.planning.service as service

    def boom(*a, **k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(service, "get_workspace", boom)
    assert plan_store.list_pages(1) == []


# ------------------------------------------------------------------ placing a page

def test_placing_a_page_template_appends_a_screen(flows):
    entry = _entry("dashboard-page", kind="page-template", wire='screen "Dashboard" route="/dashboard"\n  heading "Dashboard"')
    result = plan_store.place_page(1, entry)
    assert result["placed"] is True
    assert result["created_page"] == "dashboard"
    assert "screen \"Dashboard\"" in flows["content"]
    assert len(plan_store.list_pages(1)) == 4


def test_the_same_page_is_not_added_twice(flows):
    entry = _entry("dashboard-page", kind="page-template", wire='screen "Dashboard"\n  heading "D"')
    plan_store.place_page(1, entry)
    again = plan_store.place_page(1, entry)
    assert again["placed"] is False
    assert "already in the plan" in again["error"]


def test_a_wire_without_a_screen_is_wrapped(flows):
    entry = _entry("bare", kind="page-template", wire='heading "Bare"')
    result = plan_store.place_page(1, entry)
    assert result["placed"] is True
    assert 'screen "Bare"' in flows["content"]


def test_an_entry_with_no_wire_is_refused(flows):
    entry = _entry("empty", kind="page-template", wire="x")
    entry = type(entry)(**{**entry.__dict__, "wire": ""}) if hasattr(entry, "__dict__") else entry
    assert plan_store.place_page(1, _entry("nowire", wire="   "))["placed"] is False


def test_placing_a_page_without_a_flows_item_is_refused(monkeypatch):
    import distr.core.planning.service as service
    monkeypatch.setattr(service, "get_workspace", lambda *a, **k: {"items": []})
    result = plan_store.place_page(1, _entry("dashboard-page", kind="page-template", wire='screen "D"'))
    assert result["placed"] is False
    assert "no flows item" in result["error"]


# ------------------------------------------------------------------ placing a widget

def test_placing_a_widget_inserts_into_the_page_screen(flows):
    result = plan_store.place_widget(1, "locale", _entry())
    assert result["placed"] is True
    content = flows["content"]
    locale_block = content.split('screen "Locale"')[1].split('screen "Identity"')[0]
    assert "Total" in locale_block          # the widget landed inside Locale
    assert "Total" not in content.split('screen "Identity"')[1]


def test_the_same_widget_is_not_added_twice(flows):
    plan_store.place_widget(1, "locale", _entry())
    again = plan_store.place_widget(1, "locale", _entry())
    assert again["placed"] is False
    assert "already on this page" in again["error"]


def test_placing_onto_an_unknown_page_is_refused(flows):
    result = plan_store.place_widget(1, "no-such-page", _entry())
    assert result["placed"] is False
    assert "no page called" in result["error"]


def test_placing_an_entry_with_no_wire_is_refused(flows):
    assert plan_store.place_widget(1, "locale", _entry("nowire", wire="   "))["placed"] is False


def test_an_empty_page_name_is_refused(flows):
    assert plan_store.place_widget(1, "", _entry())["placed"] is False


def test_a_size_limit_is_enforced(flows, monkeypatch):
    monkeypatch.setattr(plan_store, "MAX_FLOWS_BYTES", 10)
    assert plan_store.place_widget(1, "locale", _entry())["placed"] is False


# ------------------------------------------------------------------ helpers

def test_screen_bounds_finds_a_block():
    lines = FLOWS.splitlines()
    bounds = plan_store._screen_bounds(lines, "locale")
    assert bounds is not None
    start, stop = bounds
    assert lines[start].strip().startswith('screen "Locale"')
    assert "Identity" in lines[stop]


def test_screen_bounds_returns_none_for_a_missing_page():
    assert plan_store._screen_bounds(FLOWS.splitlines(), "nope") is None


def test_screens_parses_name_title_and_route():
    screens = plan_store._screens(FLOWS)
    assert [(s[0], s[2]) for s in screens] == [
        ("overview", ""), ("locale", "/locale"), ("identity", "/identity"),
    ]


def test_append_helpers_keep_indentation():
    assert plan_store._screen_indent("    heading") == "    "
    assert plan_store._slug("Locale / Something!") == "locale-something"


# ------------------------------------------------------------------ tickets

def test_tickets_are_written_into_the_planning_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_store, "_seen_tickets", None, raising=False)
    result = plan_store.save_tickets(tmp_path / "planning", [
        {"action": "add", "page": "locale", "component": "kpi-row", "title": "Add KPI row", "body": "- **Library entry:** `kpi-row`"},
    ])
    assert result["saved"] is True
    index = plan_store.load_tickets_index(tmp_path / "planning")
    assert "`kpi-row`" in index


def test_tickets_never_leave_the_planning_folder(tmp_path):
    plan_store.save_tickets(tmp_path / "planning", [{"action": "add", "page": "a", "component": "x", "title": "T", "body": "B"}])
    written = {p.relative_to(tmp_path / "planning").parts[0] for p in (tmp_path / "planning").rglob("*.md")}
    assert written == {"tickets"}
