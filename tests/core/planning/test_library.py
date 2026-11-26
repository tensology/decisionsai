"""Section A: the planning library discovers folders like skins do."""

import json

import pytest

from distr.core.planning import library


def _write(root, sub, name, manifest):
    d = root / sub / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


def _widget(**over):
    base = {"name": "kpi-row", "kind": "widget", "wire": "row\n  content \"Total\""}
    base.update(over)
    return base


# ------------------------------------------------------------------ validation

def test_parses_a_valid_manifest():
    entry = library.parse_manifest({
        "name": "KPI Row", "kind": "widget", "title": "KPI row", "scope": "global",
        "wire": "row", "tags": ["Dashboard", "metrics"], "slots": ["a"], "version": 2,
    })
    assert entry.name == "kpi-row"
    assert entry.kind == "widget"
    assert entry.tags == ("dashboard", "metrics")
    assert entry.version == 2
    assert entry.product is None


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "not a dict",
        [],
        {},                                   # no name, no wire
        {"name": "x"},                        # no wire
        {"name": "x", "wire": "   "},          # blank wire
        {"name": "x", "kind": "nonsense", "wire": "row"},   # unknown kind
        {"wire": "row"},                      # no name and no folder
    ],
)
def test_unusable_manifests_are_skipped(raw):
    assert library.parse_manifest(raw) is None


def test_a_wire_body_over_the_cap_is_skipped():
    assert library.parse_manifest(_widget(wire="x" * (library.MAX_WIRE + 1))) is None


def test_name_is_slugified():
    entry = library.parse_manifest(_widget(name="  KPI   Row!!  "))
    assert entry.name == "kpi-row"


def test_junk_version_falls_back_to_one():
    assert library.parse_manifest(_widget(version="abc")).version == 1


# ------------------------------------------------------------------ scope

@pytest.mark.parametrize(
    "scope,expected",
    [
        ("global", None),
        ("", None),
        ("Global", None),
        ("product:player1sport", "player1sport"),
        ("product:Player1Sport", "player1sport"),
        ("player1sport", "player1sport"),          # bare name is accepted
        ("product:", None),
    ],
)
def test_scope_normalisation(scope, expected):
    entry = library.parse_manifest(_widget(scope=scope))
    assert entry.product == expected


# ------------------------------------------------------------------ discovery

def test_discovers_entries_recursively(tmp_path):
    _write(tmp_path, "widgets", "a", _widget(name="a"))
    _write(tmp_path, "deep/nested", "b", _widget(name="b"))
    found = {e.name for e in library.discover(user_root=tmp_path, builtin=tmp_path / "missing")}
    assert found == {"a", "b"}


def test_invalid_entries_are_skipped_not_fatal(tmp_path):
    _write(tmp_path, "widgets", "good", _widget(name="good"))
    _write(tmp_path, "widgets", "bad", _widget(name="bad", kind="nonsense"))
    (tmp_path / "widgets" / "broken").mkdir(parents=True)
    (tmp_path / "widgets" / "broken" / "manifest.json").write_text("{not json", encoding="utf-8")
    found = {e.name for e in library.discover(user_root=tmp_path, builtin=tmp_path / "missing")}
    assert found == {"good"}


def test_dot_folders_are_ignored(tmp_path):
    _write(tmp_path, ".hidden", "a", _widget(name="a"))
    assert library.discover(user_root=tmp_path, builtin=tmp_path / "missing") == []


def test_a_missing_root_is_not_an_error(tmp_path):
    assert library.discover(user_root=tmp_path / "nope", builtin=tmp_path / "also-nope") == []


def test_a_user_entry_overrides_the_builtin(tmp_path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    _write(builtin, "widgets", "kpi", _widget(name="kpi-row", title="Built-in"))
    _write(user, "widgets", "kpi", _widget(name="kpi-row", title="Mine"))

    entries = library.discover(user_root=user, builtin=builtin)
    assert len(entries) == 1
    assert entries[0].title == "Mine"
    assert entries[0].source == "user"


# ------------------------------------------------------------------ filtering

def test_product_entries_only_appear_for_that_product(tmp_path):
    _write(tmp_path, "widgets", "g", _widget(name="shared"))
    _write(tmp_path, "widgets", "p", _widget(name="bespoke", scope="product:player1sport"))

    general = {e.name for e in library.entries(user_root=tmp_path, builtin=tmp_path / "x")}
    assert general == {"shared"}

    scoped = {e.name for e in library.entries(user_root=tmp_path, builtin=tmp_path / "x", product="player1sport")}
    assert scoped == {"shared", "bespoke"}


def test_kind_and_tag_filters(tmp_path):
    _write(tmp_path, "w", "a", _widget(name="a", tags=["dashboard"]))
    _write(tmp_path, "p", "b", {"name": "b", "kind": "page-template", "wire": "screen \"B\""})
    assert {e.name for e in library.entries(user_root=tmp_path, builtin=tmp_path / "x", kind="widget")} == {"a"}
    assert {e.name for e in library.entries(user_root=tmp_path, builtin=tmp_path / "x", tag="dashboard")} == {"a"}


def test_get_by_name(tmp_path):
    _write(tmp_path, "w", "a", _widget(name="a"))
    assert library.get("a", user_root=tmp_path, builtin=tmp_path / "x").name == "a"
    assert library.get("missing", user_root=tmp_path, builtin=tmp_path / "x") is None


# ------------------------------------------------------------------ the panel

def test_the_panel_offers_templates_at_sitemap_level_and_widgets_inside_a_page(tmp_path):
    _write(tmp_path, "w", "a", _widget(name="a"))
    _write(tmp_path, "p", "b", {"name": "b", "kind": "page-template", "wire": "screen \"B\""})

    at_sitemap = library.panel_entries("sitemap", user_root=tmp_path, builtin=tmp_path / "x")
    in_page = library.panel_entries("page", user_root=tmp_path, builtin=tmp_path / "x")
    assert {e.name for e in at_sitemap} == {"b"}
    assert {e.name for e in in_page} == {"a"}
    assert {e.name for e in library.panel_entries("flow", user_root=tmp_path, builtin=tmp_path / "x")} == {"b"}


# ------------------------------------------------------------------ the shipped spine

def test_the_builtin_spine_loads():
    names = {e.name for e in library.discover()}
    assert {"default-base", "app-shell", "kpi-row", "dashboard-page"} <= names
    # every shipped entry is valid and global
    for entry in library.discover():
        assert entry.kind in library.KINDS
        assert entry.wire.strip()
        assert entry.product is None


def test_the_shipped_base_template_exists():
    base = library.get("default-base")
    assert base is not None and base.kind == "base-template"
