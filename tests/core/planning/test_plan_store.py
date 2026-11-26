"""Plans live in the PROJECT'S PLANNING FOLDER, not the database or downloads."""

import json

import pytest

from distr.core.planning import library, plan_store, plan_tickets, sitemap
from distr.core.planning.page import parse_page


@pytest.fixture()
def planning(tmp_path):
    return tmp_path / "project" / "planning"


def _sitemap():
    sm = sitemap.Sitemap(title="Ops")
    sm.drop_template(library.get("dashboard-page"), title="Dashboard", route="/")
    return sm


# ------------------------------------------------------------------ sitemap

def test_a_sitemap_round_trips_through_the_planning_folder(planning):
    sm = _sitemap()
    result = plan_store.save_sitemap(planning, sm)
    assert result["saved"] is True
    assert (planning / "sitemap.json").is_file()

    back = plan_store.load_sitemap(planning)
    assert back.title == "Ops"
    assert back.routes() == ["/"]
    assert [b.ref for b in back.find("dashboard").page.blocks] == ["dashboard-page"]


def test_the_rendered_wire_is_written_beside_it(planning):
    sm = _sitemap()
    wire, _ = sm.to_wire()
    plan_store.save_sitemap(planning, sm, wire=wire)
    text = (planning / "sitemap.wire").read_text()
    assert text.startswith('screen "Dashboard"')


def test_a_missing_folder_yields_an_empty_sitemap(tmp_path):
    back = plan_store.load_sitemap(tmp_path / "nope")
    assert back.count() == 0


def test_a_corrupt_sitemap_is_treated_as_empty(planning):
    planning.mkdir(parents=True)
    (planning / "sitemap.json").write_text("{not json", encoding="utf-8")
    assert plan_store.load_sitemap(planning).count() == 0


def test_no_root_is_refused_not_scanned(tmp_path):
    """An empty root must not become the current directory."""
    assert plan_store.save_sitemap("", _sitemap())["saved"] is False
    assert plan_store.save_sitemap(None, _sitemap())["saved"] is False


def test_the_planning_folder_is_created_on_demand(planning):
    assert not planning.exists()
    plan_store.save_sitemap(planning, _sitemap())
    assert planning.is_dir()


def test_writes_are_atomic_leaving_no_temp_files(planning):
    plan_store.save_sitemap(planning, _sitemap())
    leftovers = [p.name for p in planning.iterdir() if p.name.startswith(".plan-")]
    assert leftovers == []


# ------------------------------------------------------------------ library

def test_a_project_entry_is_stored_under_the_planning_folder(planning):
    from distr.core.planning.library import parse_manifest

    entry = parse_manifest(
        {"name": "trading-ticker", "kind": "widget", "wire": 'content "ticker"', "scope": "product:trading"},
        source="user",
    )
    result = plan_store.save_project_entry(planning, entry)
    assert result["saved"] is True
    manifest = planning / "library" / "trading-ticker" / "manifest.json"
    assert json.loads(manifest.read_text())["name"] == "trading-ticker"

    # and it is discovered from there, for that product only
    found = library.entries(user_root=planning / "library", builtin=planning / "none", product="trading")
    assert [e.name for e in found] == ["trading-ticker"]


def test_saving_junk_as_an_entry_is_refused(planning):
    assert plan_store.save_project_entry(planning, {"no": "name"})["saved"] is False
    assert plan_store.save_project_entry(planning, None)["saved"] is False


# ------------------------------------------------------------------ tickets

def test_tickets_are_written_into_the_planning_folder(planning):
    sm = _sitemap()
    plan_store.save_sitemap(planning, sm)
    tickets = plan_tickets.tickets_for_sitemap(sm)

    result = plan_store.save_tickets(planning, tickets)
    assert result["saved"] is True
    assert (planning / "tickets" / "index.md").is_file()
    assert "dashboard.md" in result["pages"]

    index = plan_store.load_tickets_index(planning)
    assert index.startswith("# Plan tickets")
    assert "`app-shell`" in index


def test_tickets_never_go_outside_the_planning_folder(planning):
    """The internal agent's platform notes are a DIFFERENT destination."""
    plan_store.save_tickets(planning, plan_tickets.tickets_for_sitemap(_sitemap()))
    written = {p.relative_to(planning).parts[0] for p in planning.rglob("*.md")}
    assert written == {"tickets"}


def test_saving_no_tickets_is_not_an_error(planning):
    assert plan_store.save_tickets(planning, [])["saved"] is True
    assert plan_store.save_tickets(planning, None)["saved"] is True


def test_the_tickets_index_is_empty_when_there_are_none(tmp_path):
    assert plan_store.load_tickets_index(tmp_path / "nope") == ""


def test_a_page_name_with_slashes_stays_a_single_file(planning):
    tickets = [{"action": "add", "page": "a/b c", "component": "x", "title": "T", "body": "B"}]
    plan_store.save_tickets(planning, tickets)
    names = sorted(p.name for p in (planning / "tickets").iterdir())
    assert names == ["a-b-c.md", "index.md"]


# ------------------------------------------------------------------ isolation

def test_everything_reads_back_out_of_the_planning_folder(planning):
    """Persist and read everything from there - the whole round trip."""
    sm = _sitemap()
    wire, _ = sm.to_wire()
    plan_store.save_sitemap(planning, sm, wire=wire)

    reloaded = plan_store.load_sitemap(planning)
    tickets = plan_tickets.tickets_for_sitemap(reloaded)
    plan_store.save_tickets(planning, tickets)

    assert reloaded.count() == 1
    assert tickets
    assert (planning / "sitemap.json").is_file()
    assert (planning / "sitemap.wire").is_file()
    assert plan_store.load_tickets_index(planning)
