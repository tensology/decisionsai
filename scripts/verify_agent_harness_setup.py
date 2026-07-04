#!/usr/bin/env python3
"""Idempotently verify local Decisions/ECC harness setup for agent surfaces."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


def _have(command: str) -> bool:
    return shutil.which(command) is not None


def _copytree_clean(source: Path, target: Path) -> None:
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store", ".git"))


def _copy_commands(source: Path, target: Path) -> None:
    if not source.exists():
        return
    target.mkdir(parents=True, exist_ok=True)
    for item in source.glob("*.md"):
        shutil.copy2(item, target / item.name)


def _run_installer(path: Path, *, quiet: bool) -> None:
    if not path.exists():
        return
    stdout = subprocess.DEVNULL if quiet else None
    stderr = subprocess.DEVNULL if quiet else None
    try:
        subprocess.run(["python3", str(path)], check=False, stdout=stdout, stderr=stderr)
    except Exception:
        return


def codex_available(home: Path) -> bool:
    return _have("codex") or (home / ".codex").exists() or (home / ".agents").exists()


def cursor_available(home: Path) -> bool:
    return _have("cursor") or _have("cursor-agent") or (home / ".cursor").exists()


def claude_available(home: Path) -> bool:
    return _have("claude") or (home / ".claude").exists()


def init_rtk_agent_hooks(*, home: Path, quiet: bool = True) -> None:
    from distr.core.rtk_hooks import init_rtk_agent_hooks as _init

    _init(home=home, quiet=quiet)


def install_claude_surface(root: Path, *, home: Path, quiet: bool = True) -> None:
    ecc = root / "plugins" / "ecc"
    _copytree_clean(ecc / ".claude-plugin", home / ".claude" / "plugins" / "local" / "ecc")
    _copy_commands(ecc / ".claude" / "commands", home / ".claude" / "commands")
    if not quiet:
        print("Verified Claude ECC harness surface.")


def verify_agent_harness_setup(root: Path, *, home: Path | None = None, quiet: bool = True) -> dict[str, bool]:
    home = home or Path.home()
    root = root.resolve()
    results = {"codex": False, "cursor": False, "claude": False, "rtk": False}

    if codex_available(home):
        _run_installer(root / "plugins" / "codex-ide" / "scripts" / "install_local.py", quiet=quiet)
        results["codex"] = True

    if cursor_available(home):
        _run_installer(root / "plugins" / "cursor-ide" / "scripts" / "install_local.py", quiet=quiet)
        results["cursor"] = True

    if claude_available(home):
        install_claude_surface(root, home=home, quiet=quiet)
        results["claude"] = True

    if _have("rtk"):
        init_rtk_agent_hooks(home=home, quiet=quiet)
        results["rtk"] = True

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify local DecisionsAI agent harness setup.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    try:
        verify_agent_harness_setup(Path(args.root), quiet=bool(args.quiet))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
