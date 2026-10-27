"""Pushed Tensology ticket events: websocket client, fetch-after-event, image download, state sync."""
import asyncio
import io
import json
import urllib.error
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import distr.core.tensology_client as tc
import distr.core.tensology_stream as stream
from distr.core.tensology_client import TensologyApiError, TensologyClient


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(stream, "EVENT_DEBOUNCE_SECONDS", 0)
    monkeypatch.delenv(stream.PUSH_PAUSED_ENV, raising=False)
    with tc._TENSOLOGY_TICKETS_LOCK:
        tc._TENSOLOGY_TICKETS.clear()
    yield
    with tc._TENSOLOGY_TICKETS_LOCK:
        tc._TENSOLOGY_TICKETS.clear()


# ── websocket client ────────────────────────────────────────────────────────

def test_stream_url_derived_from_tensology_url():
    assert stream.tensology_stream_url({"tensology_url": "https://www.tensology.com"}) == "wss://www.tensology.com/ws/decisions"
    assert stream.tensology_stream_url({"tensology_url": "http://localhost:8080/"}) == "ws://localhost:8080/ws/decisions"
    assert stream.tensology_stream_url({"tensology_url": "https://x", "tensology_ws_url": "wss://relay/ws/decisions"}) == "wss://relay/ws/decisions"


def test_backoff_doubles_and_caps():
    delays, value = [], 0.0
    for _ in range(9):
        value = stream.next_backoff(value)
        delays.append(value)
    assert delays[:4] == [1.0, 2.0, 4.0, 8.0]
    assert delays[-1] == stream.BACKOFF_MAX_SECONDS


class FakeSocket:
    def __init__(self, messages, hold=0.05):
        self.messages = list(messages)
        self.sent = []
        self.hold = hold

    async def send(self, data):
        self.sent.append(json.loads(data))

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for message in self.messages:
            await asyncio.sleep(0.01)
            yield message
        await asyncio.sleep(self.hold)


def _connector(sockets, seen):
    @asynccontextmanager
    async def connect(url, key):
        seen.append((url, key))
        item = sockets.pop(0)
        if isinstance(item, Exception):
            raise item
        yield item
    return connect


def test_connect_catches_up_then_fetches_pushed_ticket_ids_and_answers_pings():
    seen, catch_ups, events, sleeps = [], [], [], []
    socket = FakeSocket([
        json.dumps({"type": "connected"}),
        json.dumps({"type": "decisions_ticket", "ticket_id": 17, "event": "sent_to_decisions"}),
        json.dumps({"type": "ping", "timestamp": "t"}),
        "not json",
    ])

    async def catch_up():
        catch_ups.append(1)

    async def handle(ids):
        events.append(ids)

    async def sleep(delay):
        sleeps.append(delay)

    asyncio.run(stream.run_tensology_ticket_stream(
        connect=_connector([socket], seen), catch_up=catch_up, handle_events=handle,
        config=lambda: ("wss://t/ws/decisions", "tns_x"), sleep=sleep, max_attempts=1,
    ))
    assert seen == [("wss://t/ws/decisions", "tns_x")]
    assert catch_ups == [1]  # one catch-up on connect
    assert events == [[17]]  # pushed id -> fetch that ticket
    assert socket.sent == [{"type": "pong", "timestamp": "t"}]
    assert len(sleeps) == 1 and sleeps[0] <= 1.2  # reconnect after a short backoff
    assert stream.STREAM_STATE["connected"] is False


def test_reconnects_with_growing_backoff_and_catches_up_on_reconnect():
    seen, catch_ups, sleeps = [], [], []
    clock = {"t": 0.0}

    async def catch_up():
        catch_ups.append(clock["t"])

    async def handle(ids):
        pass

    async def sleep(delay):
        sleeps.append(delay)
        clock["t"] += delay

    sockets = [OSError("refused"), OSError("refused"), FakeSocket([]), OSError("refused")]
    asyncio.run(stream.run_tensology_ticket_stream(
        connect=_connector(sockets, seen), catch_up=catch_up, handle_events=handle,
        config=lambda: ("wss://t/ws/decisions", "k"), sleep=sleep, clock=lambda: clock["t"], max_attempts=4,
    ))
    assert len(seen) == 4
    # 1s, 2s, 4s (connection was short-lived so backoff keeps growing), 8s
    assert [round(d / b, 1) >= 0.8 and round(d / b, 1) <= 1.2 for d, b in zip(sleeps, [1, 2, 4, 8])] == [True] * 4
    # offline fallback at first failure (t=0) and catch-up on the successful (re)connect
    assert len(catch_ups) == 2


def test_event_without_ticket_id_triggers_full_catch_up():
    calls = []

    async def catch_up():
        calls.append("full")

    async def handle(ids):
        calls.append(ids)

    async def scenario():
        dispatcher = stream.TicketEventDispatcher(catch_up, handle)
        worker = asyncio.create_task(dispatcher.run())
        dispatcher.notify(5)
        dispatcher.notify(6)
        await asyncio.sleep(0.05)
        dispatcher.notify(None)
        await asyncio.sleep(0.05)
        worker.cancel()

    asyncio.run(scenario())
    assert calls == [[5, 6], "full"]


def test_not_configured_waits_without_connecting():
    seen, sleeps = [], []

    async def sleep(delay):
        sleeps.append(delay)

    asyncio.run(stream.run_tensology_ticket_stream(
        connect=_connector([], seen), config=lambda: None, sleep=sleep, max_attempts=2,
    ))
    assert seen == [] and sleeps == [stream.NOT_CONFIGURED_RETRY_SECONDS] * 2


# ── pause (tensology_push_paused) ───────────────────────────────────────────

def test_push_paused_env_override_wins_over_setting(monkeypatch):
    assert stream.tensology_push_paused({"tensology_push_paused": True}) is True
    assert stream.tensology_push_paused({"tensology_push_paused": 0}) is False
    assert stream.tensology_push_paused({}) is False
    monkeypatch.setenv(stream.PUSH_PAUSED_ENV, "1")
    assert stream.tensology_push_paused({"tensology_push_paused": False}) is True
    monkeypatch.setenv(stream.PUSH_PAUSED_ENV, "0")
    assert stream.tensology_push_paused({"tensology_push_paused": True}) is False


def test_paused_never_connects_or_catches_up():
    seen, catch_ups, sleeps = [], [], []

    async def catch_up():
        catch_ups.append(1)

    async def sleep(delay):
        sleeps.append(delay)

    asyncio.run(stream.run_tensology_ticket_stream(
        connect=_connector([FakeSocket([])], seen), catch_up=catch_up,
        config=lambda: ("wss://t/ws/decisions", "k"), paused=lambda: True, sleep=sleep, max_attempts=2,
    ))
    assert seen == [] and catch_ups == []
    assert sleeps == [stream.NOT_CONFIGURED_RETRY_SECONDS] * 2
    assert stream.STREAM_STATE["last_error"] == "paused"


class ClosableSocket(FakeSocket):
    def __init__(self):
        super().__init__([])
        self.closed = asyncio.Event()

    async def close(self):
        self.closed.set()

    async def _iter(self):
        await self.closed.wait()
        return
        yield  # pragma: no cover


def test_pausing_live_closes_open_stream_without_offline_catch_up(monkeypatch):
    monkeypatch.setattr(stream, "PAUSE_CHECK_SECONDS", 0.01)
    state = {"paused": False}
    seen, catch_ups, events = [], [], []

    async def catch_up():
        catch_ups.append(1)
        state["paused"] = True  # operator pauses after the stream connected

    async def handle(ids):
        events.append(ids)

    async def sleep(delay):
        pass

    async def scenario():
        socket = ClosableSocket()
        await asyncio.wait_for(stream.run_tensology_ticket_stream(
            connect=_connector([socket], seen), catch_up=catch_up, handle_events=handle,
            config=lambda: ("wss://t/ws/decisions", "k"), paused=lambda: state["paused"],
            sleep=sleep, max_attempts=1,
        ), timeout=5)
        return socket

    socket = asyncio.run(scenario())
    assert socket.closed.is_set()
    assert catch_ups == [1]  # connect catch-up only; no offline catch-up once paused
    assert events == []
    assert stream.STREAM_STATE["connected"] is False


def test_paused_client_skips_ticket_fetch_and_state_patches(monkeypatch):
    monkeypatch.setenv(stream.PUSH_PAUSED_ENV, "1")
    tc._remember_tensology_tickets([_ticket(execution_status="development", decisions_thread_id="42")])
    with patch("distr.core.tensology_client.configured_tensology_client") as configured:
        assert tc.process_tensology_approved_tickets(force=True) == 0
        assert tc.process_tensology_ticket_events([17]) == 0
        assert tc.sync_tensology_execution_states(force=True) == 0
    configured.assert_not_called()


# ── fetch after event ───────────────────────────────────────────────────────

def _ticket(**extra):
    base = {
        "id": 17, "project_id": 9, "project_name": "Snuza", "reference": "PROJ-9-001",
        "title": "Fix contact form", "execution_status": "awaiting_decisions",
        "decisions_thread_id": "", "repository_ready": True, "attachments": [], "discussion": [],
    }
    base.update(extra)
    return base


def test_pushed_event_fetches_ticket_by_id_and_processes_it():
    client = MagicMock()
    client.get.return_value = [_ticket(), _ticket(id=99)]  # older server ignoring ?id=
    with (
        patch("distr.core.tensology_client.configured_tensology_client", return_value=client),
        patch("distr.core.tensology_client._process_tensology_ticket", return_value=True) as process,
    ):
        assert tc.process_tensology_ticket_events([17]) == 1
    path = client.get.call_args.args[0]
    query = client.get.call_args.kwargs["query"]
    assert path == "tickets" and query["id"] == "17"
    assert "awaiting_decisions" in query["execution_status"]
    assert [call.args[1]["id"] for call in process.call_args_list] == [17]


# ── image attachments ──────────────────────────────────────────────────────

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class FakeResponse(io.BytesIO):
    def __init__(self, body, length=None):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body) if length is None else length)}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_download_sends_key_only_to_tensology_host(tmp_path):
    client = TensologyClient("https://www.tensology.com", "tns_secret")
    requests = []

    def urlopen(request, timeout=None):
        requests.append(request)
        return FakeResponse(PNG)

    with patch("urllib.request.urlopen", side_effect=urlopen):
        client.download("tickets/17/attachment?url=%2Fmedia%2Fa.png", tmp_path / "a.png")
        client.download("https://tensology.com/media/b.png", tmp_path / "b.png")
        client.download("https://cdn.example.com/c.png", tmp_path / "c.png")
    assert requests[0].full_url == "https://www.tensology.com/api/integrations/v1/tickets/17/attachment?url=%2Fmedia%2Fa.png"
    assert requests[0].get_header("Authorization") == "Bearer tns_secret"
    assert requests[1].get_header("Authorization") == "Bearer tns_secret"
    assert requests[2].get_header("Authorization") is None
    assert (tmp_path / "a.png").read_bytes() == PNG
    assert not list(tmp_path.glob("*.part"))


def test_download_rejects_too_large_and_http_errors(tmp_path):
    client = TensologyClient("https://www.tensology.com", "k")
    with patch("urllib.request.urlopen", return_value=FakeResponse(PNG, length=10**9)):
        with pytest.raises(TensologyApiError) as too_big:
            client.download("/media/a.png", tmp_path / "a.png", max_bytes=1000)
    assert too_big.value.code == "attachment_too_large"
    with patch("urllib.request.urlopen", return_value=FakeResponse(PNG * 100, length=0)):
        with pytest.raises(TensologyApiError):
            client.download("/media/a.png", tmp_path / "a.png", max_bytes=100)
    error = urllib.error.HTTPError("https://www.tensology.com/x", 404, "Not Found", {}, io.BytesIO(b""))
    with patch("urllib.request.urlopen", side_effect=error):
        with pytest.raises(TensologyApiError) as missing:
            client.download("/media/gone.png", tmp_path / "gone.png")
    assert missing.value.status == 404
    assert not (tmp_path / "a.png").exists() and not list(tmp_path.glob("*.part"))


def test_localize_downloads_images_records_paths_and_never_blocks_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ticket = _ticket(
        attachments=[
            {"name": "spec.png", "url": "/media/q/spec.png", "type": "image/png", "api_path": "tickets/17/attachment?url=%2Fmedia%2Fq%2Fspec.png"},
            {"name": "notes.pdf", "url": "/media/q/notes.pdf", "type": "application/pdf"},
            {"name": "voice.ogg", "url": "/media/q/voice.ogg", "type": "audio/ogg", "kind": "audio", "transcript": "Add the export flow"},
            {"name": "gone.jpg", "url": "/media/q/gone.jpg", "api_path": "tickets/17/attachment?url=gone"},
        ],
        discussion=[{"content": "see shot", "attachments": [{"name": "shot.png", "url": "/media/d/shot.png", "type": "image/png"}]}],
    )
    client = MagicMock()

    def download(location, destination, **kwargs):
        if "gone" in location:
            raise TensologyApiError("Attachment is unavailable (HTTP 404).", status=404, code="attachment_http_error")
        Path(destination).write_bytes(PNG)
        return len(PNG)

    client.download.side_effect = download
    result = tc._localize_tensology_attachments(client, ticket, {"id": 8})
    root = tmp_path / ".decisions/workspaces/projects/8/attachments/tickets/tensology/9/PROJ-9-001"
    assert result["local_image_paths"] == [str(root / "spec.png"), str(root / "shot.png")]
    assert result["attachments"][0]["download_status"] == "downloaded"
    # Documents and voice notes are now localized too, so the thread keeps the originals.
    assert result["attachments"][1]["download_status"] == "downloaded"
    assert result["attachments"][2]["local_path"].endswith("voice.ogg")
    assert result["attachments"][2]["transcript"] == "Add the export flow"
    assert result["local_attachment_paths"] == [
        str(root / "spec.png"), str(root / "notes.pdf"), str(root / "voice.ogg"), str(root / "shot.png"),
    ]
    assert "404" in result["attachments"][3]["download_error"]
    assert client.download.call_args_list[0].args[0].startswith("tickets/17/attachment")  # authed api_path first
    assert ticket["attachments"][0].get("local_path") is None  # input not mutated


def test_execution_prompt_includes_local_image_paths():
    ticket = _ticket(local_image_paths=["/tmp/x/spec.png"], description="Describe", tasks=[])
    prompt = tc._execution_prompt(ticket, {"id": 8, "name": "Snuza", "folder_location": "/tmp/snuza"})
    assert "/tmp/x/spec.png" in prompt
    assert "local_image_paths" in prompt


def test_new_thread_gets_localized_ticket_and_claims_remote():
    client = MagicMock()
    localized = {**_ticket(), "local_image_paths": ["/tmp/a.png"]}
    with (
        patch("distr.core.tensology_client._resolve_tensology_project", return_value={"id": 8, "name": "Snuza"}),
        patch("distr.core.tensology_client._existing_tensology_thread", return_value=None),
        patch("distr.core.tensology_client._localize_tensology_attachments", return_value=localized) as localize,
        patch("distr.core.tensology_client._create_tensology_thread", return_value=(42, {})) as create,
    ):
        assert tc._process_tensology_ticket(client, _ticket()) is True
    localize.assert_called_once()
    assert create.call_args.args[0]["local_image_paths"] == ["/tmp/a.png"]
    assert client.post.call_args.kwargs["idempotency_key"] == "tensology-ticket-17-thread-42"


def test_image_failure_does_not_block_thread_creation():
    client = MagicMock()
    with (
        patch("distr.core.tensology_client._resolve_tensology_project", return_value={"id": 8, "name": "Snuza"}),
        patch("distr.core.tensology_client._existing_tensology_thread", return_value=None),
        patch("distr.core.tensology_client._localize_tensology_attachments", side_effect=OSError("disk full")),
        patch("distr.core.tensology_client._create_tensology_thread", return_value=(42, {})) as create,
    ):
        assert tc._process_tensology_ticket(client, _ticket()) is True
    create.assert_called_once()


# ── local state sync (replaces the poll's status mirror) ───────────────────

def test_state_sync_patches_only_on_change():
    tc._remember_tensology_tickets([_ticket(execution_status="development", decisions_thread_id="42")])
    client = MagicMock()
    client.patch.return_value = _ticket(execution_status="failed", decisions_thread_id="42")
    state = {"status": "running"}
    with (
        patch("distr.core.tensology_client.configured_tensology_client", return_value=client),
        patch("distr.core.tensology_client._existing_tensology_thread", return_value=42),
        patch("distr.core.workflow.development_harness.development_execution_state", side_effect=lambda chat_id: dict(state)),
        patch("distr.core.tensology_client._tensology_execution_receipt", return_value={}),
    ):
        assert tc.sync_tensology_execution_states(force=True) == 0
        client.patch.assert_not_called()
        state.update(status="failed", error="boom")
        assert tc.sync_tensology_execution_states(force=True) == 1
        assert client.patch.call_args.args[1]["status"] == "failed"
        # failed is terminal: dropped from the live cache, no further calls
        assert tc.sync_tensology_execution_states(force=True) == 0
    assert client.patch.call_count == 1


def test_state_sync_does_nothing_without_claimed_tickets():
    with patch("distr.core.tensology_client.configured_tensology_client") as configured:
        assert tc.sync_tensology_execution_states(force=True) == 0
    configured.assert_not_called()


def test_work_request_event_triggers_handler():
    seen, handled = [], []
    socket = FakeSocket([
        json.dumps({"type": "connected"}),
        json.dumps({"type": "decisions_work_request", "request_id": 55, "event": "proposal_created"}),
    ])

    async def catch_up():
        pass

    async def handle(ids):
        pass

    async def handle_wr(request_id):
        handled.append(request_id)

    async def sleep(delay):
        pass

    asyncio.run(stream.run_tensology_ticket_stream(
        connect=_connector([socket], seen), catch_up=catch_up, handle_events=handle,
        handle_work_request=handle_wr, config=lambda: ("wss://t/ws/decisions", "k"),
        sleep=sleep, max_attempts=1,
    ))
    assert handled == [55]


def test_work_request_signal_records_needs_input(monkeypatch):
    import distr.core.orchestration_events as oe

    calls = []
    monkeypatch.setattr(oe, "emit_orchestration_event", lambda **kw: calls.append(kw))
    with patch("distr.core.chat.ChatService.get_current_chat_id", return_value=None):
        stream.handle_work_request_signal(77)

    assert calls and calls[0]["event_type"] == "needs_input"
    assert "77" in calls[0]["summary"]
    assert calls[0]["source"] == "tensology"
