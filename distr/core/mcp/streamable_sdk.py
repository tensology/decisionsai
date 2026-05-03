"""Optional MCP Python SDK streamable HTTP session (requires ``mcp`` package + asyncio thread)."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


def mcp_sdk_available() -> bool:
    try:
        from mcp.client.streamable_http import streamable_http_client  # noqa: F401

        return True
    except ImportError:
        return False


class StreamableSdkSession:
    """Runs ``ClientSession`` over ``streamable_http_client`` in a dedicated event-loop thread."""

    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self._url = url
        self._headers = dict(headers or {})
        self._loop = asyncio.new_event_loop()
        self._thread: threading.Thread | None = None
        self._stop_evt: asyncio.Event | None = None
        self._session: Any = None
        self._ready = threading.Event()
        self._start_error: BaseException | None = None

    def start(self, *, timeout: float = 60.0) -> None:
        self._thread = threading.Thread(
            target=self._thread_main, name="mcp-streamable", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            raise TimeoutError(f"MCP streamable connect timeout ({timeout}s): {self._url}")
        if self._start_error:
            raise self._start_error

    def _thread_main(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main())
        finally:
            try:
                pending = asyncio.all_tasks(self._loop)
                for t in pending:
                    t.cancel()
                if pending:
                    self._loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                logger.debug("MCP streamable loop cleanup", exc_info=True)
            self._loop.close()

    async def _async_main(self) -> None:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        self._stop_evt = asyncio.Event()
        try:
            try:
                transport_cm = (
                    streamable_http_client(self._url, headers=self._headers)
                    if self._headers
                    else streamable_http_client(self._url)
                )
            except TypeError:
                transport_cm = streamable_http_client(self._url)
            async with transport_cm as streams:
                read_stream = streams[0]
                write_stream = streams[1]
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.set()
                    await self._stop_evt.wait()
        except BaseException as e:
            self._start_error = e
            self._ready.set()
            logger.warning("MCP streamable session ended: %s", e, exc_info=True)
        finally:
            self._session = None

    def stop(self) -> None:
        if self._loop.is_running() and self._stop_evt:

            def _wake() -> None:
                if self._stop_evt and not self._stop_evt.is_set():
                    self._stop_evt.set()

            self._loop.call_soon_threadsafe(_wake)
        if self._thread:
            self._thread.join(timeout=15)
            self._thread = None

    def is_alive(self) -> bool:
        return self._session is not None

    def list_tools(self, *, timeout: float = 60.0) -> dict[str, Any]:
        session = self._session
        if session is None:
            raise BrokenPipeError("MCP streamable not connected")

        async def _run() -> dict[str, Any]:
            res = await session.list_tools()
            tools_out: list[dict[str, Any]] = []
            for t in res.tools:
                if hasattr(t, "model_dump"):
                    tools_out.append(t.model_dump(mode="json"))
                else:
                    tools_out.append(
                        {
                            "name": getattr(t, "name", ""),
                            "description": getattr(t, "description", "") or "",
                            "inputSchema": getattr(t, "inputSchema", {}) or {},
                        }
                    )
            return {"tools": tools_out}

        fut = asyncio.run_coroutine_threadsafe(_run(), self._loop)
        return fut.result(timeout=timeout)

    def call_tool(
        self, name: str, arguments: dict[str, Any] | None, *, timeout: float = 60.0
    ) -> dict[str, Any]:
        session = self._session
        if session is None:
            raise BrokenPipeError("MCP streamable not connected")

        async def _run() -> dict[str, Any]:
            result = await session.call_tool(name, arguments or {})
            if hasattr(result, "model_dump"):
                return result.model_dump(mode="json")
            return {"content": getattr(result, "content", [])}

        fut = asyncio.run_coroutine_threadsafe(_run(), self._loop)
        return fut.result(timeout=timeout)
