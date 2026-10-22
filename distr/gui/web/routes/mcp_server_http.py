"""Streamable HTTP transport for Decisions MCP server mode at ``/mcp``.

Only mounted when ``mcp_server_enabled`` is on at app creation (restart to toggle).
Guards: loopback client IP only, ``Authorization: Bearer <mcp_server_token>`` required,
DNS-rebinding Host/Origin checks via the SDK. Stateless JSON-response mode.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from typing import Any

logger = logging.getLogger(__name__)

MOUNT_PATH = "/mcp"


def _is_loopback(host: str | None) -> bool:
    try:
        return ipaddress.ip_address((host or "").split("%")[0]).is_loopback
    except ValueError:
        return False


class MCPServerASGI:
    """ASGI app: auth gate + lazily-started StreamableHTTPSessionManager."""

    def __init__(self, ctx=None) -> None:
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from mcp.server.transport_security import TransportSecuritySettings

        from distr.core.mcp_server.config import relay_vendors_enabled
        from distr.core.mcp_server.tools import RelayContext, build_server

        self.ctx = ctx or RelayContext(relay=relay_vendors_enabled())
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*", "[::1]", "[::1]:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        )
        self.manager = StreamableHTTPSessionManager(
            app=build_server(self.ctx), stateless=True, json_response=True,
            security_settings=security,
        )
        self._started = asyncio.Event()
        self._start_lock: asyncio.Lock | None = None
        self._runner: asyncio.Task | None = None

    async def _ensure_started(self) -> None:
        if self._started.is_set():
            return
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
        async with self._start_lock:
            if self._started.is_set():
                return

            async def _hold() -> None:
                async with self.manager.run():
                    self._started.set()
                    await asyncio.Event().wait()  # run for app lifetime

            self._runner = asyncio.get_running_loop().create_task(_hold())
            await asyncio.wait_for(self._started.wait(), timeout=10)

    @staticmethod
    async def _deny(send, status: int, msg: str) -> None:
        body = ('{"error":"%s"}' % msg).encode()
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        if status == 401:
            headers.append((b"www-authenticate", b'Bearer realm="decisions-mcp"'))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope.get("type") != "http":
            return
        from distr.core.mcp_server.config import is_enabled, token_matches

        client = scope.get("client") or ("", 0)
        if not _is_loopback(client[0]):
            return await self._deny(send, 403, "loopback only")
        if not is_enabled():  # live kill-switch even while mounted
            return await self._deny(send, 404, "mcp server mode disabled")
        auth = ""
        for k, v in scope.get("headers") or []:
            if k.lower() == b"authorization":
                auth = v.decode("latin-1")
                break
        scheme, _, tok = auth.partition(" ")
        if scheme.lower() != "bearer" or not token_matches(tok):
            return await self._deny(send, 401, "bearer token required")
        await self._ensure_started()
        await self.manager.handle_request(scope, receive, send)


def maybe_mount_mcp_server(app) -> bool:
    """Mount ``/mcp`` iff server mode is enabled and the SDK is importable."""
    from distr.core.mcp_server.config import get_or_create_token, is_enabled

    if not is_enabled():
        return False
    try:
        asgi = MCPServerASGI()
    except ImportError:
        logger.warning("MCP server mode enabled but the `mcp` package is missing; /mcp not mounted")
        return False
    get_or_create_token()  # ensure token file exists (never logged)
    app.mount(MOUNT_PATH, asgi, name="decisions_mcp_server")
    app.state.mcp_server_mounted = True
    logger.info("Decisions MCP server mounted at %s (loopback + bearer only)", MOUNT_PATH)
    return True
