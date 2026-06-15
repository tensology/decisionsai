"""Apple Silicon guard for rubicon before pyautogui/mouseinfo import."""

from __future__ import annotations

import importlib.util
import platform
import site
import sys
from pathlib import Path

_APPLIED = False


def _site_package_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        key = str(path)
        if key not in seen and path.is_dir():
            seen.add(key)
            roots.append(path)

    _add(Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages")
    for entry in site.getsitepackages():
        _add(Path(entry))
    user_site = site.getusersitepackages()
    if user_site:
        _add(Path(user_site))
    return roots


def apply_rubicon_arm64_fix() -> None:
    """Pre-load rubicon.objc.types with arm64 flags before runtime imports."""
    global _APPLIED
    if _APPLIED or sys.platform != "darwin":
        return
    if platform.machine() != "arm64":
        return
    if "rubicon.objc.runtime" in sys.modules:
        return

    for base in _site_package_roots():
        types_file = base / "rubicon" / "objc" / "types.py"
        if not types_file.is_file():
            continue
        spec = importlib.util.spec_from_file_location("rubicon.objc.types", types_file)
        if spec is None or spec.loader is None:
            continue
        rubicon_types = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rubicon_types)
        rubicon_types.__arm__ = False
        rubicon_types.__arm64__ = True
        rubicon_types.__i386__ = False
        rubicon_types.__x86_64__ = False
        sys.modules["rubicon.objc.types"] = rubicon_types
        _APPLIED = True
        return
