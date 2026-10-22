"""harnesssrc 2026-10-22: shared installed-harness detection."""
import os

from distr.core.project_cli_backends import installed as inst


def test_which_hit_and_known_folder_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(inst.shutil, "which", lambda name: "/x/codex" if name == "codex" else None)
    exe = tmp_path / "opencode"
    exe.write_text("#!/bin/sh\n"); os.chmod(exe, 0o755)
    monkeypatch.setattr(inst, "_known_folders", lambda: [str(tmp_path)])
    rows = {r["id"]: r for r in inst.detect_harnesses()}
    assert rows["codex"]["installed"] and rows["codex"]["path"] == "/x/codex"
    assert rows["opencode"]["installed"] and rows["opencode"]["path"] == str(exe)
    assert not rows["cline"]["installed"]
    assert {r["id"] for r in inst.installed_harnesses()} == {"codex", "opencode"}


def test_non_executable_is_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(inst.shutil, "which", lambda name: None)
    (tmp_path / "cline").write_text("x")
    monkeypatch.setattr(inst, "_known_folders", lambda: [str(tmp_path)])
    assert inst.installed_harnesses() == []
