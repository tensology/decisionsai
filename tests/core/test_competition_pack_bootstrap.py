from __future__ import annotations

from pathlib import Path


def test_competition_pack_bootstrap_projects_skills(tmp_path, monkeypatch):
    from distr.core.competition_pack import ensure_competition_pack_setup

    monkeypatch.setattr(
        "distr.core.competition_pack.shutil.which",
        lambda name: "/usr/local/bin/tool"
        if name in {"codex", "cursor", "cursor-agent", "node"}
        else None,
    )
    monkeypatch.setattr(
        "distr.core.competition_pack._install_codex_plugin_if_requested",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "distr.core.competition_pack._install_fallow_cli_if_requested",
        lambda **kwargs: {"installed": False, "reason": "test"},
    )

    result = ensure_competition_pack_setup(
        home=tmp_path,
        run_full=True,
        install_codex_plugin=False,
        install_fallow_cli=False,
    )

    assert result["vendor_ready"] is True
    harness = tmp_path / "plugins" / "decisions-codex" / "skills" / "decisions-competition-harness" / "SKILL.md"
    assert harness.exists()
    assert "Ponytail" in harness.read_text(encoding="utf-8")
    assert (tmp_path / "plugins" / "decisions-codex" / "skills" / "ponytail" / "SKILL.md").exists()
    assert (tmp_path / "plugins" / "decisions-codex" / "skills" / "fallow" / "SKILL.md").exists()
    assert (tmp_path / ".cursor" / "rules" / "decisions-ponytail.mdc").exists()


def test_merge_competition_pre_chain_adds_fallow_for_js_projects(tmp_path):
    from distr.core.competition_pack import merge_competition_pre_chain

    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    chain = merge_competition_pre_chain(["tdd-workflow"], project_folder=str(tmp_path))
    assert chain[:2] == ["ponytail", "fallow"]
    assert "tdd-workflow" in chain


def test_merge_competition_pre_chain_python_project_skips_fallow(tmp_path):
    from distr.core.competition_pack import merge_competition_pre_chain

    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    chain = merge_competition_pre_chain([], project_folder=str(tmp_path))
    assert chain == ["ponytail"]
