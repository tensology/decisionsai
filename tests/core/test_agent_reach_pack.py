from __future__ import annotations

from pathlib import Path

import pytest


def test_agent_reach_pack_projects_skills(tmp_path, monkeypatch):
    from distr.core.agent_reach_pack import ensure_agent_reach_pack_setup

    monkeypatch.setattr(
        "distr.core.agent_reach_pack.detected_harnesses",
        lambda: {"codex": True, "cursor": False, "claude": False, "pi": False},
    )
    monkeypatch.setattr(
        "distr.core.agent_reach_pack._ensure_cli_installed",
        lambda **kwargs: {"installed": False, "reason": "test"},
    )

    result = ensure_agent_reach_pack_setup(home=tmp_path, run_full=False, install_cli=False)

    assert result["vendor_ready"] is True
    skill = tmp_path / "plugins" / "decisions-codex" / "skills" / "agent-reach" / "SKILL.md"
    assert skill.is_file()
    assert "agent-reach doctor" in skill.read_text(encoding="utf-8")
    wrapper = tmp_path / "plugins" / "decisions-codex" / "skills" / "decisions-agent-reach" / "SKILL.md"
    assert wrapper.is_file()
    harness = (
        tmp_path
        / "plugins"
        / "decisions-codex"
        / "skills"
        / "decisions-agent-reach-harness"
        / "SKILL.md"
    )
    assert harness.is_file()


def test_merge_agent_reach_pre_chain_when_research(tmp_path):
    from distr.core.agent_reach_pack import merge_agent_reach_pre_chain

    chain = merge_agent_reach_pre_chain(["content-engine"], project_folder=str(tmp_path))
    assert chain == ["content-engine"]

    chain2 = merge_agent_reach_pre_chain(
        ["content-engine", "research-topic"],
        project_folder=str(tmp_path),
    )
    assert chain2[0] == "decisions-agent-reach"
    assert "agent-reach" in chain2


def test_reference_clone_exists():
    from distr.core.plugins import agent_reach_reference_dir

    ref = agent_reach_reference_dir()
    if not ref.is_dir():
        pytest.skip("optional agent-reach development reference clone is not present")
    assert ref.is_dir()
    assert (ref / "agent_reach" / "cli.py").is_file()
    assert (ref / "docs" / "README_en.md").is_file()
