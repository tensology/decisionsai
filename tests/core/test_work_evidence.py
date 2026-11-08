"""Redacted work-evidence read and the separate evidence credential."""
from __future__ import annotations

import contextlib
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

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat, ChatTurnEvent
from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
from distr.core.db.projects import Project
from distr.core.db.workflow import DevelopmentTimeEntry, DevelopmentWorkItem
from distr.core.reports.work_evidence import read_work_evidence
from distr.gui.web.security import (
    EVIDENCE_AUTH_HEADER,
    EVIDENCE_GET_PATH,
    LEDGER_AUTH_HEADER,
    PROJECTION_AUTH_HEADER,
    evidence_header_authorizes_read,
    ledger_header_authorizes_write,
    projection_header_authorizes_write,
)


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'evidence.db'}")
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


def test_ids_are_explicit_and_secrets_are_absent(tmp_path):
    get_session = _db(tmp_path)
    with get_session() as db:
        project = Project(name="Alpha", folder_location=str(tmp_path / "alpha"))
        db.add(project)
        db.flush()
        chat = Chat(title="Thread", project_id=None, params="{}")
        db.add(chat)
        db.flush()
        item = DevelopmentWorkItem(chat_id=chat.id, identity_key="t", project_id=project.id, ticket_title="SECRET_TICKET_LABEL")
        db.add(item)
        db.flush()
        session = ProjectExecutionSession(
            project_id=project.id, started_at=datetime(2026, 10, 30, 8, 0),
            completed_at=datetime(2026, 10, 30, 8, 5), updated_at=datetime(2026, 10, 30, 8, 5),
            status="completed", route_backend="pi", origin="run",
            input_packet="SECRET_PROMPT", output_packet="SECRET_OUTPUT",
        )
        db.add(session)
        db.flush()
        db.add(ProjectExecutionEvent(
            session_id=session.id, event_type="cli_completed", status="completed",
            message="SECRET_PROMPT", payload='{"transcript":"SECRET_OUTPUT"}',
            created_at=datetime(2026, 10, 30, 8, 5),
        ))
        entry = DevelopmentTimeEntry(
            work_item_id=item.id, chat_id=chat.id,
            started_at=datetime(2026, 10, 30, 9, 0), ended_at=datetime(2026, 10, 30, 10, 0),
            seconds=3600, source="play",
        )
        db.add(entry)
        db.flush()
        db.add(ChatTurnEvent(
            chat_id=chat.id, turn_id=chat.id, sequence=1, event_id="evt-1",
            event_type="tool_completed", status="completed", title="Tool",
            summary="SECRET_PROMPT", detail="SECRET_OUTPUT",
            metadata_json=json.dumps({"tool_name": "read_file", "argument": "SECRET_PROMPT"}),
            created_date=datetime(2026, 10, 30, 9, 30),
        ))
        bare = DevelopmentTimeEntry(
            work_item_id=item.id, chat_id=chat.id,
            started_at=datetime(2026, 11, 1, 9, 0), ended_at=datetime(2026, 11, 1, 10, 0),
            seconds=3600, source="play",
        )
        db.add(bare)
        db.flush()
        session_id, entry_id, bare_id, project_id = int(session.id), int(entry.id), int(bare.id), int(project.id)
    from distr.core.reports.projection_store import ProjectionStoreError
    try:
        read_work_evidence(get_session, start=__import__("datetime").date(2026, 10, 30), end=__import__("datetime").date(2026, 10, 30), ids=["../secret"])
    except ProjectionStoreError as exc:
        assert exc.status == 400
    else:
        raise AssertionError("a path id was accepted")
    result = read_work_evidence(
        get_session,
        start=__import__("datetime").date(2026, 10, 30),
        end=__import__("datetime").date(2026, 10, 30),
        ids=[f"execution:{session_id}", f"time:{entry_id}"],
    )
    text = json.dumps(result)
    assert "SECRET_PROMPT" not in text and "SECRET_OUTPUT" not in text and "SECRET_TICKET_LABEL" not in text
    execution = next(row for row in result["items"] if row["ref"] == f"execution:{session_id}")
    assert execution["outcome"]["event_type"] == "cli_completed"
    assert execution["project_id"] == project_id and execution["project_provenance"] == "stored_on_row"
    timer = next(row for row in result["items"] if row["ref"] == f"time:{entry_id}")
    assert timer["outcome"]["tool_name"] == "read_file"
    assert timer["project_provenance"] == "work_item"
    outside = read_work_evidence(
        get_session,
        start=__import__("datetime").date(2026, 10, 30),
        end=__import__("datetime").date(2026, 10, 30),
        ids=[f"time:{bare_id}"],
    )
    assert outside["items"][0]["unknown"] is True and outside["items"][0]["outcome"] is None


def test_evidence_token_does_not_widen_other_scopes(tmp_path, monkeypatch):
    evidence = tmp_path / "evidence"
    projection = tmp_path / "projection"
    ledger = tmp_path / "ledger"
    internal = tmp_path / "internal"
    internal.write_text("internal-test-credential-value-0123456789\n", encoding="utf-8")
    internal.chmod(0o600)
    monkeypatch.setenv("DECISIONSAI_INTERNAL_TOKEN_FILE", str(internal))
    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", "internal-test-credential-value-0123456789")
    monkeypatch.setenv("DECISIONSAI_LEDGER_TOKEN_FILE", str(ledger))
    monkeypatch.setenv("DECISIONSAI_PROJECTION_TOKEN_FILE", str(projection))
    monkeypatch.setenv("DECISIONSAI_EVIDENCE_TOKEN_FILE", str(evidence))
    from distr.gui.web.security import provision_evidence_operator_token, provision_ledger_operator_token, provision_projection_operator_token
    provision_ledger_operator_token(confirm="provision-time-ledger")
    provision_projection_operator_token(confirm="provision-projection-store")
    provision_evidence_operator_token(confirm="provision-evidence-reader")
    secret = evidence.read_text(encoding="utf-8").strip()
    ledger_secret = ledger.read_text(encoding="utf-8").strip()
    projection_secret = projection.read_text(encoding="utf-8").strip()
    assert evidence_header_authorizes_read(EVIDENCE_GET_PATH, "GET", secret)
    assert not evidence_header_authorizes_read(EVIDENCE_GET_PATH, "POST", secret)
    assert not evidence_header_authorizes_read(EVIDENCE_GET_PATH + "-evil", "GET", secret)
    assert not evidence_header_authorizes_read("/api/workflows/studio/time-ledger/backups", "POST", secret)
    assert not ledger_header_authorizes_write(EVIDENCE_GET_PATH, "GET", ledger_secret)
    assert not projection_header_authorizes_write(EVIDENCE_GET_PATH, "GET", projection_secret)
    app = FastAPI()

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if request.url.path == EVIDENCE_GET_PATH or request.url.path.startswith(EVIDENCE_GET_PATH + "-"):
            if not evidence_header_authorizes_read(request.url.path, request.method, request.headers.get(EVIDENCE_AUTH_HEADER, "")):
                if request.headers.get("X-DecisionsAI-Internal-Token") != "internal-test-credential-value-0123456789":
                    return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get(EVIDENCE_GET_PATH)
    def read():
        return {"ok": True}

    client = TestClient(app)
    assert client.get(EVIDENCE_GET_PATH).status_code == 401
    assert client.get(EVIDENCE_GET_PATH, headers={LEDGER_AUTH_HEADER: ledger_secret}).status_code == 401
    assert client.get(EVIDENCE_GET_PATH, headers={PROJECTION_AUTH_HEADER: projection_secret}).status_code == 401
    ok = client.get(EVIDENCE_GET_PATH, headers={EVIDENCE_AUTH_HEADER: secret})
    assert ok.status_code == 200 and secret not in ok.text
    monkeypatch.setenv("DECISIONSAI_EVIDENCE_TOKEN_FILE", str(internal))
    try:
        provision_evidence_operator_token(confirm="provision-evidence-reader")
    except Exception as exc:
        assert getattr(exc, "code", None) == 3
    else:
        raise AssertionError("evidence credential aliased the internal token")
