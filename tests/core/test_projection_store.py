"""Projection backups and pad corrections. Canonical rows and JSONL files stay put on failure."""
from __future__ import annotations

import contextlib
import json
import pytest
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

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.automation import Automation, AutomationRun
from distr.core.db.kanban import ProjectExecutionSession
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
from distr.core.reports import month
from distr.core.reports.projection_store import (
    ProjectionStoreError,
    apply_active_to_local_blocks,
    apply_correction,
    assign_project_manual,
    attach_reporting_corrections,
    create_backup,
    evidence,
    manual_project_map,
    restore_backup,
    revoke_correction,
)
from distr.gui.web.security import (
    LEDGER_AUTH_HEADER,
    PROJECTION_AUTH_HEADER,
    ledger_header_authorizes_write,
    projection_header_authorizes_write,
)


def _db(tmp_path):
    for name in ("distr.core.db.workflow", "distr.core.db.automation", "distr.core.db.kanban", "distr.core.db.work_reports"):
        try:
            __import__(name)
        except ModuleNotFoundError:
            pass
    engine = create_engine(f"sqlite:///{tmp_path / 'proj.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def get_session():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return get_session


def _seed(get_session, tmp_path):
    with get_session() as db:
        workflow = AutoWorkflow(name="Workflow")
        automation = Automation(name="Nightly")
        db.add_all([workflow, automation])
        db.flush()
        execution = ProjectExecutionSession(
            project_id=7, started_at=datetime(2026, 10, 30, 8, 0),
            completed_at=datetime(2026, 10, 30, 8, 10), updated_at=datetime(2026, 10, 30, 8, 10),
            status="completed", route_backend="pi", origin="run",
        )
        auto = AutomationRun(
            automation_id=automation.id, status="completed",
            started_at=datetime(2026, 10, 30, 9, 0), completed_at=datetime(2026, 10, 30, 9, 5),
        )
        run = AutoWorkflowRun(
            workflow_id=workflow.id, status="completed",
            started_at=datetime(2026, 10, 30, 10, 0), completed_at=datetime(2026, 10, 30, 10, 20),
        )
        db.add_all([execution, auto, run])
        db.flush()
        ids = {"execution": int(execution.id), "automation": int(auto.id), "workflow": int(run.id)}
    pi = tmp_path / "pi"
    pi.mkdir()
    session = pi / "solo.jsonl"
    session.write_text(json.dumps({"id": "solo", "cwd": "", "timestamp": "2026-10-30T12:00:00Z"}) + "\n", encoding="utf-8")
    return ids, pi, session.read_bytes()


def test_backup_restore_and_failed_apply_touch_nothing(tmp_path):
    get_session = _db(tmp_path)
    ids, pi, original = _seed(get_session, tmp_path)
    directory = tmp_path / "backups"
    lo, hi = datetime(2026, 10, 30, 0, 0), datetime(2026, 10, 31, 0, 0)
    manifest = create_backup(
        get_session, directory, ["execution", "automation", "workflow", "pi_local"], lo, hi, pi_root=pi,
    )
    assert manifest["counts"]["execution"] == 1
    assert manifest["counts"]["automation"] == 1
    assert manifest["counts"]["workflow"] == 1
    assert manifest["counts"]["pi_local"] == 1
    assert "input_packet" not in json.dumps(manifest)
    found = evidence(get_session, directory, manifest["backup_id"])
    pad = next(row for row in found["candidates"] if row["source"] == "pi_local")
    assert pad["eligible"] is True and pad["outcome"] is None and pad["project_provenance"] == "unknown"
    with get_session() as db:
        db.query(ProjectExecutionSession).delete()
        db.query(AutomationRun).delete()
        db.query(AutoWorkflowRun).delete()
    restored = restore_backup(get_session, directory, {
        "backup_id": manifest["backup_id"],
        "refs": [f"execution:{ids['execution']}", f"automation:{ids['automation']}", f"workflow:{ids['workflow']}", "pi_local:solo"],
        "confirm": "restore-projection-backup",
    })
    assert set(restored["restored"]) == {f"execution:{ids['execution']}", f"automation:{ids['automation']}", f"workflow:{ids['workflow']}"}
    assert restored["already_present"] == ["pi_local:solo"]
    assert (pi / "solo.jsonl").read_bytes() == original
    with pytest_raises_store():
        apply_correction(get_session, directory, {
            "backup_id": manifest["backup_id"], "source": "pi_local", "ref": "pi_local:solo",
            "fingerprint": "0" * 64, "reason": "remove_synthetic_pad",
            "provenance": "collator_min_block_seconds", "confirm": "apply-projection-correction",
        })
    with get_session() as db:
        assert db.get(ProjectExecutionSession, ids["execution"]) is not None
    assert (pi / "solo.jsonl").read_bytes() == original


def pytest_raises_store():
    import pytest
    return pytest.raises(ProjectionStoreError)


def test_pad_correction_is_reporting_only(tmp_path):
    get_session = _db(tmp_path)
    _ids, pi, original = _seed(get_session, tmp_path)
    directory = tmp_path / "backups"
    lo, hi = datetime(2026, 10, 30, 0, 0), datetime(2026, 10, 31, 0, 0)
    manifest = create_backup(get_session, directory, ["pi_local"], lo, hi, pi_root=pi)
    pad = next(row for row in evidence(get_session, directory, manifest["backup_id"])["candidates"] if row["eligible"])
    applied = apply_correction(get_session, directory, {
        "backup_id": manifest["backup_id"], "source": "pi_local", "ref": pad["ref"],
        "fingerprint": pad["fingerprint"], "reason": "remove_synthetic_pad",
        "provenance": "collator_min_block_seconds", "confirm": "apply-projection-correction",
    })
    assert applied["duration_unknown"] is True
    again = apply_correction(get_session, directory, {
        "backup_id": manifest["backup_id"], "source": "pi_local", "ref": pad["ref"],
        "fingerprint": pad["fingerprint"], "reason": "remove_synthetic_pad",
        "provenance": "collator_min_block_seconds", "confirm": "apply-projection-correction",
    })
    assert again["idempotent"] is True and again["correction_id"] == applied["correction_id"]
    attach_reporting_corrections(get_session)
    blocks, _counts, _status = month.collect_local_sessions(lo, hi, type("M", (), {"match": lambda self, cwd: None})(), pi_root=pi)
    assert blocks == []
    assert (pi / "solo.jsonl").read_bytes() == original
    month.collect_local_sessions = month.collect_local_sessions._projection_original


def test_projection_token_does_not_widen_ledger_posts(tmp_path, monkeypatch):
    projection = tmp_path / "projection"
    ledger = tmp_path / "ledger"
    internal = tmp_path / "internal"
    internal.write_text("internal-test-credential-value-0123456789\n", encoding="utf-8")
    internal.chmod(0o600)
    monkeypatch.setenv("DECISIONSAI_INTERNAL_TOKEN_FILE", str(internal))
    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", "internal-test-credential-value-0123456789")
    monkeypatch.setenv("DECISIONSAI_LEDGER_TOKEN_FILE", str(ledger))
    monkeypatch.setenv("DECISIONSAI_PROJECTION_TOKEN_FILE", str(projection))
    from distr.gui.web.security import provision_ledger_operator_token, provision_projection_operator_token
    provision_ledger_operator_token(confirm="provision-time-ledger")
    provision_projection_operator_token(confirm="provision-projection-store")
    projection_secret = projection.read_text(encoding="utf-8").strip()
    ledger_secret = ledger.read_text(encoding="utf-8").strip()
    assert not projection_header_authorizes_write("/api/workflows/studio/time-ledger/backups", "POST", projection_secret)
    assert not ledger_header_authorizes_write("/api/workflows/studio/projection-store/apply", "POST", ledger_secret)
    assert not projection_header_authorizes_write("/api/workflows/studio/projection-store/apply-evil", "POST", projection_secret)
    assert not projection_header_authorizes_write("/api/workflows/studio/projection-store/backups", "DELETE", projection_secret)
    assert projection_header_authorizes_write("/api/workflows/studio/projection-store/backups", "POST", projection_secret)
    app = FastAPI()

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method != "GET":
            ledger_ok = ledger_header_authorizes_write(request.url.path, request.method, request.headers.get(LEDGER_AUTH_HEADER, ""))
            projection_ok = projection_header_authorizes_write(request.url.path, request.method, request.headers.get(PROJECTION_AUTH_HEADER, ""))
            if not ledger_ok and not projection_ok:
                if request.headers.get("X-DecisionsAI-Internal-Token") != "internal-test-credential-value-0123456789":
                    return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.post("/api/workflows/studio/projection-store/backups")
    def backups():
        return {"ok": True}

    @app.post("/api/workflows/studio/time-ledger/backups")
    def ledger_backups():
        return {"ok": True}

    client = TestClient(app)
    assert client.post("/api/workflows/studio/projection-store/backups").status_code == 401
    assert client.post("/api/workflows/studio/projection-store/backups", headers={PROJECTION_AUTH_HEADER: "nope"}).status_code == 401
    assert client.post("/api/workflows/studio/projection-store/backups", headers={LEDGER_AUTH_HEADER: ledger_secret}).status_code == 401
    assert client.post("/api/workflows/studio/time-ledger/backups", headers={PROJECTION_AUTH_HEADER: projection_secret}).status_code == 401
    assert client.post("/api/workflows/studio/projection-store/backups", headers={PROJECTION_AUTH_HEADER: projection_secret}).status_code == 200
    body = client.post("/api/workflows/studio/projection-store/backups", headers={PROJECTION_AUTH_HEADER: projection_secret})
    assert body.status_code == 200
    assert projection_secret not in body.text
    monkeypatch.setenv("DECISIONSAI_PROJECTION_TOKEN_FILE", str(internal))
    try:
        provision_projection_operator_token(confirm="provision-projection-store")
    except Exception as exc:
        assert getattr(exc, "code", None) == 3
    else:
        raise AssertionError("projection credential was allowed to alias the internal token")


def _mac_month():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "fixtures" / "mac_month_a3deb95e.py"
    digest = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    assert digest == "a3deb95ef9bcbad0a9928cce93ba9cc9207ac5c853f110f17f5b2190ed02ac8d"
    spec = importlib.util.spec_from_file_location("mac_month_a3deb95e", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["mac_month_a3deb95e"] = module
    spec.loader.exec_module(module)
    module.latest_report = lambda *args, **kwargs: None
    module.default_model = lambda *args, **kwargs: {}
    module.period_options = lambda *args, **kwargs: []
    module.attach_report_extras = lambda snapshot, project_ids: snapshot
    return module


def _write_pi(root: Path, name: str, lines: list[dict]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


def _report(mac, get_session, pi: Path):
    period = mac.resolve_period(start="2026-10-30", end="2026-10-31")
    return mac.get_period_report(period, None, get_session=get_session, pi_root=pi, include_local=True, include_automations=False)


def test_normal_period_report_duration_changes_and_revoke_restores(tmp_path):
    mac = _mac_month()
    get_session = _db(tmp_path)
    with get_session() as db:
        row = ProjectExecutionSession(
            project_id=7, started_at=datetime(2026, 10, 30, 8, 0),
            completed_at=datetime(2026, 10, 30, 8, 5), updated_at=datetime(2026, 10, 30, 8, 5),
            status="completed", route_backend="pi", origin="run",
        )
        db.add(row)
        db.flush()
        execution_id = int(row.id)
    pi = tmp_path / "pi"
    short = _write_pi(pi, "short", [
        {"id": "short", "cwd": "", "timestamp": "2026-10-30T10:00:00Z"},
        {"timestamp": "2026-10-30T10:00:10Z"},
    ])
    _write_pi(pi, "natural", [
        {"id": "natural", "cwd": "", "timestamp": "2026-10-30T11:00:00Z"},
        {"timestamp": "2026-10-30T11:01:30Z"},
    ])
    _write_pi(pi, "cross", [
        {"id": "cross", "cwd": "", "timestamp": "2026-10-30T21:55:00Z"},
        {"timestamp": "2026-10-30T22:05:00Z"},
    ])
    originals = {path.name: path.read_bytes() for path in pi.glob("*.jsonl")}
    directory = tmp_path / "backups"
    lo, hi = datetime(2026, 10, 30, 0, 0), datetime(2026, 11, 1, 0, 0)
    manifest = create_backup(get_session, directory, ["pi_local", "execution"], lo, hi, pi_root=pi)
    attach_reporting_corrections(get_session, month_module=mac)
    before = _report(mac, get_session, pi)
    by_ref = {block["ref"]: block for block in before["blocks"]}
    assert by_ref["pi_local:short"]["seconds"] == 60
    assert by_ref["pi_local:natural"]["seconds"] == 90
    assert by_ref["pi_local:cross"]["seconds"] == 600
    assert by_ref["pi_local:short"]["project_id"] == 0
    day_before = {row["date"]: row["seconds"] for row in before["by_day"]}
    assert set(day_before) >= {"2026-10-30", "2026-10-31"}
    found = evidence(get_session, directory, manifest["backup_id"])
    short_pad = next(row for row in found["candidates"] if row["ref"] == "pi_local:short" and row["eligible"])
    natural = next(row for row in found["candidates"] if row["ref"] == "pi_local:natural")
    assert natural["eligible"] is False and natural["outcome"] is None and natural["project_provenance"] == "unknown"
    applied = apply_correction(get_session, directory, {
        "backup_id": manifest["backup_id"], "source": "pi_local", "ref": "pi_local:short",
        "fingerprint": short_pad["fingerprint"], "reason": "remove_synthetic_pad",
        "provenance": "collator_min_block_seconds", "confirm": "apply-projection-correction",
    })
    try:
        apply_correction(get_session, directory, {
            "backup_id": manifest["backup_id"], "source": "pi_local", "ref": "pi_local:natural",
            "fingerprint": natural["fingerprint"], "reason": "remove_synthetic_pad",
            "provenance": "collator_min_block_seconds", "confirm": "apply-projection-correction",
        })
    except ProjectionStoreError as exc:
        assert exc.code == "fingerprint_mismatch"
    else:
        raise AssertionError("a real 90 second interval was treated as a pad")
    after = _report(mac, get_session, pi)
    after_ref = {block["ref"]: block for block in after["blocks"]}
    assert after_ref["pi_local:short"]["seconds"] == 10
    assert after_ref["pi_local:short"]["ref"] == "pi_local:short"
    assert after_ref["pi_local:natural"]["seconds"] == 90
    assert after_ref["pi_local:cross"]["seconds"] == 600
    day_after = {row["date"]: row["seconds"] for row in after["by_day"]}
    assert day_before["2026-10-30"] - day_after["2026-10-30"] == 50
    assert day_before["2026-10-31"] == day_after["2026-10-31"]
    with get_session() as db:
        kept = db.get(ProjectExecutionSession, execution_id)
        assert kept.status == "completed"
        assert kept.completed_at == datetime(2026, 10, 30, 8, 5)
    from distr.core.reports.projection_store import revoke_correction
    revoke_correction(get_session, {
        "correction_id": applied["correction_id"], "fingerprint": short_pad["fingerprint"],
        "backup_id": manifest["backup_id"], "confirm": "revoke-projection-correction",
    })
    restored = _report(mac, get_session, pi)
    restored_ref = {block["ref"]: block for block in restored["blocks"]}
    assert restored_ref["pi_local:short"]["seconds"] == 60
    assert {path.name: path.read_bytes() for path in pi.glob("*.jsonl")} == originals
    assert short.read_bytes() == originals["short.jsonl"]
    mac.collect_local_sessions = mac.collect_local_sessions._projection_original


def test_stale_provider_bytes_do_not_shorten_the_period_report(tmp_path):
    import base64
    mac = _mac_month()
    get_session = _db(tmp_path)
    pi = tmp_path / "pi"
    path = _write_pi(pi, "solo", [
        {"id": "solo", "cwd": "", "timestamp": "2026-10-30T12:00:00Z"},
        {"timestamp": "2026-10-30T12:00:30Z"},
    ])
    directory = tmp_path / "backups"
    lo, hi = datetime(2026, 10, 30), datetime(2026, 10, 31)
    manifest = create_backup(get_session, directory, ["pi_local"], lo, hi, pi_root=pi)
    private = json.loads((directory / f"{manifest['backup_id']}.json").read_text(encoding="utf-8"))
    image = next(row for row in private["images"] if row["source"] == "pi_local")
    assert image["content_sha256"] and image["byte_length"] == path.stat().st_size
    assert base64.b64decode(image["content_base64"]) == path.read_bytes()
    assert "content_base64" not in json.dumps(manifest)
    path.write_bytes(path.read_bytes() + (json.dumps({"timestamp": "2026-10-30T12:00:50Z"}) + "\n").encode())
    pad = next(row for row in evidence(get_session, directory, manifest["backup_id"])["candidates"] if row["eligible"])
    result = apply_correction(get_session, directory, {
        "backup_id": manifest["backup_id"], "source": "pi_local", "ref": pad["ref"],
        "fingerprint": pad["fingerprint"], "reason": "remove_synthetic_pad",
        "provenance": "collator_min_block_seconds", "confirm": "apply-projection-correction",
    })
    assert result["applied"] is False and result["reason"] == "source_changed"
    attach_reporting_corrections(get_session, month_module=mac)
    period = mac.resolve_period(start="2026-10-30", end="2026-10-30")
    mac.collect_database_blocks = lambda *args, **kwargs: ([], {})
    report = mac.collate(period, get_session=get_session, pi_root=pi, codex_root=tmp_path / "none")
    assert report["totals"]["activity_seconds"] >= 50
    mac.collect_local_sessions = mac.collect_local_sessions._projection_original


def test_backup_contains_exact_bytes_and_correction_rows_and_reads_do_not_create_the_table(tmp_path):
    import base64
    from sqlalchemy import inspect as sa_inspect
    get_session = _db(tmp_path)
    _ids, pi, original = _seed(get_session, tmp_path)
    secret = "SECRET_PROMPT_DO_NOT_LEAK"
    path = pi / "solo.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + json.dumps({"role": "user", "content": secret, "timestamp": "2026-10-30T12:00:00Z"}) + "\n", encoding="utf-8")
    directory = tmp_path / "backups"
    lo, hi = datetime(2026, 10, 30), datetime(2026, 10, 31)
    with get_session() as db:
        assert not sa_inspect(db.get_bind()).has_table("projection_corrections")
    apply_active_to_local_blocks([], get_session)
    with get_session() as db:
        assert not sa_inspect(db.get_bind()).has_table("projection_corrections")
    first = create_backup(get_session, directory, ["pi_local"], lo, hi, pi_root=pi)
    assert secret not in json.dumps(first)
    assert secret not in json.dumps(evidence(get_session, directory, first["backup_id"]))
    private = json.loads((directory / f"{first['backup_id']}.json").read_text(encoding="utf-8"))
    image = next(row for row in private["images"] if row["ref"] == "pi_local:solo")
    assert secret.encode() in base64.b64decode(image["content_base64"])
    pad = next(row for row in evidence(get_session, directory, first["backup_id"])["candidates"] if row["eligible"])
    apply_correction(get_session, directory, {
        "backup_id": first["backup_id"], "source": "pi_local", "ref": pad["ref"],
        "fingerprint": pad["fingerprint"], "reason": "remove_synthetic_pad",
        "provenance": "collator_min_block_seconds", "confirm": "apply-projection-correction",
    })
    second = create_backup(get_session, directory, ["pi_local"], lo, hi, pi_root=pi)
    stored = json.loads((directory / f"{second['backup_id']}.json").read_text(encoding="utf-8"))
    assert any(row["table"] == "projection_corrections" for row in stored["images"])
    path.unlink()
    restored = restore_backup(get_session, directory, {
        "backup_id": first["backup_id"], "refs": ["pi_local:solo"], "confirm": "restore-projection-backup",
    })
    assert "pi_local:solo" not in restored["already_present"]
    assert restored["absent"] == ["pi_local:solo"]
    assert restored["provider_files_written"] == []
    assert not path.exists()
    assert original  # the seed bytes were replaced before unlink; the file stays absent


def test_manual_project_assignment_is_reversible(tmp_path):
    get_session = _db(tmp_path)
    body = {"source": "pi_local", "ref": "pi_local:s1", "project_id": 7, "confirm": "assign-project-manual"}
    first = assign_project_manual(get_session, dict(body))
    assert first["applied"] and first["updated"] is False and first["project_id"] == 7
    assert manual_project_map(get_session) == {("pi_local", "pi_local:s1"): 7}

    # Choosing another project updates the single row for that block.
    second = assign_project_manual(get_session, {**body, "project_id": 3})
    assert second["updated"] is True and second["correction_id"] == first["correction_id"]
    assert manual_project_map(get_session) == {("pi_local", "pi_local:s1"): 3}

    # The correction is reversible; revoking leaves no override.
    revoke_correction(get_session, {
        "correction_id": first["correction_id"], "fingerprint": second["fingerprint"],
        "backup_id": "", "confirm": "revoke-projection-correction",
    })
    assert manual_project_map(get_session) == {}

    with pytest.raises(ProjectionStoreError):
        assign_project_manual(get_session, {**body, "confirm": "wrong"})


def test_manual_project_assignment_scopes_reused_thread_intervals(tmp_path):
    from distr.core.reports.projection_store import manual_interval_project_map
    get_session = _db(tmp_path)
    base = {'source': 'pi_local', 'ref': 'pi_local:reused', 'confirm': 'assign-project-manual',
            'source_start': '2026-10-23T08:00:00Z', 'source_end': '2026-10-23T08:10:00Z', 'project_id': 7}
    first = assign_project_manual(get_session, base)
    second = assign_project_manual(get_session, {**base, 'source_start': '2026-10-23T10:00:00Z',
                                               'source_end': '2026-10-23T10:10:00Z', 'project_id': 3})
    assert first['correction_id'] != second['correction_id']
    choices = manual_interval_project_map(get_session)
    assert len(choices) == 2 and sorted(choices.values()) == [3, 7]
    assert manual_project_map(get_session) == {}  # neither choice affects the whole thread
    with pytest.raises(ProjectionStoreError):
        assign_project_manual(get_session, {**base, 'source_end': None})
