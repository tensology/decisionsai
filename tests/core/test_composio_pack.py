from __future__ import annotations

import json
from pathlib import Path


def test_composio_catalog_in_mcp_harness(tmp_path):
    from distr.core.mcp_harness import collect_mcp_catalog

    catalog = collect_mcp_catalog()
    assert "composio_connect" in catalog
    assert "composio_rube" not in catalog
    assert catalog["composio_connect"]["auto_merge"] is True


def test_merge_adds_composio_connect(tmp_path):
    from distr.core.mcp_harness import recalibrate_mcp_harness

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir(parents=True)
    (cursor_dir / "mcp.json").write_text(json.dumps({"mcpServers": {}}) + "\n", encoding="utf-8")

    result = recalibrate_mcp_harness(home=tmp_path, run_full=False)
    merged = set(result["cursor_merged"])
    assert "composio" in merged

    servers = json.loads((cursor_dir / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert servers["composio"]["url"] == "https://connect.composio.dev/mcp"


def test_prunes_deprecated_rube_server(tmp_path, monkeypatch):
    from distr.core.mcp_harness import recalibrate_mcp_harness

    monkeypatch.setenv("COMPOSIO_API_KEY", "test-composio-key")
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir(parents=True)
    (cursor_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "rube": {
                        "url": "https://rube.app/mcp?agent=cursor",
                        "headers": {},
                    },
                    "composio": {
                        "url": "https://connect.composio.dev/mcp",
                        "headers": {},
                    },
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = recalibrate_mcp_harness(home=tmp_path, run_full=False)
    assert "removed:rube" in result["cursor_merged"]

    servers = json.loads((cursor_dir / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert "rube" not in servers
    assert servers["composio"]["headers"]["x-api-key"] == "test-composio-key"


def test_composio_pack_projects_skill(tmp_path, monkeypatch):
    from distr.core.composio_pack import ensure_composio_pack_setup

    monkeypatch.setattr(
        "distr.core.composio_pack.detected_harnesses",
        lambda: {"codex": True, "cursor": False, "claude": False, "pi": False},
    )
    result = ensure_composio_pack_setup(home=tmp_path, run_full=False)
    assert result["status"] == "configured"
    skill = tmp_path / "plugins" / "decisions-codex" / "skills" / "decisions-composio" / "SKILL.md"
    assert skill.is_file()
    assert "Rube is deprecated" in skill.read_text(encoding="utf-8")


def test_merge_composio_pre_chain_for_slack():
    from distr.core.composio_pack import merge_composio_pre_chain

    chain = merge_composio_pre_chain(["post-to-slack"], project_folder="")
    assert "decisions-composio" in chain
