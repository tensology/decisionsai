from __future__ import annotations

from pathlib import Path


def test_capabilities_pack_projects_browser_skills(tmp_path, monkeypatch):
    from distr.core.capabilities_pack import ensure_capabilities_pack_setup

    monkeypatch.setattr(
        "distr.core.capabilities_pack.detected_harnesses",
        lambda: {"codex": True, "cursor": True, "claude": False, "pi": False},
    )
    monkeypatch.setattr(
        "distr.core.capabilities_pack._ensure_playwright_browsers",
        lambda: {"ok": True, "skipped": True},
    )
    monkeypatch.setattr(
        "distr.core.capabilities_pack._ensure_browser_use_package",
        lambda **kwargs: {"installed": False, "reason": "test"},
    )

    result = ensure_capabilities_pack_setup(
        home=tmp_path,
        run_full=True,
        install_browser_use=False,
    )

    assert result["status"] == "configured"
    assert result["skill_count"] >= 3
    harness = (
        tmp_path
        / "plugins"
        / "decisions-codex"
        / "skills"
        / "decisions-browser-content-harness"
        / "SKILL.md"
    )
    assert harness.is_file()
    assert "Playwright" in harness.read_text(encoding="utf-8")
    assert (tmp_path / "plugins" / "decisions-codex" / "skills" / "browser-qa" / "SKILL.md").is_file()
    assert (tmp_path / "plugins" / "decisions-codex" / "skills" / "decisions-playwright" / "SKILL.md").is_file()
    assert (tmp_path / ".decisions" / "harness" / "mcp-recommendations.json").is_file()


def test_merge_browser_content_pre_chain_includes_baseline(tmp_path):
    from distr.core.capabilities_pack import merge_browser_content_pre_chain

    chain = merge_browser_content_pre_chain(["tdd-workflow"], project_folder=str(tmp_path))
    assert chain[0] == "decisions-harness-stack"
    assert "decisions-design-references" in chain
    assert "ponytail" in chain
    assert "tdd-workflow" in chain


def test_harness_stack_runs_all_packs(tmp_path, monkeypatch):
    from distr.core.harness_stack import ensure_harness_stack_setup

    monkeypatch.setattr(
        "distr.core.harness_pack._detected_harnesses",
        lambda: {"codex": True, "cursor": False, "claude": False, "pi": False},
    )
    monkeypatch.setattr(
        "distr.core.competition_pack._detected_harnesses",
        lambda: {"codex": True, "cursor": False, "claude": False, "pi": False},
    )
    monkeypatch.setattr(
        "distr.core.capabilities_pack.detected_harnesses",
        lambda: {"codex": True, "cursor": False, "claude": False, "pi": False},
    )
    monkeypatch.setattr(
        "distr.core.harness_pack._install_codex_plugin_if_requested",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "distr.core.harness_pack._install_editor_extension_if_requested",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "distr.core.competition_pack._install_codex_plugin_if_requested",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "distr.core.competition_pack._install_fallow_cli_if_requested",
        lambda **kwargs: {"installed": False},
    )
    monkeypatch.setattr(
        "distr.core.capabilities_pack._ensure_playwright_browsers",
        lambda: {"ok": True},
    )
    monkeypatch.setattr(
        "distr.core.capabilities_pack._ensure_browser_use_package",
        lambda **kwargs: {"installed": False},
    )
    monkeypatch.setattr("distr.core.rtk_hooks.init_rtk_agent_hooks", lambda **kwargs: None)

    stack = ensure_harness_stack_setup(
        home=tmp_path,
        run_full=True,
        init_rtk_hooks=True,
        install_editor_extension=False,
    )

    assert "ecc" in stack and "competition" in stack and "capabilities" in stack
    assert (tmp_path / "plugins" / "decisions-codex" / "skills" / "ecc-harness-pack" / "SKILL.md").is_file()
    assert (tmp_path / ".codex" / "commands" / "decisions-harness-audit.md").is_file()
