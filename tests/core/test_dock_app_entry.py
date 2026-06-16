"""Tests for dock bundle entry path resolution."""

import importlib.util
from pathlib import Path


def _load_entry():
    path = Path(__file__).resolve().parents[2] / "bin" / "dock_app_entry.py"
    spec = importlib.util.spec_from_file_location("dock_app_entry", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_dock_app_entry_paths_from_env(monkeypatch, tmp_path):
    entry = _load_entry()
    app_root = tmp_path / "repo" / "decisions.app"
    project_root = tmp_path / "repo"
    monkeypatch.setenv("DECISIONS_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("DECISIONS_APP_BUNDLE", str(app_root))

    resolved_app, resolved_project = entry._project_paths()
    assert resolved_app == app_root
    assert resolved_project == project_root


def test_dock_app_entry_paths_from_bundle_executable(monkeypatch, tmp_path):
    entry = _load_entry()
    app_root = tmp_path / "repo" / "decisions.app"
    macos = app_root / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    launcher = macos / "decisions"
    launcher.write_text("", encoding="utf-8")
    monkeypatch.delenv("DECISIONS_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("DECISIONS_APP_BUNDLE", raising=False)
    monkeypatch.setattr(entry, "__file__", str(launcher))

    resolved_app, project_root = entry._project_paths()
    assert resolved_app == app_root
    assert project_root == tmp_path / "repo"
