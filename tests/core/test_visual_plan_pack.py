from __future__ import annotations


def test_visual_plan_pack_projects_agent_watchdog(tmp_path, monkeypatch):
    from distr.core.visual_plan_pack import ensure_visual_plan_pack_setup

    monkeypatch.setattr(
        "distr.core.visual_plan_pack.detected_harnesses",
        lambda: {"codex": True, "cursor": True, "claude": False, "pi": False},
    )

    result = ensure_visual_plan_pack_setup(home=tmp_path, run_full=True)

    assert result["status"] == "configured"
    assert result["skill_count"] >= 4
    assert (
        tmp_path
        / "plugins"
        / "decisions-codex"
        / "skills"
        / "agent-watchdog"
        / "SKILL.md"
    ).is_file()
    assert (tmp_path / ".codex" / "skills" / "agent-watchdog" / "SKILL.md").is_file()
    assert (tmp_path / ".cursor" / "skills" / "agent-watchdog" / "SKILL.md").is_file()
    assert (tmp_path / ".codex" / "commands" / "agent-watchdog.md").is_file()
    assert (tmp_path / ".cursor" / "commands" / "agent-watchdog.md").is_file()


def test_visual_plan_pre_chain_routes_agent_audits():
    from distr.core.visual_plan_pack import merge_visual_plan_pre_chain

    chain = merge_visual_plan_pre_chain(["audit agent session"])

    assert chain[0] == "agent-watchdog"
