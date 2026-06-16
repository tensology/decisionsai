"""Tests for detached desktop restart helpers."""

from pathlib import Path

import distr.core.app_restart as app_restart


def test_build_restart_shell_script_includes_skip_kill_and_sleep(tmp_path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "start.py").write_text("print('ok')\n", encoding="utf-8")
    script = app_restart.build_restart_shell_script(
        project_root=tmp_path,
        python_path="/Users/me/.virtualenvs/decisions/bin/python",
    )
    assert "#!/bin/bash" in script
    assert "sleep 5" in script
    assert f"cd {tmp_path}" in script.replace("'", "")
    assert "--skip-kill-existing" in script
    assert "bin/start.py" in script
    assert "DECISIONS_RESTARTING=1" in script
    assert str(app_restart.restart_log_path()) in script


def test_resolve_restart_python_prefers_existing_sys_executable(tmp_path, monkeypatch):
    python_path = tmp_path / "python"
    python_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(app_restart.sys, "executable", str(python_path))
    resolved = app_restart.resolve_restart_python(tmp_path)
    assert resolved == str(python_path)


def test_build_restart_shell_script_uses_app_bundle_when_present(tmp_path):
    bundle = tmp_path / "decisions.app"
    bundle.mkdir()
    script = app_restart.build_restart_shell_script(
        project_root=tmp_path,
        python_path="/Users/me/.virtualenvs/decisions/bin/python",
        dock_app_bundle=str(bundle),
    )
    assert "/usr/bin/open -n" in script
    assert str(bundle) in script
    assert "start.py" not in script


def test_spawn_restart_process_writes_script_on_macos(monkeypatch, tmp_path):
    calls: list[dict] = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(app_restart.sys, "platform", "darwin")
    monkeypatch.setattr(app_restart.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(app_restart.os, "chmod", lambda *args, **kwargs: None)
    monkeypatch.setattr(app_restart.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(app_restart, "resolve_project_root", lambda: tmp_path)
    monkeypatch.setattr(app_restart, "resolve_restart_python", lambda _root: str(tmp_path / "python"))
    monkeypatch.setattr(app_restart, "resolve_app_bundle_path", lambda _root: "")
    monkeypatch.setattr(app_restart, "restart_script_path", lambda: tmp_path / "restart_decisions.sh")
    monkeypatch.setattr(app_restart, "restart_log_path", lambda: tmp_path / "restart.log")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "start.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "python").write_text("", encoding="utf-8")

    app_restart.spawn_restart_process(project_root=tmp_path, python_path=str(tmp_path / "python"))

    assert app_restart.restart_pending_path().is_file()
    script = (tmp_path / "restart_decisions.sh").read_text(encoding="utf-8")
    assert "--skip-kill-existing" in script
    assert len(calls) == 1
    assert calls[0]["args"] == ["nohup", "/bin/bash", str(tmp_path / "restart_decisions.sh")]
