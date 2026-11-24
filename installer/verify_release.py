#!/usr/bin/env python3
"""Fail-closed validation for a DecisionsAI macOS app artifact."""

from __future__ import annotations

import argparse
import plistlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from distr.core.skin_discovery import BUILTIN_SKIN_IDS, get_skin_by_name


BUNDLE_IDENTIFIER = "com.tensology.decisionsai"


def validate_bundled_skins(avatars: Path) -> None:
    for skin_id in BUILTIN_SKIN_IDS:
        skin = get_skin_by_name(avatars, skin_id)
        if skin is None:
            raise ValueError(f"app bundle has a missing or invalid built-in skin: {skin_id}")
        _, config = skin
        animations = {response.animation for response in config.events.values()}
        animations.update(config.transitions.values())
        missing = sorted(name for name in animations if not (avatars / skin_id / name).is_file())
        if missing:
            raise ValueError(f"built-in skin {skin_id} is missing animations: {', '.join(missing)}")


def validate_app(app: Path, *, version: str | None, require_signature: bool) -> str:
    if not app.is_dir():
        raise ValueError(f"app bundle not found: {app}")
    plist_path = app / "Contents" / "Info.plist"
    executable = app / "Contents" / "MacOS" / "DecisionsAI"
    if not plist_path.is_file():
        raise ValueError("app bundle is missing Contents/Info.plist")
    if not executable.is_file() or not executable.stat().st_mode & 0o111:
        raise ValueError("app bundle is missing an executable DecisionsAI launcher")
    with plist_path.open("rb") as stream:
        metadata = plistlib.load(stream)
    if metadata.get("CFBundleIdentifier") != BUNDLE_IDENTIFIER:
        raise ValueError("app bundle identifier is not com.tensology.decisionsai")
    actual_version = str(metadata.get("CFBundleShortVersionString") or "")
    if not actual_version:
        raise ValueError("app bundle has no release version")
    if version is not None and actual_version != version:
        raise ValueError(f"expected version {version}, found {actual_version}")
    validate_bundled_skins(app / "Contents" / "Resources" / "assets" / "avatars")
    if require_signature:
        subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)],
            check=True,
        )
    return actual_version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("app", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--require-signature", action="store_true")
    args = parser.parse_args()
    try:
        actual_version = validate_app(
            args.app.resolve(),
            version=args.version,
            require_signature=args.require_signature,
        )
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"release verification failed: {exc}\n")
    print(f"verified DecisionsAI {actual_version}: {args.app.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
