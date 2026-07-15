from __future__ import annotations

from pathlib import Path


def test_community_skills_pack_projects(tmp_path, monkeypatch):
    from distr.core.community_skills_pack import ensure_community_skills_pack_setup

    monkeypatch.setattr(
        "distr.core.community_skills_pack.detected_harnesses",
        lambda: {"codex": True, "cursor": False, "claude": False, "pi": False},
    )
    result = ensure_community_skills_pack_setup(home=tmp_path, run_full=False)
    assert result["vendor_ready"] is True
    assert result["skill_count"] >= 10
    assert (tmp_path / "plugins" / "decisions-codex" / "skills" / "humanizer" / "SKILL.md").is_file()
    assert (tmp_path / "plugins" / "decisions-codex" / "skills" / "last30days" / "SKILL.md").is_file()
    assert (tmp_path / ".decisions" / "community-skills-pack-state.json").is_file()


def test_merge_community_adds_humanizer_for_content():
    from distr.core.community_skills_pack import merge_community_pre_chain

    chain = merge_community_pre_chain(["article-writing"], project_folder="")
    assert "humanizer" in chain
    assert "article-writing" in chain


def test_merge_community_adds_taste_skill_for_design_prompts():
    from distr.core.community_skills_pack import merge_community_pre_chain

    chain = merge_community_pre_chain(["build-a-redesign", "landing"], project_folder="")
    assert "design-taste-frontend" in chain
    assert "decisions-design-aesthetics" in chain
