from __future__ import annotations

from pathlib import Path


def test_user_data_root_is_platform_native(monkeypatch, tmp_path):
    from distr.core.paths import user_data_root

    monkeypatch.delenv("DECISIONS_DATA_DIR", raising=False)
    assert user_data_root(platform="darwin", home=tmp_path) == (
        tmp_path / "Library" / "Application Support" / "DecisionsAI"
    )
    assert user_data_root(platform="win32", home=tmp_path) == (
        tmp_path / "AppData" / "Local" / "DecisionsAI"
    )
    assert user_data_root(platform="linux", home=tmp_path) == (
        tmp_path / ".local" / "share" / "decisionsai"
    )


def test_explicit_data_root_wins_for_installed_smoke_tests(monkeypatch, tmp_path):
    from distr.core.paths import user_data_root

    target = tmp_path / "isolated-data"
    monkeypatch.setenv("DECISIONS_DATA_DIR", str(target))
    assert user_data_root(platform="darwin", home=Path("/ignored")) == target
