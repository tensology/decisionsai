"""Registered projection routes against the canonical model columns.

The handlers are not replaced. Auth uses the same header checks as the server.
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
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

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.automation import Automation, AutomationRun
from distr.core.db.kanban import ProjectExecutionSession
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
from distr.gui.web.routes.development import projection_corrections as routes
from distr.gui.web.security import (
    EVIDENCE_AUTH_HEADER,
    EVIDENCE_GET_PATH,
    LEDGER_AUTH_HEADER,
    PROJECTION_AUTH_HEADER,
    PROJECTION_GET_PATHS,
    PROJECTION_POST_PATHS,
    evidence_header_authorizes_read,
    get_internal_api_token,
    ledger_header_authorizes_write,
    projection_header_authorizes_read,
    projection_header_authorizes_write,
)

SCHEMA = _ROOT / "tests/fixtures/canonical_schema"
POST_PATHS = [
    "/api/workflows/studio/projection-store/backups",
    "/api/workflows/studio/projection-store/apply",
    "/api/workflows/studio/projection-store/revoke",
    "/api/workflows/studio/projection-store/restore",
]
GET_EVIDENCE = "/api/workflows/studio/projection-store/evidence"
GET_WORK = EVIDENCE_GET_PATH


def _schema_columns(relative: str, class_name: str) -> list[str]:
    tree = ast.parse((SCHEMA / relative).read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        names = []
        for statement in node.body:
            if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
                continue
            target = statement.targets[0]
            value = statement.value
            if isinstance(target, ast.Name) and isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "Column":
                names.append(target.id)
        return names
    raise AssertionError(class_name)


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'canonical.db'}")
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


def _gate(request: Request):
    path, method = request.url.path, request.method
    if path == EVIDENCE_GET_PATH or path.startswith(EVIDENCE_GET_PATH + "-") or path.startswith(EVIDENCE_GET_PATH + "/"):
        if evidence_header_authorizes_read(path, method, request.headers.get(EVIDENCE_AUTH_HEADER, "")):
            return None
        expected = get_internal_api_token()
        received = request.headers.get("X-DecisionsAI-Internal-Token", "").strip()
        if not (expected and received and __import__("secrets").compare_digest(received, expected)):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return None
    if path in PROJECTION_POST_PATHS or path in PROJECTION_GET_PATHS or path.startswith("/api/workflows/studio/projection-store/"):
        write_ok = projection_header_authorizes_write(path, method, request.headers.get(PROJECTION_AUTH_HEADER, ""))
        read_ok = projection_header_authorizes_read(path, method, request.headers.get(PROJECTION_AUTH_HEADER, ""))
        expected = get_internal_api_token()
        received = request.headers.get("X-DecisionsAI-Internal-Token", "").strip()
        internal_ok = bool(expected and received and __import__("secrets").compare_digest(received, expected))
        if not (write_ok or read_ok or internal_ok):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return None


def _app(get_session, backup_dir):
    app = FastAPI()

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        denied = _gate(request)
        if denied is not None:
            return denied
        return await call_next(request)

    router = APIRouter(prefix="/api")
    routes.register_routes(router, None, get_session=get_session, backup_dir=backup_dir)
    app.include_router(router)
    return app


def _tokens(tmp_path, monkeypatch):
    internal = tmp_path / "internal"
    internal.write_text("internal-test-credential-value-0123456789\n", encoding="utf-8")
    internal.chmod(0o600)
    monkeypatch.setenv("DECISIONSAI_INTERNAL_TOKEN_FILE", str(internal))
    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", "internal-test-credential-value-0123456789")
    monkeypatch.setenv("DECISIONSAI_LEDGER_TOKEN_FILE", str(tmp_path / "ledger"))
    monkeypatch.setenv("DECISIONSAI_PROJECTION_TOKEN_FILE", str(tmp_path / "projection"))
    monkeypatch.setenv("DECISIONSAI_EVIDENCE_TOKEN_FILE", str(tmp_path / "evidence"))
    from distr.gui.web.security import (
        provision_evidence_operator_token,
        provision_ledger_operator_token,
        provision_projection_operator_token,
    )
    provision_ledger_operator_token(confirm="provision-time-ledger")
    provision_projection_operator_token(confirm="provision-projection-store")
    provision_evidence_operator_token(confirm="provision-evidence-reader")
    return {
        "ledger": (tmp_path / "ledger").read_text(encoding="utf-8").strip(),
        "projection": (tmp_path / "projection").read_text(encoding="utf-8").strip(),
        "evidence": (tmp_path / "evidence").read_text(encoding="utf-8").strip(),
        "internal": "internal-test-credential-value-0123456789",
    }


def test_schema_files_match_the_attached_canonical_hashes():
    meta = json.loads((SCHEMA / "metadata.json").read_text(encoding="utf-8"))
    for relative, info in meta.items():
        data = (SCHEMA / relative).read_bytes()
        assert hashlib.sha256(data).hexdigest() == info["sha256"]
        assert len(data) == info["size"]


def test_registered_routes_round_trip_canonical_columns_and_reject_other_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    tokens = _tokens(tmp_path, monkeypatch)
    get_session = _db(tmp_path)
    started = datetime(2026, 10, 30, 8, 0, 0)
    ended = datetime(2026, 10, 30, 8, 10, 0)
    with get_session() as db:
        workflow = AutoWorkflow(name="Workflow")
        automation = Automation(name="Nightly", project_id=19)
        db.add_all([workflow, automation])
        db.flush()
        execution = ProjectExecutionSession(
            project_id=19, workflow_id=workflow.id, run_id=None, step_id=None, ticket_id=None, audit_id=4,
            route_type="project_cli", route_backend="pi", selected_model="model-a",
            selection_reason="because", complexity="low", origin="run", status="completed",
            input_packet="SECRET_PACKET", output_packet="SECRET_OUTPUT", error="none",
            started_at=started, updated_at=ended, completed_at=ended,
        )
        auto = AutomationRun(
            automation_id=automation.id, status="completed", run_data="SECRET_RUN",
            started_at=started, completed_at=ended,
        )
        run = AutoWorkflowRun(
            workflow_id=workflow.id, status="completed", run_data="SECRET_FLOW",
            step_results="[]", variable_values="{}", started_at=started, completed_at=ended,
        )
        db.add_all([execution, auto, run])
        db.flush()
        ids = {"execution": int(execution.id), "automation": int(auto.id), "workflow": int(run.id)}
    pi = Path.home() / ".pi" / "agent" / "sessions"
    pi.mkdir(parents=True)
    session_file = pi / "short.jsonl"
    session_file.write_text(
        json.dumps({"id": "short", "cwd": "", "timestamp": "2026-10-30T10:00:00Z", "content": "SECRET_PROMPT"}) + "\n"
        + json.dumps({"timestamp": "2026-10-30T10:00:10Z"}) + "\n",
        encoding="utf-8",
    )
    app = _app(get_session, tmp_path / "backups")
    client = TestClient(app)
    projection = {PROJECTION_AUTH_HEADER: tokens["projection"]}
    body = client.post("/api/workflows/studio/projection-store/backups", json={
        "sources": ["execution", "automation", "workflow", "pi_local"],
        "window_start": "2026-10-30T00:00:00Z", "window_end": "2026-10-31T00:00:00Z",
        "confirm": "backup-projection-store",
    })
    assert body.status_code == 401
    saved = client.post("/api/workflows/studio/projection-store/backups", headers=projection, json={
        "sources": ["execution", "automation", "workflow", "pi_local"],
        "window_start": "2026-10-30T00:00:00Z", "window_end": "2026-10-31T00:00:00Z",
        "confirm": "backup-projection-store",
    })
    assert saved.status_code == 200, saved.text
    assert "SECRET_PACKET" not in saved.text and "SECRET_PROMPT" not in saved.text
    manifest = saved.json()
    private = json.loads((tmp_path / "backups" / f"{manifest['backup_id']}.json").read_text(encoding="utf-8"))
    by_source = {}
    for image in private["images"]:
        by_source.setdefault(image["source"], image)
    expected = {
        "execution": _schema_columns("distr/core/db/kanban.py", "ProjectExecutionSession"),
        "automation": _schema_columns("distr/core/db/automation.py", "AutomationRun"),
        "workflow": _schema_columns("distr/core/db/workflow.py", "AutoWorkflowRun"),
    }
    for source, names in expected.items():
        assert names
        assert set(names) <= set(by_source[source]["fields"])
    assert by_source["execution"]["fields"]["input_packet"] == "SECRET_PACKET"
    assert by_source["execution"]["fields"]["project_id"] == 19
    pi_image = by_source["pi_local"]
    assert "SECRET_PROMPT" not in json.dumps({key: value for key, value in pi_image.items() if key != "content_base64"})
    found = client.get(GET_EVIDENCE, params={"backup_id": manifest["backup_id"]}, headers=projection)
    assert found.status_code == 200
    assert "SECRET_PROMPT" not in found.text and "SECRET_PACKET" not in found.text
    pad = next(row for row in found.json()["candidates"] if row.get("eligible"))
    applied = client.post("/api/workflows/studio/projection-store/apply", headers=projection, json={
        "backup_id": manifest["backup_id"], "source": "pi_local", "ref": pad["ref"],
        "fingerprint": pad["fingerprint"], "reason": "remove_synthetic_pad",
        "provenance": "collator_min_block_seconds", "confirm": "apply-projection-correction",
    })
    assert applied.status_code == 200 and applied.json()["applied"] is True
    import distr.core.reports.month as month
    try:
        blocks, _counts, _status = month.collect_local_sessions(
            datetime(2026, 10, 30), datetime(2026, 10, 31), type("M", (), {"match": lambda self, cwd: None})(), pi_root=pi,
        )
        short = [block for block in blocks if block["ref"] == "pi_local:short"]
        assert short and int((short[0]["end"] - short[0]["start"]).total_seconds()) == 10
        revoked = client.post("/api/workflows/studio/projection-store/revoke", headers=projection, json={
            "correction_id": applied.json()["correction_id"], "fingerprint": pad["fingerprint"],
            "backup_id": manifest["backup_id"], "confirm": "revoke-projection-correction",
        })
        assert revoked.status_code == 200
        blocks, _counts, _status = month.collect_local_sessions(
            datetime(2026, 10, 30), datetime(2026, 10, 31), type("M", (), {"match": lambda self, cwd: None})(), pi_root=pi,
        )
        short = [block for block in blocks if block["ref"] == "pi_local:short"]
        assert int((short[0]["end"] - short[0]["start"]).total_seconds()) == 60
    finally:
        if getattr(month.collect_local_sessions, "_projection_wrapped", False):
            month.collect_local_sessions = month.collect_local_sessions._projection_original
    with get_session() as db:
        db.query(ProjectExecutionSession).delete()
        db.query(AutomationRun).delete()
        db.query(AutoWorkflowRun).delete()
    session_file.unlink()
    restored = client.post("/api/workflows/studio/projection-store/restore", headers=projection, json={
        "backup_id": manifest["backup_id"],
        "refs": [f"execution:{ids['execution']}", f"automation:{ids['automation']}", f"workflow:{ids['workflow']}", "pi_local:short"],
        "confirm": "restore-projection-backup",
    })
    assert restored.status_code == 200, restored.text
    payload = restored.json()
    assert f"execution:{ids['execution']}" in payload["restored"]
    assert "pi_local:short" in payload["absent"] and "pi_local:short" not in payload["already_present"]
    assert payload["provider_files_written"] == []
    assert not session_file.exists()
    with get_session() as db:
        row = db.get(ProjectExecutionSession, ids["execution"])
        assert row.input_packet == "SECRET_PACKET" and row.project_id == 19 and row.status == "completed"
        assert row.completed_at == ended
    evidence_header = {EVIDENCE_AUTH_HEADER: tokens["evidence"]}
    ledger_header = {LEDGER_AUTH_HEADER: tokens["ledger"]}
    assert client.get(GET_WORK, params={"start": "2026-10-30", "end": "2026-10-30", "ids": f"execution:{ids['execution']}"}, headers=ledger_header).status_code == 401
    assert client.get(GET_WORK, params={"start": "2026-10-30", "end": "2026-10-30", "ids": f"execution:{ids['execution']}"}, headers=projection).status_code == 401
    work = client.get(GET_WORK, params={"start": "2026-10-30", "end": "2026-10-30", "ids": f"execution:{ids['execution']}"}, headers=evidence_header)
    assert work.status_code == 200, work.text
    assert "SECRET_PACKET" not in work.text
    assert work.json()["items"][0]["unknown"] is True
    assert client.post(POST_PATHS[0], headers=evidence_header, json={"sources": ["execution"], "window_start": "2026-10-30T00:00:00Z", "window_end": "2026-10-31T00:00:00Z", "confirm": "backup-projection-store"}).status_code == 401
    assert client.post(POST_PATHS[0], headers=ledger_header, json={"sources": ["execution"], "window_start": "2026-10-30T00:00:00Z", "window_end": "2026-10-31T00:00:00Z", "confirm": "backup-projection-store"}).status_code == 401
    for path in POST_PATHS:
        assert projection_header_authorizes_write(path, "POST", tokens["projection"]) is True
        assert projection_header_authorizes_write(path, "DELETE", tokens["projection"]) is False
        assert projection_header_authorizes_write(path, "GET", tokens["projection"]) is False
        assert projection_header_authorizes_write(path + "-evil", "POST", tokens["projection"]) is False
        assert ledger_header_authorizes_write(path, "POST", tokens["ledger"]) is False
        assert client.post(path, headers=ledger_header, json={}).status_code == 401
    assert projection_header_authorizes_read(GET_EVIDENCE, "GET", tokens["projection"]) is True
    assert projection_header_authorizes_read(GET_EVIDENCE, "POST", tokens["projection"]) is False
    assert projection_header_authorizes_read(GET_EVIDENCE + "-evil", "GET", tokens["projection"]) is False
    assert evidence_header_authorizes_read(GET_WORK, "POST", tokens["evidence"]) is False
    assert client.post(POST_PATHS[1], headers={PROJECTION_AUTH_HEADER: "nope"}, json={
        "backup_id": "x", "source": "pi_local", "ref": "pi_local:short", "fingerprint": "0" * 64,
        "reason": "remove_synthetic_pad", "provenance": "collator_min_block_seconds",
        "confirm": "apply-projection-correction",
    }).status_code == 401
    assert tokens["projection"] not in saved.text and tokens["evidence"] not in work.text
