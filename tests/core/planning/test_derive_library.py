"""Section F: derive library candidates from a project, library first."""

import json

import pytest

from distr.core.planning import derive_library as dl
from distr.core.planning import library


def _project(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    return tmp_path, src


def test_derives_widgets_and_skips_pages(tmp_path):
    root, src = _project(tmp_path)
    (src / "BasketSummary.tsx").write_text("export function BasketSummary(p) { return null }")
    (src / "UserCard.tsx").write_text("interface UserCardProps { n: string }\nexport const UserCard = () => null")
    (src / "DashboardPage.tsx").write_text("export default function DashboardPage() { return null }")
    (src / "AppShell.tsx").write_text("export function AppShell() { return null }")

    result = dl.derive_entries(root)
    names = {e["name"] for e in result["entries"]}
    # widgets are derived; page/layout/shell names are not widgets
    assert {"basket-summary", "user-card"} <= names
    assert "dashboard-page" not in names
    assert "app-shell" not in names


def test_the_library_comes_first(tmp_path):
    """A component the library already covers is reported, not re-derived."""
    root, src = _project(tmp_path)
    (src / "Hero.tsx").write_text("export function Hero() { return null }")
    (src / "DataTable.tsx").write_text("export function DataTable() { return null }")

    result = dl.derive_entries(root)
    assert "hero" in result["covered"]
    assert "data-table" in result["covered"]
    assert result["entries"] == []


def test_derived_entries_are_product_scoped(tmp_path):
    root, src = _project(tmp_path)
    (src / "Widget.tsx").write_text("export function Widget() { return null }")
    entry = dl.derive_entries(root, product="Player1Sport")["entries"][0]
    assert entry["scope"] == "product:player1sport"
    assert entry["derived"] is True


def test_without_a_product_entries_are_global(tmp_path):
    root, src = _project(tmp_path)
    (src / "Widget.tsx").write_text("export function Widget() { return null }")
    assert dl.derive_entries(root)["entries"][0]["scope"] == "global"


def test_dependency_folders_are_ignored(tmp_path):
    root, src = _project(tmp_path)
    (src / "Real.tsx").write_text("export function Real() { return null }")
    junk = root / "node_modules" / "pkg"
    junk.mkdir(parents=True)
    (junk / "Junk.tsx").write_text("export function Junk() { return null }")
    names = {e["name"] for e in dl.derive_entries(root)["entries"]}
    assert names == {"real"}


def test_only_component_suffixes_are_scanned(tmp_path):
    root, src = _project(tmp_path)
    (src / "styles.css").write_text(".a { color: red }")
    (src / "notes.md").write_text("export function NotReal() {}")
    assert dl.derive_entries(root)["entries"] == []


def test_a_missing_project_yields_nothing(tmp_path):
    result = dl.derive_entries(tmp_path / "nope")
    assert result == {"entries": [], "covered": [], "scanned": 0}


def test_derivation_never_raises_on_junk(tmp_path):
    assert dl.derive_entries(None)["entries"] == []
    assert dl.derive_entries("")["entries"] == []


def test_entries_are_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "MAX_ENTRIES", 3)
    root, src = _project(tmp_path)
    for i in range(10):
        (src / f"Widget{i}.tsx").write_text(f"export function Widget{i}() {{ return null }}")
    assert len(dl.derive_entries(root)["entries"]) == 3


def test_a_broken_library_does_not_stop_derivation(tmp_path):
    root, src = _project(tmp_path)
    (src / "Thing.tsx").write_text("export function Thing() { return null }")

    class Exploding:
        def discover(self):
            raise RuntimeError("library down")

    assert {e["name"] for e in dl.derive_entries(root, library_module=Exploding())["entries"]} == {"thing"}


# ------------------------------------------------------------------ writing

def test_candidates_are_written_and_then_discovered(tmp_path):
    root, src = _project(tmp_path)
    (src / "FancyWidget.tsx").write_text("export function FancyWidget() { return null }")
    target = tmp_path / "library"

    written = dl.write_candidates(dl.derive_entries(root, product="Snuza"), target)
    assert written == ["fancy-widget"]

    found = library.entries(user_root=target, builtin=tmp_path / "none", product="snuza")
    assert [e.name for e in found] == ["fancy-widget"]
    assert found[0].product == "snuza"


def test_writing_nothing_is_not_an_error(tmp_path):
    assert dl.write_candidates({"entries": []}, tmp_path) == []
    assert dl.write_candidates({}, tmp_path) == []
    assert dl.write_candidates(None, tmp_path) == []


def test_written_manifests_drop_the_derived_flag(tmp_path):
    root, src = _project(tmp_path)
    (src / "Thing.tsx").write_text("export function Thing() { return null }")
    target = tmp_path / "library"
    dl.write_candidates(dl.derive_entries(root), target)
    manifest = json.loads((target / "derived" / "thing" / "manifest.json").read_text())
    assert "derived" not in manifest
    assert manifest["kind"] == "widget"
