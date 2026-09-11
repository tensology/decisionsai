from __future__ import annotations

import json
from pathlib import Path


def test_mcp_harness_writes_catalog(tmp_path):
    from distr.core.mcp_harness import recalibrate_mcp_harness

    result = recalibrate_mcp_harness(home=tmp_path, run_full=False)
    catalog_path = tmp_path / ".decisions" / "harness" / "mcp-recommendations.json"
    assert catalog_path.is_file()
    assert result["catalog_count"] >= 8
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert "refero" in data
    assert "mobbin" in data
    assert "context7" in data
    assert "exa_search" in data
    assert data["computer_use"]["skill"] == "decisions-computer-use"
    assert data["computer_use"]["auto_merge"] is False
    assert data["impeccable"]["skill"] == "impeccable"
    assert data["context7"]["auto_merge"] is False
    assert data["headroom"]["skill"] == "decisions-headroom"
    assert data["headroom"]["auto_merge"] is True
    assert data["headroom"]["merge_targets"] == ["decisions"]
    assert data["headroom"]["mcp"]["args"] == ["-m", "distr.core.headroom_mcp"]
    assert result["decisions_merged"] == ["headroom"]
    native = json.loads(
        (tmp_path / ".decisions" / "models" / "mcp_config.json").read_text(encoding="utf-8")
    )
    headroom = next(server for server in native["servers"] if server["name"] == "headroom")
    assert headroom["enabled"] is True
    assert headroom["command"][-2:] == ["-m", "distr.core.headroom_mcp"]
    assert headroom["env"]["HEADROOM_MCP_READ"] == "on"
    assert "composio_connect" in data
    assert "composio_rube" not in data


def test_merge_cursor_keeps_optional_mcps_in_catalog_only(tmp_path):
    from distr.core.mcp_harness import recalibrate_mcp_harness

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir(parents=True)
    (cursor_dir / "mcp.json").write_text(json.dumps({"mcpServers": {}}) + "\n", encoding="utf-8")

    result = recalibrate_mcp_harness(home=tmp_path, run_full=False)
    assert result["cursor_merged"] == []

    servers = json.loads((cursor_dir / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert servers == {}


def test_merge_cursor_skips_duplicate_exa_url(tmp_path):
    from distr.core.mcp_harness import recalibrate_mcp_harness

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir(parents=True)
    (cursor_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "exa_search": {"url": "https://mcp.exa.ai/mcp"},
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = recalibrate_mcp_harness(home=tmp_path, run_full=False)
    assert "exa" not in result["cursor_merged"]
    servers = json.loads((cursor_dir / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert "exa_search" in servers
    assert "exa" not in servers


def test_fal_stays_opt_in_even_with_env(tmp_path, monkeypatch):
    from distr.core.mcp_harness import recalibrate_mcp_harness

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir(parents=True)
    (cursor_dir / "mcp.json").write_text(json.dumps({"mcpServers": {}}) + "\n", encoding="utf-8")

    result = recalibrate_mcp_harness(home=tmp_path, run_full=False)
    assert "fal-ai" not in result["cursor_merged"]

    monkeypatch.setenv("FAL_KEY", "test-fal-key")
    result = recalibrate_mcp_harness(home=tmp_path, run_full=False)
    assert "fal-ai" not in result["cursor_merged"]
    servers = json.loads((cursor_dir / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert "fal-ai" not in servers


def test_merge_codex_does_not_append_optional_sections(tmp_path):
    from distr.core.mcp_harness import recalibrate_mcp_harness

    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True)
    config = codex_dir / "config.toml"
    config.write_text('model = "gpt-5"\n', encoding="utf-8")

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir(parents=True)
    (cursor_dir / "mcp.json").write_text(json.dumps({"mcpServers": {}}) + "\n", encoding="utf-8")

    result = recalibrate_mcp_harness(home=tmp_path, run_full=False)
    assert result["codex_merged"] == []
    text = config.read_text(encoding="utf-8")
    assert "[mcp_servers.context7]" not in text
    assert "[mcp_servers.headroom]" not in text


def test_headroom_default_merge_preserves_an_explicit_user_entry(tmp_path):
    from distr.core.mcp_harness import recalibrate_mcp_harness

    path = tmp_path / ".decisions" / "models" / "mcp_config.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "servers": [{
                "name": "headroom",
                "enabled": False,
                "transport": "stdio",
                "command": ["custom-headroom"],
            }]
        }),
        encoding="utf-8",
    )

    result = recalibrate_mcp_harness(home=tmp_path, run_full=False)

    assert result["decisions_merged"] == []
    config = json.loads(path.read_text(encoding="utf-8"))
    assert config["servers"][0]["enabled"] is False
    assert config["servers"][0]["command"] == ["custom-headroom"]
