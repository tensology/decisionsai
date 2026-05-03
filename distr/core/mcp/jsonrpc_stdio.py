"""Line-delimited JSON-RPC over an MCP stdio subprocess (no SDK required)."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from typing import Any

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"


class MCPStdioRpcError(RuntimeError):
    """JSON-RPC error returned by the MCP server."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"MCP RPC error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class StdioJsonRpcSession:
    """Single MCP session over stdin/stdout with serialized requests."""

    def __init__(
        self,
        command: list[str],
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self._command = command
        self._env = env
        self._cwd = cwd
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._stderr_thread: threading.Thread | None = None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        if self._proc is not None:
            return
        creationflags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=self._env,
            cwd=self._cwd,
            creationflags=creationflags,
        )
        assert self._proc.stdin and self._proc.stdout

        def _drain_stderr() -> None:
            if not self._proc or not self._proc.stderr:
                return
            for line in self._proc.stderr:
                line = line.rstrip()
                if line:
                    logger.debug("MCP stderr [%s]: %s", self.pid, line)

        self._stderr_thread = threading.Thread(
            target=_drain_stderr, name="mcp-stderr", daemon=True
        )
        self._stderr_thread.start()

        self._handshake()

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            logger.debug("MCP stdin close failed", exc_info=True)
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:
            logger.warning("MCP terminate failed", exc_info=True)

    def _read_json_message(self) -> dict[str, Any]:
        if not self._proc or not self._proc.stdout:
            raise BrokenPipeError("MCP stdout unavailable")
        line = self._proc.stdout.readline()
        if line == "":
            code = self._proc.poll()
            raise BrokenPipeError(f"MCP stdout EOF (exit={code})")
        line = line.strip()
        if not line:
            return self._read_json_message()
        try:
            return json.loads(line)
        except json.JSONDecodeError as e:
            raise MCPStdioRpcError(-32700, f"Invalid JSON from MCP: {e}") from e

    def _send_request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if not self._proc or not self._proc.stdin:
            raise BrokenPipeError("MCP not connected")
        msg_id = self._next_id
        self._next_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        raw = json.dumps(payload, separators=(",", ":")) + "\n"
        try:
            self._proc.stdin.write(raw)
            self._proc.stdin.flush()
        except BrokenPipeError:
            raise
        except OSError as e:
            raise BrokenPipeError(str(e)) from e

        while True:
            if self._proc.poll() is not None:
                raise BrokenPipeError(f"MCP process exited with {self._proc.returncode}")
            msg = self._read_json_message()
            if msg.get("method"):
                continue
            rid = msg.get("id")
            if rid != msg_id:
                continue
            if "error" in msg:
                err = msg["error"]
                raise MCPStdioRpcError(
                    int(err.get("code", -32603)),
                    str(err.get("message", "error")),
                    err.get("data"),
                )
            return msg.get("result")

    def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        if not self._proc or not self._proc.stdin:
            raise BrokenPipeError("MCP not connected")
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        raw = json.dumps(payload, separators=(",", ":")) + "\n"
        self._proc.stdin.write(raw)
        self._proc.stdin.flush()

    def _handshake(self) -> None:
        self._send_request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "decisionsai", "version": "1.0.0"},
            },
        )
        self._send_notification("notifications/initialized")

    def list_tools(self) -> dict[str, Any]:
        with self._lock:
            result = self._send_request("tools/list", {})
            return result if isinstance(result, dict) else {}

    def call_tool(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        with self._lock:
            params: dict[str, Any] = {"name": name}
            if arguments is not None:
                params["arguments"] = arguments
            result = self._send_request("tools/call", params)
            return result if isinstance(result, dict) else {}
