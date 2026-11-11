"""Remote cache-only media reads never invoke relay, transcode, ACK, or DB writes."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from distr.gui.web.routes import kanban_whatsapp as routes


@pytest.fixture
def cached_media(tmp_path, monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    routes.WhatsAppMessage.__table__.create(engine)
    media_dir = tmp_path / 'whatsapp_media'
    media_dir.mkdir()
    cached = media_dir / 'cached.png'
    cached.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    outside = tmp_path / 'whatsapp_media_other' / 'private.png'
    outside.parent.mkdir()
    outside.write_bytes(b'private')
    (media_dir / 'escape.png').symlink_to(outside)
    with Session(engine) as db:
        for ident, path in [(1, str(cached)), (2, ''), (3, str(outside)), (4, str(media_dir / 'escape.png'))]:
            db.add(routes.WhatsAppMessage(id=ident, message_id=f'WA{ident}', jid='fixture@s.whatsapp.net', media_type='image', media_mime_type='image/png', media_filename='photo"\r\nInjected: value.png', media_local_path=path))
        db.commit()
    sql = []
    event.listen(engine, 'before_cursor_execute', lambda conn, cursor, statement, params, context, many: sql.append(statement))
    @contextmanager
    def session():
        with Session(engine) as db:
            yield db
    monkeypatch.setattr(routes, 'get_session', session)
    monkeypatch.setattr(routes, 'DB_DIR', str(tmp_path))
    monkeypatch.setattr(routes, 'resolve_whatsapp_media_disk_path', lambda value: value)
    relay = Mock(side_effect=AssertionError('Cache-only reads must never enter provider path'))
    identity = Mock(side_effect=AssertionError('Cache-only reads must never request device identity'))
    router = APIRouter()
    routes.register_whatsapp_routes(router, relay, identity)
    monkeypatch.setattr('distr.gui.web.security.get_internal_api_token', lambda: 'fixture-token')
    endpoint = next(route.endpoint for route in router.routes if route.path.endswith('/relay-media/{message_id}'))
    for name, cell in zip(endpoint.__code__.co_freevars, endpoint.__closure__):
        if name == '_whatsapp_relay_base_url':
            monkeypatch.setattr(cell, 'cell_contents', relay)
    app = FastAPI()
    app.include_router(router, prefix='/api')
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, sql=sql, relay=relay, cached=cached, identity=identity)
    engine.dispose()


def test_cached_original_bytes_and_filename_are_safe(cached_media):
    result = cached_media.client.get('/api/tickets/whatsapp/relay-media/1?cached_only=true&format=mp3')
    assert result.status_code == 200
    assert result.content == cached_media.cached.read_bytes()
    assert result.headers['content-type'] == 'image/png'
    assert result.headers['content-disposition'].startswith("attachment; filename*=utf-8''")
    assert '%0D%0A' in result.headers['content-disposition']
    assert 'injected' not in result.headers
    assert cached_media.sql and all(sql.lstrip().upper().startswith('SELECT') for sql in cached_media.sql)
    cached_media.relay.assert_not_called()


@pytest.mark.parametrize('ident,query', [('2', ''), ('3', ''), ('4', ''), ('999', ''), ('-1', ''), ('1', '&wa_key=WRONG')])
def test_unavailable_or_mismatched_media_never_fetches_or_mutates(cached_media, ident, query):
    result = cached_media.client.get(f'/api/tickets/whatsapp/relay-media/{ident}?cached_only=true{query}')
    assert result.status_code == 404
    assert all(sql.lstrip().upper().startswith('SELECT') for sql in cached_media.sql)
    cached_media.relay.assert_not_called()


def test_non_numeric_message_id_is_rejected(cached_media):
    assert cached_media.client.get('/api/tickets/whatsapp/relay-media/not-a-message?cached_only=true').status_code == 422
    assert not cached_media.sql
    cached_media.relay.assert_not_called()


def test_uncached_attachment_uses_authoritative_relay_without_processing_message(cached_media, monkeypatch):
    import base64
    import httpx
    from distr.gui.web.security import INTERNAL_AUTH_HEADER
    from tests.core.test_remote_voice_transcribe_paths import _Host

    captured = []

    def upstream(request):
        captured.append(request)
        if request.headers["range"].startswith("bytes=4-"):
            return httpx.Response(404)
        return httpx.Response(206, content=b"data", headers={
            "Content-Range": "bytes 0-3/8",
            "ETag": '"fixture-tag"',
            "Content-Type": "image/png",
        })

    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(upstream), **kwargs
    ))
    path = "/api/tickets/whatsapp/incoming-media/2"
    assert cached_media.client.get(path).status_code == 401
    assert cached_media.client.get(path, headers={
        INTERNAL_AUTH_HEADER: "fixture-token", "Range": "bytes=0-1048576",
    }).status_code == 400
    assert not captured
    cached_media.relay.assert_not_called()
    cached_media.relay.side_effect = ["https://relay.example.test", {"Authorization": "fixture"}] * 2
    monkeypatch.setattr('distr.gui.web.server.get_unified_server', lambda: None)
    monkeypatch.setattr('requests.get', lambda url, **kwargs: cached_media.client.get(
        url.removeprefix('http://127.0.0.1:8765'), headers=kwargs['headers'],
    ))
    host = _Host()
    for offset in (0, 4):
        host._dispatch_api_relay({'request_id': str(offset), 'data': {
            'method': 'GET', 'path': path, 'response_type': 'base64', 'media_offset': offset,
        }})
    success, missing = [frame["data"] for frame in host.responses]
    assert success["status"] == 206
    assert base64.b64decode(success["response"]["body_base64"]) == b"data"
    assert success["response"]["content_range"] == "bytes 0-3/8"
    assert success["response"]["etag"] == '"fixture-tag"'
    assert missing["status"] == 404
    assert "unavailable on the desktop and WhatsApp relay" in missing["response"]["detail"]
    assert len(captured) == 2 and all(request.method == "GET" for request in captured)
    assert captured[0].method == "GET"
    assert captured[0].url.path.endswith("/media/WA2")
    assert captured[0].headers["range"] == "bytes=0-1048575"
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in cached_media.sql)


def test_uncached_ranges_reuse_device_auth_and_renew_rejected_token(cached_media, monkeypatch):
    import base64
    import httpx
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from distr.gui.web.security import INTERNAL_AUTH_HEADER

    original = bytes(range(35))
    challenged = []
    authorized = []
    media_reads = []
    clock = [1000.0]
    monkeypatch.setattr(routes, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    cached_media.relay.side_effect = lambda *args: {} if args else "https://relay.example.test"
    private_key = Ed25519PrivateKey.generate().private_bytes_raw()
    cached_media.identity.side_effect = None
    cached_media.identity.return_value = {
        "device_id": "fixture-device",
        "private_key": base64.b64encode(private_key).decode(),
    }

    def upstream(request):
        if request.url.path == "/device/challenge":
            challenged.append(request)
            if len(challenged) > 30:
                return httpx.Response(429, json={"success": False})
            return httpx.Response(200, json={
                "success": True, "challenge_id": str(len(challenged)), "challenge_message": "fixture",
            })
        if request.url.path == "/device/ws-auth":
            authorized.append(request)
            return httpx.Response(200, json={
                "success": True, "ws_token": f"fixture-{len(authorized)}", "expires_in_seconds": 180,
            })
        assert request.method == "GET"
        offset = int(request.headers["range"].removeprefix("bytes=").split("-")[0])
        token = request.url.params["ws_token"]
        media_reads.append((offset, token))
        if offset >= 20 and token == "fixture-1":
            return httpx.Response(401)
        return httpx.Response(206, content=original[offset:offset + 1], headers={
            "Content-Range": f"bytes {offset}-{offset}/{len(original)}", "ETag": '"same-file"',
        })

    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(upstream), **kwargs,
    ))
    restored = bytearray()
    for offset in range(len(original)):
        if offset == 31:
            clock[0] += 180
        result = cached_media.client.get("/api/tickets/whatsapp/incoming-media/2", headers={
            INTERNAL_AUTH_HEADER: "fixture-token", "Range": f"bytes={offset}-{offset}",
        })
        assert result.status_code == 206, (offset, result.text)
        restored.extend(result.content)
    assert restored == original
    assert len(challenged) == len(authorized) == 3
    assert [token for offset, token in media_reads if offset == 20] == ["fixture-1", "fixture-2"]
    assert [token for offset, token in media_reads if offset == 31] == ["fixture-3"]
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in cached_media.sql)


def test_binary_api_bridge_preserves_cached_bytes(cached_media, monkeypatch):
    import base64
    from tests.core.test_remote_voice_transcribe_paths import _Host
    monkeypatch.setattr('distr.gui.web.server.get_unified_server', lambda: SimpleNamespace(is_running=True, get_url=lambda: 'http://127.0.0.1:8765'))
    monkeypatch.setattr('distr.gui.web.security.get_internal_api_token', lambda: 'fixture-token')
    calls = []
    def local_get(url, **kwargs):
        calls.append((url, kwargs))
        return cached_media.client.get(url.removeprefix('http://127.0.0.1:8765'))
    monkeypatch.setattr('requests.get', local_get)
    host = _Host()
    host._dispatch_api_relay({'request_id': 'cached-media-fixture', 'data': {
        'method': 'GET', 'path': '/api/tickets/whatsapp/relay-media/1?cached_only=true', 'response_type': 'base64'}})
    response = host.responses[-1]['data']
    assert response['status'] == 200
    assert response['response']['content_type'] == 'image/png'
    assert base64.b64decode(response['response']['body_base64']) == cached_media.cached.read_bytes()
    assert response['response']['size_bytes'] == len(cached_media.cached.read_bytes())
    assert len(calls) == 1 and calls[0][0].endswith('?cached_only=true')
    cached_media.relay.assert_not_called()
    assert all(sql.lstrip().upper().startswith('SELECT') for sql in cached_media.sql)


def test_binary_bridge_can_transfer_large_cached_file_in_bounded_frames(cached_media, monkeypatch):
    import base64
    from tests.core.test_remote_voice_transcribe_paths import _Host

    original = bytes(range(251)) * 54309
    cached_media.cached.write_bytes(original)
    monkeypatch.setattr('distr.gui.web.server.get_unified_server', lambda: None)
    monkeypatch.setattr('distr.gui.web.security.get_internal_api_token', lambda: 'fixture-token')
    monkeypatch.setattr(
        'requests.get',
        lambda url, **kwargs: cached_media.client.get(
            url.removeprefix('http://127.0.0.1:8765'), headers=kwargs['headers']
        ),
    )
    host = _Host()
    chunk_size = 1024 * 1024
    assert len(base64.b64encode(original)) > 16 * 1024 * 1024
    restored = bytearray()
    tags = set()
    for offset in range(0, len(original), chunk_size):
        host._dispatch_api_relay({'request_id': str(offset), 'data': {
            'method': 'GET', 'path': '/api/tickets/whatsapp/relay-media/1?cached_only=true',
            'response_type': 'base64', 'media_offset': offset,
        }})
        result = host.responses.pop()['data']
        assert result['status'] == 206
        chunk = result['response']
        end = min(offset + chunk_size, len(original)) - 1
        assert chunk['content_range'] == f'bytes {offset}-{end}/{len(original)}'
        assert chunk['size_bytes'] == end - offset + 1
        assert len(chunk['body_base64']) < 2 * chunk_size
        tags.add(chunk['etag'])
        restored.extend(base64.b64decode(chunk['body_base64']))
    assert restored == original
    assert len(tags) == 1 and all(tags)
    cached_media.relay.assert_not_called()
    assert all(sql.lstrip().upper().startswith('SELECT') for sql in cached_media.sql)
