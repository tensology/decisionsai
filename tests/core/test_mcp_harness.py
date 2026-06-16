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
    assert data["context7"]["auto_merge"] is True
    assert "composio_connect" in data
    assert "composio_rube" not in data


def test_merge_cursor_adds_context7_and_design_mcps(tmp_path):
    from distr.core.mcp_harness import recalibrate_mcp_harness

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir(parents=True)
    (cursor_dir / "mcp.json").write_text(json.dumps({"mcpServers": {}}) + "\n", encoding="utf-8")

    result = recalibrate_mcp_harness(home=tmp_path, run_full=False)
    merged = set(result["cursor_merged"])
    assert "context7" in merged
    assert "mobbin" in merged
    assert "refero" in merged
    assert "exa" in merged

    servers = json.loads((cursor_dir / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert servers["context7"]["command"] == "npx"
    assert "@upstash/context7-mcp" in servers["context7"]["args"][1]
    assert servers["mobbin"]["url"] == "https://api.mobbin.com/mcp"
    assert servers["refero"]["url"] == "https://api.refero.design/mcp"


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


def test_fal_only_merges_with_env(tmp_path, monkeypatch):
    from distr.core.mcp_harness import recalibrate_mcp_harness

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir(parents=True)
    (cursor_dir / "mcp.json").write_text(json.dumps({"mcpServers": {}}) + "\n", encoding="utf-8")

    result = recalibrate_mcp_harness(home=tmp_path, run_full=False)
    assert "fal-ai" not in result["cursor_merged"]

    monkeypatch.setenv("FAL_KEY", "test-fal-key")
    result = recalibrate_mcp_harness(home=tmp_path, run_full=False)
    assert "fal-ai" in result["cursor_merged"]
    servers = json.loads((cursor_dir / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert servers["fal-ai"]["env"]["FAL_KEY"] == "test-fal-key"


def test_merge_codex_appends_missing_sections(tmp_path):
    from distr.core.mcp_harness import recalibrate_mcp_harness

    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True)
    config = codex_dir / "config.toml"
    config.write_text('model = "gpt-5"\n', encoding="utf-8")

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir(parents=True)
    (cursor_dir / "mcp.json").write_text(json.dumps({"mcpServers": {}}) + "\n", encoding="utf-8")

    result = recalibrate_mcp_harness(home=tmp_path, run_full=False)
    assert "context7" in result["codex_merged"]
    text = config.read_text(encoding="utf-8")
    assert "[mcp_servers.context7]" in text
    assert "@upstash/context7-mcp" in text
