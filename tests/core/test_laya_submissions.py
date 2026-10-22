"""Unit tests for the Laya submission API (LLM, Laya MCP and agent runtime mocked)."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core import laya_submissions as svc
from distr.core.db import Base, Chat, Settings
from distr.core.db.projects import Project


@pytest.fixture()
def db():
    from distr.core.db.workflow import DevelopmentWorkItem

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    tables = [Base.metadata.tables[n] for n in ("chats", "projects", "settings", DevelopmentWorkItem.__tablename__)]
    Base.metadata.create_all(engine, tables=tables)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def factory():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    with factory() as s:
        s.add_all([
            Project(id=1, name="DecisionsAI", folder_location="/Users/paul/development/TENSOLOGY/DECISIONS/DecisionsAI",
                    additional_trigger_words=json.dumps(["orchestrator", "kanban"])),
            Project(id=2, name="Merrypak", folder_location="/Users/paul/dev/merrypak", additional_trigger_words=json.dumps(["packaging", "invoice"])),
            Project(id=3, name="Spoortrack", folder_location="/Users/paul/dev/spoortrack", additional_trigger_words="[]", in_use=True),
        ])
        s.add(Settings(development_hot_corner_pinned_project_ids="[2]"))
        s.commit()
    yield factory


@pytest.fixture()
def events(monkeypatch):
    seen = []
    monkeypatch.setattr(svc, "emit", lambda sub, kind, extra=None: seen.append((kind, svc._event_payload(sub, kind, extra))))
    return seen


def no_laya(_text):
    return {}


def _cands(db):
    with db() as s:
        return svc.load_project_candidates(s)


def test_candidates_include_pinned(db):
    c = {x.name: x for x in _cands(db)}
    assert c["Merrypak"].pinned and c["DecisionsAI"].pinned  # explicit id + default pinned name
    assert c["Spoortrack"].in_use


def test_classify_by_name_keyword_and_path(db):
    cands = _cands(db)
    r = svc.classify("Fix the orchestrator routing in DecisionsAI", cands, assess=no_laya)
    assert r.project_name == "DecisionsAI" and r.confidence > 0.6
    r = svc.classify("New packaging invoice template please", cands, assess=no_laya)
    assert r.project_name == "Merrypak" and "keywords" in r.reason
    r = svc.classify("look at /Users/paul/dev/spoortrack/app.py", cands, assess=no_laya)
    assert r.project_name == "Spoortrack" and "path" in r.reason


def test_classify_hint_and_fallback(db):
    cands = _cands(db)
    assert svc.classify("anything", cands, project_hint="merrypak", assess=no_laya).project_id == 2
    assert svc.classify("anything", cands, project_hint="1", assess=no_laya).confidence == 1.0
    r = svc.classify("what's the weather", cands, assess=no_laya)
    assert r.project_name == "Spoortrack" and r.confidence < 0.5  # active project fallback


def test_classify_records_laya_assessment(db):
    r = svc.classify("Merrypak invoice", _cands(db), assess=lambda t: {"complexity": "low", "confidence": 0.9})
    assert r.laya["complexity"] == "low"


def test_laya_assessment_fail_open(monkeypatch):
    from distr.core import laya_runtime

    monkeypatch.setattr(laya_runtime, "get_mode", lambda: "auto")
    monkeypatch.setattr(laya_runtime, "assess_request", lambda t: (_ for _ in ()).throw(RuntimeError("down")))
    assert svc._laya_assessment("x") == {"error": "RuntimeError"}
    monkeypatch.setattr(laya_runtime, "get_mode", lambda: "off")
    assert svc._laya_assessment("x") == {}


def test_submit_creates_thread_dispatches_and_emits(db, events):
    dispatched = []
    sub = svc.submit("Merrypak: draft the invoice email", source="jupiter", session_factory=db,
                     dispatch=dispatched.append, assess=no_laya, watch=False)
    assert sub.status == "dispatched" and sub.project_id == 2 and sub.thread_created
    assert dispatched and dispatched[0].thread_id == sub.thread_id
    kinds = [k for k, _ in events]
    assert kinds == [svc.EVENT_ACCEPTED, svc.EVENT_ROUTED, svc.EVENT_PROGRESS]
    routed = events[1][1]
    assert routed["project_name"] == "Merrypak" and routed["thread_id"] == sub.thread_id and "confidence" in routed
    with db() as s:
        assert s.get(Chat, sub.thread_id).project_id == 2
    # second submission reuses the recent project thread
    sub2 = svc.submit("another packaging tweak for merrypak", session_factory=db, dispatch=lambda s: None, assess=no_laya, watch=False)
    assert sub2.thread_id == sub.thread_id and not sub2.thread_created
    assert svc.get_submission(sub.id) is sub


def test_submit_thread_hint_and_new_thread(db, events):
    with db() as s:
        c = Chat(title="existing", project_id=1)
        s.add(c); s.commit(); cid = c.id
    sub = svc.submit("hello", thread_id=cid, session_factory=db, dispatch=lambda s: None, assess=no_laya, watch=False)
    assert sub.thread_id == cid and "caller thread hint" in sub.reason
    sub2 = svc.submit("DecisionsAI kanban", new_thread=True, session_factory=db, dispatch=lambda s: None, assess=no_laya, watch=False)
    assert sub2.thread_created and sub2.thread_id != cid


def test_submit_failures(db, events):
    with pytest.raises(ValueError):
        svc.submit("  ", session_factory=db)
    sub = svc.submit("x", thread_id=9999, session_factory=db, dispatch=lambda s: None, assess=no_laya, watch=False)
    assert sub.status == "failed" and "not found" in sub.error
    assert events[-1][0] == svc.EVENT_FAILED

    def boom(_s):
        raise RuntimeError("agent not available")
    sub = svc.submit("merrypak", session_factory=db, dispatch=boom, assess=no_laya, watch=False)
    assert sub.status == "failed" and events[-1][0] == svc.EVENT_FAILED


def test_watch_marks_done_when_agent_responds(db, events):
    def dispatch(sub):
        with db() as s:
            s.add(Chat(parent_id=sub.thread_id, input=sub.text, response="All done."))
            s.commit()
    sub = svc.submit("merrypak invoice", session_factory=db, dispatch=dispatch, assess=no_laya,
                     watch=True, watch_interval_s=0.01, watch_timeout_s=2)
    for _ in range(200):
        if sub.status == "done":
            break
        time.sleep(0.01)
    assert sub.status == "done" and sub.result == "All done."
    assert events[-1][0] == svc.EVENT_DONE


def test_emit_publishes_on_event_bus(monkeypatch):
    from distr.core.events import ORCHESTRATION_EVENT, get_event_bus

    got = []
    handler = lambda et, data: got.append(data)  # noqa: E731
    bus = get_event_bus()
    bus.subscribe(ORCHESTRATION_EVENT, handler)
    try:
        sub = svc.Submission(id="abc", text="t")
        svc.emit(sub, svc.EVENT_ACCEPTED)
    finally:
        bus.unsubscribe(ORCHESTRATION_EVENT, handler)
    assert got and got[0]["kind"] == "laya.submission.accepted" and got[0]["submission_id"] == "abc"


def test_http_routes(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from distr.gui.web.routes.laya_submissions import router

    stored = svc.Submission(id="sid1", text="hello world", status="dispatched", project_id=2, project_name="Merrypak", thread_id=7)
    calls = {}

    def fake_submit(text, **kw):
        calls.update(kw, text=text)
        svc._store(stored)
        return stored
    monkeypatch.setattr(svc, "submit", fake_submit)
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    r = client.post("/api/laya/submissions", json={"text": "hello world", "source": "jupiter", "project": "Merrypak"})
    assert r.status_code == 202 and r.json()["id"] == "sid1" and "text" not in r.json()
    assert calls["project"] == "Merrypak" and calls["source"] == "jupiter"
    assert client.get("/api/laya/submissions/sid1").json()["thread_id"] == 7
    assert client.get("/api/laya/submissions/nope").status_code == 404
    assert client.post("/api/laya/submissions", json={"text": ""}).status_code == 422
