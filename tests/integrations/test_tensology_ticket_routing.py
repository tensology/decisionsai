"""Tensology approved-ticket path: project route only (no local free models), gates on,
decisions_project_id matching, poll back-off, and a local stub-server simulation."""
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import distr.core.tensology_client as tc

E2E_TICKET = {
    "id": 9001,
    "project_id": 77,
    "project_name": "Plan My Charter",
    "reference": "E2E-TEST-001",
    "title": "E2E TEST - do not bill",
    "description": "E2E TEST ticket from a local stub; never executes real work.",
    "tasks": ["E2E TEST"],
    "execution_status": "awaiting_decisions",
    "execution_status_detail": "",
    "decisions_thread_id": "",
    "decisions_project_id": "",
    "repository_ready": True,
    "execution_complexity": "auto",
    "project_domain": "planmycharter.com",
    "source": {"channel": "mailshot", "conversation_id": "thread-44", "message_id": "message-8"},
    "attachments": [{"name": "request.pdf", "url": "/media/request.pdf"}],
    "discussion": [{"author_name": "Client", "content": "Use the attached request.", "attachments": []}],
}
HOSTED = {"id": 12, "name": "Plan My Charter", "folder_location": "/tmp", "start_time_tracker": False,
          "coding_backend": "cursor", "coding_backend_model": "grok-4.7-medium"}


@pytest.mark.parametrize("backend,model", [("pi", ""), ("", ""), ("pi", "muse-glimmer:30b-mlx"), ("pi", "qwen3.8:27b"), ("cursor", "")])
def test_unset_or_local_route_is_refused(backend, model):
    with pytest.raises(tc.TensologyRouteError):
        tc._tensology_project_route({**HOSTED, "coding_backend": backend, "coding_backend_model": model})


def test_hosted_routes_are_used_verbatim():
    assert tc._tensology_project_route(HOSTED) == {"backend": "cursor", "provider": "cursor", "model": "grok-4.7-medium"}
    assert tc._tensology_project_route({**HOSTED, "coding_backend": "pi", "coding_backend_model": "openrouter/x-ai/grok-4"})["provider"] == "openrouter"


def test_route_block_reports_to_tensology_and_never_creates_a_thread():
    client = MagicMock()
    client.get.return_value = [dict(E2E_TICKET)]
    local = {**HOSTED, "coding_backend": "pi", "coding_backend_model": ""}
    with (
        patch.object(tc, "configured_tensology_client", return_value=client),
        patch.object(tc, "_resolve_tensology_project", return_value=local),
        patch.object(tc, "_existing_tensology_thread", return_value=None),
        patch.object(tc, "_local_api_post") as post,
        patch("distr.core.workflow.developer_workflow.get_or_create_development_workflow") as wf,
    ):
        assert tc.process_tensology_approved_tickets(force=True) == 0
    post.assert_not_called()
    wf.assert_not_called()
    client.post.assert_not_called()
    path, body = client.patch.call_args.args[:2]
    assert path == "tickets/9001/execution"
    assert body["status"] == "blocked" and body["thread_id"] == tc.ROUTE_BLOCKED_THREAD_ID
    assert "No default coding route" in body["detail"]


def test_route_block_is_not_re_reported_when_unchanged():
    client = MagicMock()
    local = {**HOSTED, "coding_backend": "pi", "coding_backend_model": ""}
    with patch.object(tc, "_tensology_project_route", side_effect=tc.TensologyRouteError("x", code="route_not_configured")):
        try:
            tc._tensology_project_route(local)
        except tc.TensologyRouteError as exc:
            detail = f"DecisionsAI did not start: {exc}"
    ticket = {**E2E_TICKET, "execution_status": "blocked", "execution_status_detail": detail}
    tc._report_tensology_route_block(client, ticket, local, tc.TensologyRouteError("x", code="route_not_configured"))
    client.patch.assert_not_called()


class _Row(SimpleNamespace):
    pass


@contextmanager
def _fake_session(by_id, by_name):
    db = MagicMock()
    db.get.side_effect = lambda model, pk: by_id.get(pk)
    db.query.return_value.filter.return_value.first.return_value = by_name
    yield db


def test_project_match_prefers_decisions_project_id_over_name(tmp_path):
    by_id = {12: _Row(id=12, name="Renamed locally", folder_location=str(tmp_path), start_time_tracker=False, coding_backend="cursor", coding_backend_model="m")}
    other = _Row(id=99, name="Plan My Charter", folder_location=str(tmp_path), start_time_tracker=False, coding_backend="pi", coding_backend_model="")
    with patch("distr.core.db.get_session", lambda: _fake_session(by_id, other)):
        assert tc._resolve_tensology_project({**E2E_TICKET, "decisions_project_id": "12"})["id"] == 12
        assert tc._resolve_tensology_project({**E2E_TICKET, "decisions_project_id": ""})["id"] == 99
        assert tc._resolve_tensology_project({**E2E_TICKET, "decisions_project_id": "555"})["id"] == 99


def test_poll_backs_off_when_idle_and_speeds_up_with_work():
    client = MagicMock()
    client.get.return_value = []
    with patch.object(tc, "configured_tensology_client", return_value=client):
        tc.process_tensology_approved_tickets(force=True)
        assert tc._TICKET_POLL_INTERVAL == tc.TICKET_POLL_IDLE_SECONDS == 15
        client.get.return_value = [dict(E2E_TICKET)]
        with (
            patch.object(tc, "_resolve_tensology_project", return_value=HOSTED),
            patch.object(tc, "_existing_tensology_thread", return_value=None),
            patch.object(tc, "_create_tensology_thread", return_value=(5, {"complexity": "low", "backend": "cursor", "provider": "cursor", "model": "m"})),
        ):
            tc.process_tensology_approved_tickets(force=True)
        assert tc._TICKET_POLL_INTERVAL == tc.TICKET_POLL_ACTIVE_SECONDS == 3
        # Throttled: a non-forced call inside the window does not hit the API.
        calls = client.get.call_count
        tc.process_tensology_approved_tickets()
        assert client.get.call_count == calls


def test_poll_can_create_project_from_domain_without_registered_repository():
    client = MagicMock()
    client.get.return_value = [{**E2E_TICKET, "repository_ready": False}]
    with (
        patch.object(tc, "configured_tensology_client", return_value=client),
        patch.object(tc, "_resolve_tensology_project", return_value=None),
        patch.object(tc, "_create_tensology_project", return_value=HOSTED) as create_project,
        patch.object(tc, "_existing_tensology_thread", return_value=None),
        patch.object(tc, "_create_tensology_thread", return_value=(5, {"complexity": "low", "backend": "cursor", "provider": "cursor", "model": "m"})),
    ):
        assert tc.process_tensology_approved_tickets(force=True) == 1
    create_project.assert_called_once()


class _Stub(BaseHTTPRequestHandler):
    calls: list = []

    def log_message(self, *args):
        pass

    def _send(self, data):
        raw = json.dumps({"data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        _Stub.calls.append(("GET", self.path, self.headers.get("Authorization", "")[:7]))
        self._send([dict(E2E_TICKET)] if self.path.startswith("/api/integrations/v1/tickets") else {})

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")

    def do_POST(self):
        _Stub.calls.append(("POST", self.path, self._body()))
        self._send({"ok": True})

    def do_PATCH(self):
        _Stub.calls.append(("PATCH", self.path, self._body()))
        self._send({"ok": True})


@pytest.fixture()
def stub_tensology():
    _Stub.calls = []
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_local_simulation_against_stub_tensology(stub_tensology):
    """LOCAL SIMULATION: real client + poll loop against a stub Tensology; the local task API is captured."""
    client = tc.TensologyClient(base_url=stub_tensology, api_key="stub-not-a-real-key", source="decisionsai-ticket-runner")
    captured = {}

    def fake_local_post(path, body):
        captured["path"], captured["body"] = path, body
        return {"task": {"id": 4242}}

    with (
        patch.object(tc, "configured_tensology_client", return_value=client),
        patch.object(tc, "_resolve_tensology_project", return_value=HOSTED),
        patch.object(tc, "_existing_tensology_thread", return_value=None),
        patch.object(tc, "_ensure_tensology_kanban_ticket", return_value=1),
        patch("distr.core.workflow.developer_workflow.get_or_create_development_workflow", return_value=374),
        patch.object(tc, "_local_api_post", side_effect=fake_local_post),
    ):
        assert tc.process_tensology_approved_tickets(force=True) == 1
    body = captured["body"]
    assert captured["path"] == "workflows/studio/tasks"
    # Founder approval in Tensology is the authorisation, so the run starts immediately.
    assert body["skip_human_checkpoints"] is True
    assert (body["backend"], body["provider"], body["model_name"]) == ("cursor", "cursor", "grok-4.7-medium")
    assert body["project_id"] == 12 and body["board_ticket_key"] == "E2E-TEST-001"
    assert '"channel": "mailshot"' in body["prompt"]
    assert '"name": "request.pdf"' in body["prompt"]
    assert "Use the attached request." in body["prompt"]
    claim = [c for c in _Stub.calls if c[0] == "POST"][0]
    assert claim[1] == "/api/integrations/v1/tickets/9001/execution"
    assert claim[2]["thread_id"] == "4242" and claim[2]["status"] == "development"
    assert claim[2]["backend"] == "cursor" and claim[2]["model"] == "grok-4.7-medium"
    print("SIMULATION", json.dumps({"thread_id": 4242, "route": [body["backend"], body["provider"], body["model_name"]],
                                    "skip_human_checkpoints": body["skip_human_checkpoints"], "stub_calls": [c[:2] for c in _Stub.calls]}))


@pytest.mark.parametrize("channel", ["gmail", "mailshot", "whatsapp"])
def test_execution_prompt_preserves_ticket_source(channel):
    prompt = tc._execution_prompt({**E2E_TICKET, "source": {"channel": channel}}, "low")
    assert f'"channel": "{channel}"' in prompt


def test_local_simulation_blocks_plan_my_charter_real_route(stub_tensology):
    """LOCAL SIMULATION with the live project's actual route (pi, no model): blocked and reported, nothing created."""
    client = tc.TensologyClient(base_url=stub_tensology, api_key="stub-not-a-real-key")
    with (
        patch.object(tc, "configured_tensology_client", return_value=client),
        patch.object(tc, "_resolve_tensology_project", return_value={**HOSTED, "coding_backend": "pi", "coding_backend_model": ""}),
        patch.object(tc, "_existing_tensology_thread", return_value=None),
        patch.object(tc, "_local_api_post") as post,
    ):
        assert tc.process_tensology_approved_tickets(force=True) == 0
    post.assert_not_called()
    patch_call = [c for c in _Stub.calls if c[0] == "PATCH"][0]
    assert patch_call[2]["status"] == "blocked"
    print("SIMULATION_BLOCKED", json.dumps(patch_call[2]))


class _RouteSession:
    """Tiny stand-in for get_session used by project auto-create."""

    def __init__(self):
        self.added = []
        self.model_name = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def query(self, model):
        self.model_name = getattr(model, "__name__", "")
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        if self.model_name == "Settings":
            return SimpleNamespace(project_cli_high_backend="pi", project_cli_high_model="deepseek/deepseek-v4-pro")
        return None

    def add(self, obj):
        self.added.append(obj)


def test_missing_project_is_created_from_repository_path(tmp_path):
    folder = tmp_path / "www.jackcat.co.za"
    folder.mkdir()
    client = MagicMock()
    client.get.return_value = [{"local_path_hint": str(folder), "enabled": True}]
    session = _RouteSession()
    snapshot = {
        "id": 14,
        "name": "Jackcat",
        "folder_location": str(folder),
        "start_time_tracker": True,
        "coding_backend": "pi",
        "coding_backend_model": "deepseek/deepseek-v4-pro",
    }
    with (
        patch("distr.core.db.get_session", return_value=session),
        patch.object(tc, "_resolve_tensology_project", return_value=snapshot),
    ):
        created = tc._create_tensology_project(client, {"project_id": 178, "project_name": "Jackcat"})
    assert created == snapshot
    assert len(session.added) == 1
    assert session.added[0].name == "Jackcat"
    assert session.added[0].folder_location == str(folder)
    assert session.added[0].coding_backend == "pi"
    assert session.added[0].coding_backend_model == "deepseek/deepseek-v4-pro"


def test_missing_registered_path_uses_the_local_folder_beside_other_projects(tmp_path):
    live = tmp_path / "CRYSTALLOGIC" / "www.jackcat.co.za"
    live.mkdir(parents=True)
    archived = tmp_path / "CRYSTALLOGIC" / "OLD" / "www.jackcat.co.za"
    archived.mkdir(parents=True)
    found = tc._usable_repository_folder(
        "/gone/JACKCAT/OLD/www.jackcat.co.za",
        search_roots=[tmp_path / "CRYSTALLOGIC"],
    )
    assert found == live
