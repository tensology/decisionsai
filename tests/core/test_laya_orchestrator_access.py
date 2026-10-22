"""Orchestrator tools + Jupiter CLI for Laya submissions (no real agent turn, no live HTTP)."""

from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

from distr.core import laya_submissions as svc
from distr.core.agent.tools.integrations.laya_submit import LayaSubmissionStatusTool, LayaSubmitTool
from tests.core.test_laya_submissions import db, events, no_laya  # noqa: F401  (fixtures)

REPO = Path(__file__).resolve().parents[2]


def test_routed_event_carries_thread_title(db, events):  # noqa: F811
    sub = svc.submit("Merrypak: draft the invoice email", session_factory=db, dispatch=lambda s: None,
                     assess=no_laya, watch=False)
    routed = dict(events)[svc.EVENT_ROUTED]
    assert routed["thread_created"] is True
    assert routed["thread_id"] == sub.thread_id
    assert routed["thread_title"] == "Merrypak: draft the invoice email"


def test_laya_submit_tool_calls_service_in_process(monkeypatch):
    calls = {}

    def fake_submit(text, **kw):
        calls.update(text=text, **kw)
        return svc.Submission(id="abc", text=text, status="dispatched", project_id=2, project_name="Merrypak",
                              thread_id=77, thread_created=True)

    monkeypatch.setattr(svc, "submit", fake_submit)
    out = json.loads(LayaSubmitTool()._run(text="invoice", project="Merrypak", thread_id=None, new_thread=True))
    assert out["ok"] is True and out["submission"]["thread_id"] == 77
    assert calls == {"text": "invoice", "source": "orchestrator", "project": "Merrypak", "thread_id": None, "new_thread": True}


def test_laya_submit_tool_reports_errors(monkeypatch):
    monkeypatch.setattr(svc, "submit", lambda text, **kw: (_ for _ in ()).throw(ValueError("text is required")))
    out = json.loads(LayaSubmitTool()._run(text=""))
    assert out == {"ok": False, "error": "text is required"}


def test_laya_submission_status_tool(monkeypatch):
    sub = svc.Submission(id="s1", text="t", status="done", thread_id=5)
    monkeypatch.setattr(svc, "get_submission", lambda sid: sub if sid == "s1" else None)
    tool = LayaSubmissionStatusTool()
    assert json.loads(tool._run(submission_id="s1"))["submission"]["status"] == "done"
    assert json.loads(tool._run(submission_id="nope"))["ok"] is False


def test_tools_registered_in_loader():
    from distr.core.agent.services.llm import tool_execution_policy as pol
    from distr.core.agent.tools import loader

    names = [n for n, _ in loader._get_tool_definitions()]
    assert "LayaSubmitTool" in names and "LayaSubmissionStatusTool" in names
    src = Path(loader.__file__).read_text()
    assert '"LayaSubmitTool":           ("integrations.laya_submit", "LayaSubmitTool")' in src
    assert "laya_submission_status" in pol._READ_ONLY_TOOLS
    assert "laya_submit" not in pol._READ_ONLY_TOOLS


# ---------------------------------------------------------------- CLI

def _cli():
    spec = importlib.util.spec_from_file_location("laya_submit_cli", REPO / "scripts" / "laya_submit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Resp(io.BytesIO):
    def __init__(self, payload, status=202):
        super().__init__(json.dumps(payload).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_cli_submit_posts_json(capsys):
    cli, seen = _cli(), {}

    def opener(req, timeout):
        seen.update(url=req.full_url, method=req.get_method(), body=json.loads(req.data), headers=dict(req.header_items()))
        return _Resp({"id": "x1", "status": "dispatched", "thread_id": 9})

    rc = cli.main(["--text", "hi", "--project", "DecisionsAI", "--thread-id", "9", "--new-thread"],
                  opener=opener, headers_fn=lambda: {"Content-Type": "application/json", "X-DecisionsAI-Internal-Token": "SECRETTOKEN123"})
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] and out["result"]["id"] == "x1"
    assert seen["url"] == "http://127.0.0.1:8765/api/laya/submissions" and seen["method"] == "POST"
    assert seen["body"] == {"text": "hi", "source": "jupiter-cli", "new_thread": True, "project": "DecisionsAI", "thread_id": 9}
    assert "SECRETTOKEN123" not in json.dumps(out)  # token never echoed


def test_cli_status_and_http_error(capsys):
    cli = _cli()

    def opener(req, timeout):
        assert req.get_method() == "GET" and req.full_url.endswith("/api/laya/submissions/abc")
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b'{"detail":"Submission not found"}'))

    rc = cli.main(["--status", "abc"], opener=opener)
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["http_status"] == 404 and out["result"]["detail"] == "Submission not found"


def test_cli_requires_text(capsys):
    assert _cli().main([], opener=lambda *a, **k: pytest.fail("no HTTP")) == 2
