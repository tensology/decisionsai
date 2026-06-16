"""RTK hook initialization for installed agent harnesses."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def init_rtk_agent_hooks(*, home: Path | None = None, quiet: bool = True) -> None:
    if not shutil.which("rtk"):
        return
    base_home = Path(home).expanduser() if home is not None else Path.home()
    stdout = subprocess.DEVNULL if quiet else None
    stderr = subprocess.DEVNULL if quiet else None
    env = {**os.environ}
    env.setdefault("RTK_TELEMETRY_DISABLED", "1")
    auto_flags = ["--auto-patch"]
    claude_flags = ["--auto-patch"]
    if (env.get("NONINTERACTIVE") or "").strip() == "1":
        claude_flags = ["--hook-only"]

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

    if shutil.which("claude") or (base_home / ".claude").exists():
        _init(claude_flags)
    if shutil.which("codex") or (base_home / ".codex").exists() or (base_home / ".agents").exists():
        _init(["--codex"])
    if shutil.which("cursor-agent") or shutil.which("cursor") or (base_home / ".cursor").exists():
        _init(["--agent", "cursor", *auto_flags])
    if shutil.which("pi"):
        _init(["--agent", "pi", *auto_flags])
    if shutil.which("hermes") or (base_home / ".hermes").exists():
        _init(["--agent", "hermes", *auto_flags])
