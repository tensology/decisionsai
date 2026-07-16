"""Operator-safe command-line behavior for the desktop entry point."""

from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_start_help_exits_before_application_initialization():
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "bin" / "start.py"), "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0
    assert "Usage:" in result.stdout
    assert "--backup-database PATH" in result.stdout
    assert "Starting Decisions" not in result.stdout
    assert "Pipecat" not in result.stdout
    assert result.stderr == ""
