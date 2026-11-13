"""OpenPencil read-side bridge: pin, reader on real upstream fixtures, gaps, associations, routes."""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest

from distr.core.open_pencil import (
    PIN_CLI_VERSION,
    PIN_SHA,
    OpenPencilCliMissing,
    OpenPencilInvalidDocument,
    OpenPencilPinMismatch,
    check_pin,
    create_tickets_from_gaps,
    draft_ticket_specs,
    find_gaps,
    inspect_document,
    validate_document_path,
)
from distr.core.open_pencil import associations
from distr.core.plugins.paths import open_design_reference_dir, open_pencil_cli_dir, open_pencil_reference_dir

FIXTURES = open_pencil_cli_dir() / "fixtures"  # LFS objects fetched at the pinned SHA, sha256-verified
SLOTS_FIG = FIXTURES / "slots.fig"
PEN_BUTTON = open_pencil_reference_dir() / "tests" / "fixtures" / "pencil_button.pen"
PEN_SIMPLE = open_pencil_reference_dir() / "tests" / "fixtures" / "pencil_simple.pen"

_PIN = check_pin()
needs_cli = pytest.mark.skipif(not _PIN.ok, reason=f"pinned OpenPencil CLI unavailable: {_PIN.problems}")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_open_pencil_is_distinct_from_open_design():
    assert open_pencil_reference_dir().name == "open-pencil"
    assert open_pencil_reference_dir() != open_design_reference_dir()
    assert open_pencil_reference_dir().parent == open_design_reference_dir().parent


def test_pin_is_exact_not_dist_tag():
    assert PIN_CLI_VERSION == "0.17.0-beta.4"
    assert PIN_SHA == "9e43b6d3b26cea2badcf3707489da579696b9857"


@pytest.mark.parametrize("bad", ["", "relative/file.fig", "https://example.com/a.fig", "file:///tmp/a.fig"])
def test_validate_rejects_non_local_paths(bad):
    with pytest.raises(OpenPencilInvalidDocument):
        validate_document_path(bad)


def test_validate_rejects_wrong_suffix_missing_and_lfs_pointer(tmp_path):
    other = tmp_path / "a.txt"
    other.write_text("x")
    with pytest.raises(OpenPencilInvalidDocument):
        validate_document_path(str(other))
    with pytest.raises(OpenPencilInvalidDocument):
        validate_document_path(str(tmp_path / "missing.fig"))
    pointer = tmp_path / "p.fig"
    pointer.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n")
    with pytest.raises(OpenPencilInvalidDocument):
        validate_document_path(str(pointer))


def test_fails_closed_when_cli_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("DECISIONS_OPEN_PENCIL_CLI", str(tmp_path / "nope" / "openpencil"))
    with pytest.raises(OpenPencilCliMissing):
        inspect_document(str(tmp_path / "x.fig"))


def test_fails_closed_on_pin_mismatch(monkeypatch, tmp_path):
    fake = tmp_path / "openpencil"
    fake.write_text("#!/bin/sh\necho 0.16.0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("DECISIONS_OPEN_PENCIL_CLI", str(fake))
    with pytest.raises(OpenPencilPinMismatch):
        inspect_document(str(tmp_path / "x.fig"), require_reference=False)


@needs_cli
def test_reads_real_slots_fig_without_modifying_it():
    before = _sha(SLOTS_FIG)
    result = inspect_document(str(SLOTS_FIG))
    assert _sha(SLOTS_FIG) == before == result["source"]["sha256"]
    summary = result["summary"]
    assert summary["pages"] == 2 and summary["total_nodes"] == 96
    assert summary["components"] == 8 and summary["component_sets"] == 1
    assert summary["slots"] == 18 and summary["instances"] == 23
    names = {p["name"] for p in result["pages"]}
    assert names == {"Page 1", "Slots fixture"}
    panel = next(c for c in result["components"] if c["name"] == "Panel")
    kinds = {p["type"] for p in panel["properties"]}
    assert kinds == {"SLOT", "VARIANT"}
    assert all(isinstance(page["layers"], list) for page in result["pages"])
    assert result["reader"]["cli_version"] == PIN_CLI_VERSION


@needs_cli
def test_gaps_on_real_slots_fig():
    result = inspect_document(str(SLOTS_FIG))
    gaps = find_gaps(result)
    counts = gaps["counts"]
    assert counts["untracked_components"] == 7  # 8 components + 1 set, minus 2 variants of the set
    assert counts["empty_slots_gap"] == 3 and counts["empty_slots_info"] == 4
    assert gaps["format_has_slots"] is True and gaps["bindings_in_use"] is True
    tracked = find_gaps(result, [{"id": 41, "title": "Build the Panel component", "description": "Card empty slot too"}])
    tracked_names = {t["name"] for t in tracked["tracked"]}
    assert {"Panel", "Card empty slot"} <= tracked_names
    assert tracked["counts"]["untracked_components"] == 5


@needs_cli
@pytest.mark.parametrize("pen", [PEN_BUTTON, PEN_SIMPLE])
def test_reads_real_pen_files(pen):
    result = inspect_document(str(pen))
    assert result["source"]["format"] == "pen"
    assert result["summary"]["pages"] == 1
    assert result["summary"]["bound_layers"] >= 1


def test_no_binding_gaps_claimed_when_file_has_no_variables():
    inspection = {"components": [], "frames": [{"id": "1:1", "name": "Home", "page": "P"}], "slots": [],
                  "bindings": [], "variables": {"count": 0}, "text_in_components": [
                      {"id": "1:2", "name": "T", "componentName": "C", "bound": []}]}
    gaps = find_gaps(inspection)
    assert gaps["bindings_in_use"] is False
    assert gaps["unbound_text_in_components"] == []
    assert "not in use" in gaps["binding_note"]
    assert gaps["counts"]["untracked_frames"] == 1


def test_ticket_generation_is_dry_run_only():
    inspection = {"source": {"path": "/x/a.fig", "sha256": "f" * 64}}
    gaps = {"untracked_components": [{"id": "1:1", "name": "Card", "type": "COMPONENT", "page": "P"}]}
    drafts = draft_ticket_specs(inspection, gaps)
    assert drafts and all(d["dry_run"] for d in drafts)
    with pytest.raises(NotImplementedError):
        create_tickets_from_gaps(inspection, gaps)


def test_associations_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISIONS_OPEN_PENCIL_STORE", str(tmp_path / "links.json"))
    design = tmp_path / "screens.pen"
    design.write_text('{"version": "2.6", "children": []}')
    rec = associations.associate(7, "interfaces/login", str(design), note="login screens")
    assert rec["path"] == str(design.resolve())
    assert associations.get_association(7, "interfaces/login")["format"] == "pen"
    assert [r["page_key"] for r in associations.list_associations(7)] == ["interfaces/login"]
    assert associations.list_associations(8) == []
    assert associations.remove_association(7, "interfaces/login") is True
    assert associations.get_association(7, "interfaces/login") is None
    with pytest.raises(OpenPencilInvalidDocument):
        associations.associate(7, "p", "https://example.com/a.fig")


def _client():
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    from distr.gui.web.routes.development import open_pencil
    from distr.gui.web.security import require_internal_token_dependency

    app = FastAPI()
    router = APIRouter()
    open_pencil.register_routes(router, None)
    app.include_router(router)
    app.dependency_overrides[require_internal_token_dependency] = lambda: None
    return TestClient(app)


def test_routes_require_token():
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    from distr.gui.web.routes.development import open_pencil

    app = FastAPI()
    router = APIRouter()
    open_pencil.register_routes(router, None)
    app.include_router(router)
    assert TestClient(app).get("/workflows/studio/open-pencil/status").status_code == 401


def test_routes_link_and_reject_invalid(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISIONS_OPEN_PENCIL_STORE", str(tmp_path / "links.json"))
    client = _client()
    bad = client.put("/workflows/studio/plan-workspaces/3/open-pencil/links", json={"page_key": "p", "path": "https://x/a.fig"})
    assert bad.status_code == 422
    missing = client.post("/workflows/studio/plan-workspaces/3/open-pencil/links/inspect", json={"page_key": "nothing"})
    assert missing.status_code == 404


@needs_cli
def test_routes_inspect_linked_real_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISIONS_OPEN_PENCIL_STORE", str(tmp_path / "links.json"))
    client = _client()
    assert client.get("/workflows/studio/open-pencil/status").json()["ok"] is True
    put = client.put("/workflows/studio/plan-workspaces/3/open-pencil/links", json={"page_key": "interfaces/cards", "path": str(SLOTS_FIG)})
    assert put.status_code == 200
    res = client.post("/workflows/studio/plan-workspaces/3/open-pencil/links/inspect", json={"page_key": "interfaces/cards"})
    body = res.json()
    assert res.status_code == 200, body
    assert body["gaps"]["counts"]["empty_slots_gap"] == 3
    assert all(d["dry_run"] for d in body["ticket_drafts"])
    assert "nothing was created" in body["ticket_generation"]
