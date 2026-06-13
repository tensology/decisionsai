"""Tests for server-side RTK shell integration."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from distr.core import rtk_support


@pytest.fixture(autouse=True)
def reset_rtk_cache():
    rtk_support._rtk_available = None
    yield
    rtk_support._rtk_available = None


def test_rtk_disabled_by_env():
    with patch.dict("os.environ", {"DECISIONS_RTK_DISABLED": "1"}, clear=False):
        assert rtk_support.rtk_enabled() is False


def test_rewrite_shell_command_passthrough_when_rtk_missing():
    with patch.object(rtk_support.shutil, "which", return_value=None):
        assert rtk_support.rewrite_shell_command("git status") == "git status"


def test_rewrite_shell_command_uses_rtk_output():
    with patch.object(rtk_support.shutil, "which", return_value="/usr/local/bin/rtk"):
        completed = subprocess.CompletedProcess(
            args=["rtk", "rewrite", "git status"],
            returncode=0,
            stdout="rtk git status\n",
            stderr="",
        )
        with patch.object(rtk_support.subprocess, "run", return_value=completed) as run_mock:
            rewritten = rtk_support.rewrite_shell_command("git status")
        assert rewritten == "rtk git status"
        run_mock.assert_called_once()


def test_rewrite_shell_command_fails_open_on_error():
    with patch.object(rtk_support.shutil, "which", return_value="/usr/local/bin/rtk"):
        with patch.object(rtk_support.subprocess, "run", side_effect=OSError("boom")):
            assert rtk_support.rewrite_shell_command("npm test") == "npm test"


def test_run_shell_command_executes_rewritten_command():
    with patch.object(rtk_support.shutil, "which", return_value="/usr/local/bin/rtk"):
        rewrite = subprocess.CompletedProcess(
            args=["rtk", "rewrite", "git status"],
            returncode=0,
            stdout="rtk git status\n",
            stderr="",
        )
        executed = subprocess.CompletedProcess(
            args="rtk git status",
            returncode=0,
            stdout="clean\n",
            stderr="",
        )

        def _run_side_effect(cmd, **kwargs):
            if cmd[:2] == ["rtk", "rewrite"]:
                return rewrite
            assert cmd == "rtk git status"
            assert kwargs.get("shell") is True
            return executed

        with patch.object(rtk_support.subprocess, "run", side_effect=_run_side_effect) as run_mock:
            proc = rtk_support.run_shell_command("git status", cwd="/tmp/proj", timeout=30)
        assert proc.stdout == "clean\n"
        assert run_mock.call_count == 2
