"""Desktop photo proxy preserves bytes, caches locally, keeps relay credentials server-side."""
from unittest.mock import patch
import base64
import json
from pathlib import Path
import httpx
import pytest
from fastapi import APIRouter
from distr.gui.web.routes import kanban_whatsapp as routes
from distr.gui.web.routes.kanban_whatsapp import register_whatsapp_routes


@pytest.fixture(autouse=True)
def _avatar_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "DB_DIR", str(tmp_path))
    yield tmp_path


@pytest.mark.asyncio
@pytest.mark.parametrize('status,mime,body,expected', [
    (200, 'image/jpeg', b'\xff\xd8\xffphoto', 200),
    (404, 'text/plain', b'', 404), (503, 'text/plain', b'', 503),
    (200, 'text/html', b'html', 502),
    (200, 'image/png', b'x'*1048577, 502), (200, 'image/png', b'', 404),
])
async def test_photo_proxy(status, mime, body, expected, tmp_path):
    router = APIRouter()
    register_whatsapp_routes(router, lambda _: {'X-Relay-Internal-Token': 'test-only'}, lambda: {})
    endpoint = next(r.endpoint for r in router.routes if r.path.endswith('/profile-picture'))
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(status, content=body, headers={'content-type': mime})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    with patch('httpx.AsyncClient', return_value=client):
        response = await endpoint('120363000000@g.us')
    assert response.status_code == expected
    assert requests[0].url.params['jid'] == '120363000000@g.us'
    assert requests[0].headers['X-Relay-Internal-Token'] == 'test-only'
    assert 'test-only' not in str(dict(response.headers))
    if expected == 200:
        assert response.body == body
        assert response.headers['cache-control'] == 'private, max-age=300'
        bin_path = tmp_path / 'whatsapp_avatars' / '120363000000@g.us.bin'
        meta_path = tmp_path / 'whatsapp_avatars' / '120363000000@g.us.meta'
        assert bin_path.read_bytes() == body
        assert json.loads(meta_path.read_text())['mime'] == 'image/jpeg'
    if expected == 404 and status == 404:
        assert (tmp_path / 'whatsapp_avatars' / '120363000000@g.us.missing').is_file()


@pytest.mark.asyncio
async def test_cache_avoids_second_relay_fetch(tmp_path):
    router = APIRouter()
    register_whatsapp_routes(router, lambda _: {'X-Relay-Internal-Token': 'test-only'}, lambda: {})
    endpoint = next(r.endpoint for r in router.routes if r.path.endswith('/profile-picture'))
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, content=b'\xff\xd8\xffcached', headers={'content-type': 'image/jpeg'})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    with patch('httpx.AsyncClient', return_value=client):
        first = await endpoint('27820000000@s.whatsapp.net')
        second = await endpoint('27820000000@s.whatsapp.net')
    assert first.status_code == 200
    assert second.body == b'\xff\xd8\xffcached'
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_bad_jid_does_not_fetch():
    router = APIRouter()
    register_whatsapp_routes(router, lambda _: {}, lambda: {})
    endpoint = next(r.endpoint for r in router.routes if r.path.endswith('/profile-picture'))
    with patch('httpx.AsyncClient') as client:
        assert (await endpoint('Mariam')).status_code == 400
        client.assert_not_called()


@pytest.mark.asyncio
async def test_device_auth_scopes_photo_to_exact_group():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    key = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    router = APIRouter()
    register_whatsapp_routes(router, lambda _: {}, lambda: {'device_id': 'test-device', 'private_key': base64.b64encode(key).decode()})
    endpoint = next(r.endpoint for r in router.routes if r.path.endswith('/profile-picture'))
    jid = '120363000000@g.us'
    def handle(request):
        if request.url.path.endswith('/challenge'):
            return httpx.Response(200, json={'success': True, 'challenge_id': 'test-challenge', 'challenge_message': 'test-only'})
        if request.url.path.endswith('/ws-auth'):
            assert json.loads(request.content)['subscribe_phones'] == [jid]
            return httpx.Response(200, json={'success': True, 'ws_token': 'test-only'})
        assert request.url.params['ws_token'] == 'test-only'
        assert request.url.params['jid'] == jid
        return httpx.Response(200, content=b'photo', headers={'content-type': 'image/jpeg'})
    original_client = httpx.AsyncClient
    with patch('httpx.AsyncClient', side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(handle))):
        assert (await endpoint(jid)).body == b'photo'
