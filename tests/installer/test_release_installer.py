from __future__ import annotations

import os
import plistlib
import stat
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = PROJECT_ROOT / "installer" / "install.sh"
VERIFIER = PROJECT_ROOT / "installer" / "verify_release.py"
SMOKE = PROJECT_ROOT / "installer" / "smoke_app.py"


def _fake_app(root: Path, version: str, *, identifier: str = "com.tensology.decisionsai") -> Path:
    app = root / f"DecisionsAI-{version}.app"
    executable = app / "Contents" / "MacOS" / "DecisionsAI"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    with (app / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump(
            {
                "CFBundleExecutable": "DecisionsAI",
                "CFBundleIdentifier": identifier,
                "CFBundleShortVersionString": version,
                "CFBundleVersion": version,
            },
            stream,
        )
    return app


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "DECISIONSAI_INSTALL_DIR": str(tmp_path / "Applications"),
        "DECISIONSAI_STATE_DIR": str(tmp_path / "state"),
    }


def _run(*args: str | Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(INSTALLER), *(str(arg) for arg in args)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS release lifecycle")
def test_install_update_and_rollback_are_atomic(tmp_path: Path):
    env = _env(tmp_path)
    first = _fake_app(tmp_path / "artifacts", "2.8.0")
    second = _fake_app(tmp_path / "artifacts", "2.8.1")

    assert _run(first, env=env).returncode == 0
    assert _run("--verify", env=env).stdout.strip() == "2.8.0"
    assert _run(second, env=env).returncode == 0
    assert _run("--verify", env=env).stdout.strip() == "2.8.1"

    rollback = _run("--rollback", env=env)
    assert rollback.returncode == 0, rollback.stderr
    assert _run("--verify", env=env).stdout.strip() == "2.8.0"
    assert not list((tmp_path / "Applications").glob(".DecisionsAI.*"))


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS release lifecycle")
def test_invalid_update_does_not_replace_installed_app(tmp_path: Path):
    env = _env(tmp_path)
    valid = _fake_app(tmp_path / "artifacts", "2.8.0")
    invalid = _fake_app(tmp_path / "artifacts", "9.9.9", identifier="invalid.example")
    assert _run(valid, env=env).returncode == 0

    rejected = _run(invalid, env=env)
    assert rejected.returncode != 0
    assert _run("--verify", env=env).stdout.strip() == "2.8.0"


def test_release_verifier_enforces_version_and_identity(tmp_path: Path):
    app = _fake_app(tmp_path, "2.8.0")
    valid = subprocess.run(
        [sys.executable, str(VERIFIER), str(app), "--version", "2.8.0"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert valid.returncode == 0, valid.stderr

    mismatch = subprocess.run(
        [sys.executable, str(VERIFIER), str(app), "--version", "2.8.1"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert mismatch.returncode != 0
    assert "expected version 2.8.1" in mismatch.stderr


def test_smoke_probe_uses_isolated_port_and_child_process_identity():
    source = SMOKE.read_text(encoding="utf-8")

    assert '"DECISIONS_WEB_PORT": str(web_port)' in source
    assert 'payload.get("pid") == process.pid' in source
    assert "127.0.0.1:8765" not in source


def test_bundled_executable_exposes_verified_database_maintenance():
    source = (PROJECT_ROOT / "bin" / "start.py").read_text(encoding="utf-8")
    assert "--backup-database" in source
    assert "--verify-database-backup" in source
    assert "--restore-database" in source
    assert "restore_database_backup(path)" in source
    assert "--restore-database requires --yes" in source
