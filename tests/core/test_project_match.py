"""Project attribution is a unique folder match, reversible, and not a client-chosen id."""
from __future__ import annotations

import json
import sys
import types
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _stub_package(name: str, path: Path) -> None:
    if name in sys.modules:
        return
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    module.__package__ = name
    sys.modules[name] = module


_stub_package("distr.gui.web", _ROOT / "distr/gui/web")

from distr.core.db.kanban import ProjectExecutionSession
from distr.core.db.projects import Project
from distr.core.reports.projection_store import (
    ProjectionCorrection,
    ProjectionStoreError,
    apply_correction,
    attach_reporting_corrections,
    create_backup,
    evidence,
    revoke_correction,
)
from tests.core.test_projection_store import _db, _mac_month


def _pi(root: Path, name: str, cwd: str, prompt: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.jsonl").write_text(
        json.dumps({"id": name, "cwd": cwd, "timestamp": "2026-10-30T10:00:00Z", "prompt": prompt}) + "\n"
        + json.dumps({"timestamp": "2026-10-30T10:00:10Z"}) + "\n",
        encoding="utf-8",
    )


def _apply_project(get_session, directory, manifest, ref, fingerprint):
    return apply_correction(get_session, directory, {
        "backup_id": manifest["backup_id"], "source": "pi_local", "ref": ref,
        "fingerprint": fingerprint, "reason": "assign_project", "provenance": "unique_folder_match",
        "confirm": "apply-projection-correction",
        "window_start": "2026-10-30T00:00:00Z", "window_end": "2026-10-31T00:00:00Z",
    })


def test_unique_folder_match_changes_the_report_and_revoke_reverts(tmp_path):
    mac = _mac_month()
    get_session = _db(tmp_path)
    with get_session() as db:
        project = Project(name="Alpha", folder_location="/work/alpha")
        other = Project(name="Beta", folder_location="/work/beta")
        execution = ProjectExecutionSession(
            project_id=19, started_at=datetime(2026, 10, 30, 8, 0),
            completed_at=datetime(2026, 10, 30, 8, 5), updated_at=datetime(2026, 10, 30, 8, 5),
            status="completed", route_backend="pi", origin="run",
        )
        db.add_all([project, other, execution])
        db.flush()
        project_id = int(project.id)
        execution_id = int(execution.id)
    pi = tmp_path / "pi"
    _pi(pi, "alpha", "/work/alpha/src", "please work on Beta")
    directory = tmp_path / "backups"
    manifest = create_backup(get_session, directory, ["pi_local", "execution"], datetime(2026, 10, 30), datetime(2026, 10, 31), pi_root=pi)
    found = evidence(get_session, directory, manifest["backup_id"])
    candidate = next(row for row in found["candidates"] if row["ref"] == "pi_local:alpha")
    assert candidate["project_id"] == project_id
    assert candidate["project_provenance"] == "unique_folder_match"
    assert candidate["session_root"] == "/work/alpha/src"
    assert "please work on Beta" not in json.dumps(found)
    applied = _apply_project(get_session, directory, manifest, candidate["ref"], candidate["fingerprint"])
    assert applied["project_id"] == project_id and applied["applied"] is True
    again = _apply_project(get_session, directory, manifest, candidate["ref"], candidate["fingerprint"])
    assert again["idempotent"] is True and again["correction_id"] == applied["correction_id"]
    attach_reporting_corrections(get_session, month_module=mac)
    try:
        blocks, _counts, _status = mac.collect_local_sessions(
            datetime(2026, 10, 30), datetime(2026, 10, 31), type("M", (), {"match": lambda self, cwd: None})(), pi_root=pi,
        )
        matched = next(block for block in blocks if block["ref"] == "pi_local:alpha")
        assert matched["project_id"] == project_id
        period = mac.resolve_period(start="2026-10-30", end="2026-10-30")
        report = mac.get_period_report(period, None, get_session=get_session, pi_root=pi, include_local=True, include_automations=False)
        named = [row for row in report["by_project"] if row["project_id"] == project_id]
        assert named and named[0]["name"] == "Alpha"
        assert all(row["name"] != "Beta" for row in report["by_project"])
        revoke_correction(get_session, {
            "correction_id": applied["correction_id"], "fingerprint": candidate["fingerprint"],
            "backup_id": manifest["backup_id"], "confirm": "revoke-projection-correction",
        })
        blocks, _counts, _status = mac.collect_local_sessions(
            datetime(2026, 10, 30), datetime(2026, 10, 31), type("M", (), {"match": lambda self, cwd: None})(), pi_root=pi,
        )
        assert next(block for block in blocks if block["ref"] == "pi_local:alpha")["project_id"] is None
    finally:
        if getattr(mac.collect_local_sessions, "_projection_wrapped", False):
            mac.collect_local_sessions = mac.collect_local_sessions._projection_original
    with get_session() as db:
        kept = db.get(ProjectExecutionSession, execution_id)
        assert kept.project_id == 19 and kept.status == "completed"
        assert db.query(ProjectionCorrection).count() == 0
    try:
        apply_correction(get_session, directory, {
            "backup_id": manifest["backup_id"], "source": "execution", "ref": f"execution:{execution_id}",
            "fingerprint": "0" * 64, "reason": "assign_project", "provenance": "unique_folder_match",
            "confirm": "apply-projection-correction",
            "window_start": "2026-10-30T00:00:00Z", "window_end": "2026-10-31T00:00:00Z",
            "project_id": 19,
        })
    except ProjectionStoreError as exc:
        assert exc.code == "reason"
    else:
        raise AssertionError("a client-chosen project id was accepted")
    with get_session() as db:
        assert db.get(ProjectExecutionSession, execution_id).project_id == 19


def test_ambiguous_or_prompt_only_session_stays_unmatched(tmp_path):
    get_session = _db(tmp_path)
    with get_session() as db:
        db.add_all([
            Project(name="One", folder_location="/work/shared"),
            Project(name="Two", folder_location="/work/shared"),
        ])
    pi = tmp_path / "pi"
    _pi(pi, "tied", "/work/shared/src", "One")
    _pi(pi, "prompted", "", "Alpha project")
    directory = tmp_path / "backups"
    manifest = create_backup(get_session, directory, ["pi_local"], datetime(2026, 10, 30), datetime(2026, 10, 31), pi_root=pi)
    found = {row["ref"]: row for row in evidence(get_session, directory, manifest["backup_id"])["candidates"]}
    assert found["pi_local:tied"]["project_id"] is None and found["pi_local:tied"]["project_provenance"] == "ambiguous"
    assert found["pi_local:prompted"]["project_id"] is None and found["pi_local:prompted"]["project_provenance"] == "unknown"
    for ref in ("pi_local:tied", "pi_local:prompted"):
        try:
            _apply_project(get_session, directory, manifest, ref, found[ref]["fingerprint"])
        except ProjectionStoreError as exc:
            assert exc.code == "unmatched"
        else:
            raise AssertionError(ref)
    from sqlalchemy import inspect as sa_inspect
    with get_session() as db:
        if sa_inspect(db.get_bind()).has_table("projection_corrections"):
            assert db.query(ProjectionCorrection).count() == 0
