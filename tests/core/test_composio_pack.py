from __future__ import annotations

import json
from pathlib import Path


def test_composio_catalog_in_mcp_harness(tmp_path):
    from distr.core.mcp_harness import collect_mcp_catalog

    catalog = collect_mcp_catalog()
    assert "composio_connect" in catalog
    assert "composio_rube" not in catalog
    assert catalog["composio_connect"]["auto_merge"] is True


def test_merge_adds_composio_connect_when_credentials_are_available(tmp_path, monkeypatch):
    from distr.core.mcp_harness import recalibrate_mcp_harness

    monkeypatch.setenv("COMPOSIO_API_KEY", "test-composio-key")

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
    assert servers["composio"]["headers"]["x-consumer-api-key"] == "test-composio-key"


def test_native_composio_installs_and_refreshes_key_without_duplicates(tmp_path, monkeypatch):
    from distr.core.composio_pack import composio_mcp_recommendations
    from distr.core.mcp_harness import _merge_decisions_mcp
    from distr.core.mcp.config import load_mcp_config

    monkeypatch.setenv("COMPOSIO_API_KEY", "first-consumer-key")
    catalog = composio_mcp_recommendations()
    assert _merge_decisions_mcp(tmp_path, catalog) == ["composio"]
    monkeypatch.setenv("COMPOSIO_API_KEY", "rotated-consumer-key")
    assert _merge_decisions_mcp(tmp_path, catalog) == ["composio:headers"]
    assert _merge_decisions_mcp(tmp_path, catalog) == []
    servers = load_mcp_config(tmp_path / ".decisions/models/mcp_config.json").servers
    assert len(servers) == 1
    assert dict(servers[0].headers) == {"x-consumer-api-key": "rotated-consumer-key"}


def test_consumer_key_replaces_invalid_legacy_headers(monkeypatch):
    from distr.core.mcp_harness import _apply_settings_api_key
    from distr.core.composio_pack import composio_mcp_recommendations

    monkeypatch.setenv("COMPOSIO_API_KEY", "consumer-key")
    cfg = {"url": "https://connect.composio.dev/mcp", "headers": {"x-api-key": "old", "Authorization": "Bearer wrong", "custom": "preserve"}}
    result = _apply_settings_api_key(cfg, composio_mcp_recommendations()["composio_connect"])
    assert result["headers"] == {"x-consumer-api-key": "consumer-key", "custom": "preserve"}
    assert cfg["headers"]["x-api-key"] == "old"


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
