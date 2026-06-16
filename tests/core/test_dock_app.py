"""Tests for macOS dock launch preference helpers."""

import json
from pathlib import Path

import distr.core.dock_app as dock_app


def test_persist_and_resolve_app_bundle(tmp_path, monkeypatch):
    pref_path = tmp_path / "dock_launch.json"
    monkeypatch.setattr(dock_app, "DOCK_LAUNCH_PREFERENCE_PATH", pref_path)
    monkeypatch.setenv("DECISIONS_DOCK_APP", "1")
    monkeypatch.setenv("DECISIONS_APP_BUNDLE", str(tmp_path / "decisions.app"))
    (tmp_path / "decisions.app").mkdir()

    dock_app.persist_dock_launch_preference()
    data = json.loads(pref_path.read_text(encoding="utf-8"))
    assert data["dock"] is True
    assert data["app_bundle"] == str(tmp_path / "decisions.app")
    assert dock_app.resolve_app_bundle_path(tmp_path) == str(tmp_path / "decisions.app")


def test_wants_dock_icon_during_restart_without_env(monkeypatch, tmp_path):
    pref_path = tmp_path / "dock_launch.json"
    pref_path.write_text(json.dumps({"dock": True, "app_bundle": ""}), encoding="utf-8")
    monkeypatch.setattr(dock_app, "DOCK_LAUNCH_PREFERENCE_PATH", pref_path)
    monkeypatch.delenv("DECISIONS_DOCK_APP", raising=False)
    monkeypatch.setenv("DECISIONS_RESTARTING", "1")

    assert dock_app.wants_dock_icon(tmp_path) is True
