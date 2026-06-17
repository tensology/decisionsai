"""RTK token-proxy helpers for DecisionsAI server-side shell execution.

RTK compresses common dev-command output before it reaches LLM context.
Agent CLIs pick up hooks via ``scripts/setup_project_clis.sh``; this module
covers workflow ``run_command`` steps and other server subprocess paths that
bypass agent bash hooks.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from typing import Sequence

_ACCEPT_REWRITE_EXIT_CODES = frozenset({0, 3})

_rtk_available: bool | None = None


def rtk_enabled() -> bool:
    """Return whether RTK should be used for server-side command rewrite."""
    global _rtk_available
    if _rtk_available is None:
        disabled = (os.environ.get("DECISIONS_RTK_DISABLED") or "").strip().lower()
        if disabled in {"1", "true", "yes"}:
            _rtk_available = False
        else:
            _rtk_available = shutil.which("rtk") is not None
    return _rtk_available


def rewrite_shell_command(command: str, *, timeout: float = 2.0) -> str:
    """Return an RTK-rewritten shell command when supported; else the original."""
    command = (command or "").strip()
    if not command or not rtk_enabled():
        return command
    try:
        result = subprocess.run(
            ["rtk", "rewrite", command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return command
    if result.returncode in _ACCEPT_REWRITE_EXIT_CODES:
        rewritten = (result.stdout or "").strip()
        if rewritten:
            return rewritten
    return command


def run_shell_command(
    command: str,
    *,
    cwd: str | None = None,
    timeout: int | float = 60,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a shell command, rewriting through RTK when installed."""
    effective = rewrite_shell_command(command)
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(
        effective,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        env=merged_env,
    )


def run_argv_command(
    argv: Sequence[str],
    *,
    cwd: str | None = None,
    timeout: int | float = 8,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run an argv command via shell rewrite (for git, pytest, etc.)."""
    return run_shell_command(shlex.join(list(argv)), cwd=cwd, timeout=timeout, env=env)


def init_rtk_agent_hooks(*, quiet: bool = True) -> bool:
    """Run ``rtk init`` for installed coding agents when RTK is on PATH."""
    if not rtk_enabled():
        return False
    auto_flags = ["--auto-patch"]
    claude_flags = ["--auto-patch"]
    if (os.environ.get("NONINTERACTIVE") or "").strip() == "1":
        claude_flags = ["--hook-only"]

    env = {**os.environ}
    env.setdefault("RTK_TELEMETRY_DISABLED", "1")
    stdout = subprocess.DEVNULL if quiet else None
    stderr = subprocess.DEVNULL if quiet else None

    def _init(args: list[str]) -> None:
        try:
            subprocess.run(
                ["rtk", "init", "-g", *args],
                check=False,
                env=env,
                stdout=stdout,
                stderr=stderr,
                timeout=120,
            )
        except Exception:
            return

    home = os.path.expanduser("~")
    if shutil.which("claude") or os.path.isdir(os.path.join(home, ".claude")):
        _init(claude_flags)
    if shutil.which("codex") or os.path.isdir(os.path.join(home, ".codex")) or os.path.isdir(
        os.path.join(home, ".agents")
    ):
        _init(["--codex"])
    if shutil.which("cursor-agent") or shutil.which("cursor") or os.path.isdir(
        os.path.join(home, ".cursor")
    ):
        _init(["--agent", "cursor", *auto_flags])
    if shutil.which("pi"):
        _init(["--agent", "pi", *auto_flags])
    if shutil.which("hermes") or os.path.isdir(os.path.join(home, ".hermes")):
        _init(["--agent", "hermes", *auto_flags])
    if shutil.which("cline") or os.path.isdir(os.path.join(home, ".cline")):
        _init(["--agent", "cline", *auto_flags])
    return True
