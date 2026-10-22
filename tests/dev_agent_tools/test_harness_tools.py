"""Dev-agent tools across harnesses + calendar + approvals. Temp HOME/DB only."""
import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest

from distr.core.workflow import dev_agent_tools as t
from distr.core.workflow import dev_agent_mcp as bridge

SECRET = "TOKEN-SHOULD-NEVER-APPEAR-123"
ALL_TOOLS = {"image_generate", "vision_analyze", "video_generate", "calendar", "files", "browser", "approval_wait", "select_thread", "open_thread"}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path / "db"))
    monkeypatch.setenv("DECISIONS_MCP_SERVER_TOKEN", SECRET)
    monkeypatch.setenv("DECISIONS_CHAT_ID", "7")
    monkeypatch.setenv("DECISIONS_TURN_ID", "3")
    monkeypatch.delenv("DECISIONS_CURSOR_DEV_MCP", raising=False)


def _task(**kw):
    from distr.core.project_cli_backends.base import ProjectTask
    kw.setdefault("folder", "/tmp/p")
    return ProjectTask(project_id=1, project_name="P", instruction="do", **kw)


NON_DEV = [dict(origin="workflow"), dict(origin="development", adapter_options={"read_only_expected": True}),
           dict(origin="development", adapter_options={"disable_tools": True}),
           dict(origin="development", adapter_options={"disable_dev_agent_tools": True})]


def _backend(cls_name):
    from distr.core.project_cli_backends import registry as r
    return getattr(r, cls_name)()


# ---------- exposure ----------
def test_exposed_tools_generic_hide_stub_and_speech(monkeypatch):
    names = set(t.exposed_tools())
    assert names == ALL_TOOLS
    monkeypatch.setitem(t.TOOL_SCHEMAS, "speak_text_directly", {"description": "x", "parameters": {"type": "object", "properties": {}}})
    monkeypatch.setitem(t.DISPATCH, "speak_text_directly", lambda **_: {"ok": True})
    monkeypatch.setitem(t.TOOL_SCHEMAS, "files_new", {"description": "x", "parameters": {"type": "object", "properties": {}}})
    monkeypatch.setitem(t.DISPATCH, "files_new", lambda **_: {"ok": True})
    names = set(t.exposed_tools())
    assert "files_new" in names and "speak_text_directly" not in names
    assert not t.call_tool("speak_text_directly", {})["ok"]


def test_bridge_gated_by_env(monkeypatch):
    monkeypatch.delenv("DECISIONS_DEV_AGENT_TOOLS", raising=False)
    assert bridge.list_specs() == []
    assert not bridge.run_tool("calendar", {"action": "list"})["ok"]
    monkeypatch.setenv("DECISIONS_DEV_AGENT_TOOLS", "1")
    specs = {s["name"]: s for s in bridge.list_specs()}
    assert set(specs) == ALL_TOOLS
    assert "start_image" in specs["video_generate"]["inputSchema"]["properties"]
    assert bridge.run_tool("computer_use", {"goal": "x"})["ok"] is False


def test_bridge_mcp_protocol_lists_tools(monkeypatch):
    monkeypatch.setenv("DECISIONS_DEV_AGENT_TOOLS", "1")
    from mcp import types
    server = bridge.build_server()
    handler = server.request_handlers[types.ListToolsRequest]
    res = asyncio.run(handler(types.ListToolsRequest(method="tools/list")))
    assert {tl.name for tl in res.root.tools} >= {"calendar", "video_generate"}


# ---------- per-harness command injection ----------
def test_claude_dev_turn_gets_mcp_config_before_p():
    cmd = _backend("ClaudeCodeBackend")._build_command("claude", _task(origin="development", chat_id=7))
    i = cmd.index("--mcp-config")
    cfg = json.loads(cmd[i + 1])["mcpServers"]["decisions_dev"]
    assert cfg["args"] == ["-m", "distr.core.workflow.dev_agent_mcp"] and cfg["env"]["DECISIONS_DEV_AGENT_TOOLS"] == "1"
    assert cfg["env"]["DECISIONS_CHAT_ID"] == "7"
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__decisions_dev"
    assert i < cmd.index("-p") and cmd[-1] == "do"
    assert SECRET not in " ".join(cmd)


def test_codex_dev_turn_gets_per_run_mcp_servers():
    cmd = _backend("CodexBackend")._build_command("codex", _task(origin="development", chat_id=7))
    joined = " ".join(cmd)
    assert "mcp_servers.decisions_dev.command=" in joined and "dev_agent_mcp" in joined
    assert "mcp_servers.decisions_dev.tool_timeout_sec=180" in joined
    assert 'DECISIONS_DEV_AGENT_TOOLS="1"' in joined and SECRET not in joined
    assert cmd[-1].endswith("do")


@pytest.mark.parametrize("kw", NON_DEV)
def test_non_dev_turns_get_no_tools(kw, monkeypatch):
    from distr.core.project_cli_backends.registry import _pi_print_command, _cursor_dev_mcp_install
    assert "--mcp-config" not in _backend("ClaudeCodeBackend")._build_command("claude", _task(**kw))
    assert "decisions_dev" not in " ".join(_backend("CodexBackend")._build_command("codex", _task(**kw)))
    assert "--approve-mcps" not in _backend("CursorBackend")._build_command("cursor-agent", _task(**kw))
    assert _cursor_dev_mcp_install(_task(**kw)) is None
    assert not any(str(c).endswith("dev_media_tools.ts") for c in _pi_print_command("pi", _task(**kw)))


def test_pi_extension_registers_extra_tools_generically():
    src = (Path(t.__file__).parents[1] / "project_cli_backends/pi_extensions/dev_media_tools.ts").read_text()
    assert '"schemas"' in src and "Type.Unsafe" in src


