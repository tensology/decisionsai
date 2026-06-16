from __future__ import annotations

from pathlib import Path


def test_merge_ytdlp_pre_chain():
    from distr.core.yt_dlp_pack import merge_ytdlp_pre_chain

    assert merge_ytdlp_pre_chain(["tdd-workflow"], project_folder="") == ["tdd-workflow"]
    chain = merge_ytdlp_pre_chain(["video-editing"], project_folder="")
    assert chain[0] == "decisions-yt-dlp"


def test_ytdlp_pack_projects_skill(tmp_path, monkeypatch):
    from distr.core.yt_dlp_pack import ensure_yt_dlp_pack_setup

    monkeypatch.setattr(
        "distr.core.yt_dlp_pack.detected_harnesses",
        lambda: {"codex": True, "cursor": False, "claude": False, "pi": False},
    )
    monkeypatch.setattr(
        "distr.core.yt_dlp_pack.ensure_ytdlp_package",
        lambda: {"installed": True, "method": "test"},
    )
    monkeypatch.setattr("distr.core.yt_dlp_pack.ytdlp_version", lambda: "2024.08.01")

    result = ensure_yt_dlp_pack_setup(home=tmp_path, run_full=False, install_package=False)
    skill = tmp_path / "plugins" / "decisions-codex" / "skills" / "decisions-yt-dlp" / "SKILL.md"
    assert skill.is_file()
    assert result["status"] == "configured"


def test_run_ytdlp_step_requires_url(monkeypatch):
    from distr.core.yt_dlp_support import run_ytdlp_step

    monkeypatch.setattr("distr.core.yt_dlp_support.is_ytdlp_available", lambda: True)
    out = run_ytdlp_step({"mode": "metadata"})
    assert out["passed"] is False
