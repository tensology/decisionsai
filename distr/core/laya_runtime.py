"""Small, fail-open bridge between Decisions routing and Laya's MCP server."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from distr.core.mcp.config import (
    MCPConfigDocument,
    MCPServerConfig,
    load_mcp_config,
    save_mcp_config,
)

LAYA_VERSION = "0.3.23"
LAYA_ENV = Path.home() / ".virtualenvs" / "laya"
LAYA_PYTHON = LAYA_ENV / "bin" / "python"
LAYA_SERVER = "laya"
VALID_MODES = {"off", "auto", "prefer"}


def _server(mode: str) -> MCPServerConfig:
    return MCPServerConfig(
        name=LAYA_SERVER,
        enabled=mode != "off",
        transport="stdio",
        command=(str(LAYA_PYTHON), "-m", "laya.mcp.server"),
        env=frozenset(
            {
                ("DECISIONS_LAYA_MODE", mode),
                ("LAYA_DEVICE", "mps"),
                ("LAYA_PRELOAD", "0"),
                ("LAYA_REVISION", "reviewed"),
            }
        ),
    )


def get_mode() -> str:
    server = load_mcp_config().by_name().get(LAYA_SERVER)
    if not server:
        return "off"
    mode = dict(server.env).get("DECISIONS_LAYA_MODE", "auto")
    return mode if server.enabled and mode in VALID_MODES else "off"


def set_mode(mode: str) -> str:
    mode = str(mode or "off").strip().lower()
    if mode not in VALID_MODES:
        raise ValueError("Laya mode must be off, auto, or prefer")
    doc = load_mcp_config()
    servers = [item for item in doc.servers if item.name != LAYA_SERVER]
    servers.append(_server(mode))
    save_mcp_config(MCPConfigDocument(servers=tuple(servers)))
    return mode


def _package_version() -> str:
    if not LAYA_PYTHON.is_file():
        return ""
    result = subprocess.run(
        [str(LAYA_PYTHON), "-c", "import importlib.metadata; print(importlib.metadata.version('laya'))"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def status() -> dict[str, Any]:
    doc = load_mcp_config()
    registered = LAYA_SERVER in doc.by_name()
    connected = False
    try:
        from distr.core.mcp.runtime import get_mcp_hub

        hub = get_mcp_hub()
        connected = bool(hub and hub.is_connected(LAYA_SERVER))
    except Exception:
        pass
    version = _package_version()
    return {
        "installed": bool(version),
        "version": version,
        "reviewed_version": LAYA_VERSION,
        "environment": str(LAYA_ENV),
        "mode": get_mode(),
        "registered": registered,
        "connected": connected,
        "device": "mps",
    }


def install() -> dict[str, Any]:
    LAYA_ENV.parent.mkdir(parents=True, exist_ok=True)
    if not LAYA_PYTHON.is_file():
        subprocess.run([sys.executable, "-m", "venv", str(LAYA_ENV)], check=True, timeout=180)
    subprocess.run(
        [
            str(LAYA_PYTHON),
            "-m",
            "pip",
            "install",
            "--upgrade",
            f"laya[mcp]=={LAYA_VERSION}",
        ],
        check=True,
        timeout=1200,
    )
    if get_mode() == "off":
        set_mode("auto")
    return status()


def remove() -> dict[str, Any]:
    set_mode("off")
    expected_parent = (Path.home() / ".virtualenvs").resolve()
    target = LAYA_ENV.resolve()
    if target.parent != expected_parent or target.name != "laya":
        raise RuntimeError("Refusing to remove an unexpected environment path")
    if target.exists():
        shutil.rmtree(target)
    return status()


def _decode_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
        wrapped = payload.get("result")
        if isinstance(wrapped, str):
            try:
                decoded = json.loads(wrapped)
            except json.JSONDecodeError:
                return payload
            if isinstance(decoded, dict):
                return decoded
        return payload

    if result.get("isError"):
        raise RuntimeError("Laya MCP tool returned an error")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return _unwrap(structured)
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            decoded = json.loads(str(item.get("text") or "{}"))
            if isinstance(decoded, dict):
                return _unwrap(decoded)
    raise RuntimeError("Laya MCP tool returned no JSON result")


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _package_version():
        raise RuntimeError("Laya is not installed")
    from distr.core.mcp.runtime import get_mcp_hub, init_mcp_stack

    if get_mcp_hub() is None:
        init_mcp_stack()
    hub = get_mcp_hub()
    if hub is None:
        raise RuntimeError("MCP runtime is unavailable")
    hub.load_and_apply()
    return _decode_tool_result(hub.call_tool(LAYA_SERVER, name, arguments or {}))


def assess_request(request: str) -> dict[str, Any]:
    result = call_tool(
        "laya_preset",
        {"preset": "model_router", "state": {"request": request}},
    )
    answers = result.get("answers") or {}
    difficulty = answers.get("difficulty") or {}
    domain = answers.get("domain") or {}
    needs_tools = answers.get("needs_tools") or {}
    sensitive = answers.get("is_sensitive") or {}
    score = float(difficulty.get("score", 1.5))
    complexity = "low" if score < 0.75 else "high" if score >= 2.25 else "medium"
    confidence = min(
        float(item.get("answer_confidence", item.get("confidence", 0.0)))
        for item in (difficulty, domain, needs_tools, sensitive)
    )
    return {
        "complexity": complexity,
        "difficulty": score,
        "domain": str(domain.get("choice") or ""),
        "needs_tools": float(needs_tools.get("noul", 0.0)) >= 0.5,
        "is_sensitive": float(sensitive.get("noul", 0.0)) >= 0.5,
        "confidence": confidence,
        "latency_ms": result.get("latency_ms"),
        "routing": result.get("routing") or {},
    }


def test_integration() -> dict[str, Any]:
    from distr.core.mcp.runtime import get_mcp_hub

    health = call_tool("laya_status")
    hub = get_mcp_hub()
    tools = [item.get("name") for item in (hub.list_tools(LAYA_SERVER) if hub else [])]
    assessment = assess_request("Refactor a production service and verify the deployment safely.")
    return {"status": health, "tools": tools, "assessment": assessment}
