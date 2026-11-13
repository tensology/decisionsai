"""Exact OpenPencil snapshot DecisionsAI reads against.

OpenPencil (https://github.com/open-pencil/open-pencil, MIT) is a *reference*
dependency: a sibling clone under ``DECISIONS/reference/open-pencil`` plus the
CLI installed at the exact same version under
``DECISIONS/reference/open-pencil-cli``. Nothing is vendored into the product.

Never resolve the CLI through a dist-tag (npm ``latest`` is 0.16.0 and
``next`` moves); the bridge fails closed when the installed CLI or the
reference clone does not match this pin.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from distr.core.plugins.paths import open_pencil_cli_dir, open_pencil_reference_dir

UPSTREAM_URL = "https://github.com/open-pencil/open-pencil"
LICENSE = "MIT"
PIN_TAG = "v0.17.0-beta.4"
PIN_SHA = "9e43b6d3b26cea2badcf3707489da579696b9857"
PIN_RELEASED_AT = "2026-11-11T17:48:13Z"
PIN_CLI_PACKAGE = "@open-pencil/cli"
PIN_CLI_VERSION = "0.17.0-beta.4"
LATEST_NON_BETA = "v0.16.0"  # ffe2fc5798468988a5943c5f5737905c3c7a28b5, 2026-10-14

CLI_ENV_OVERRIDE = "DECISIONS_OPEN_PENCIL_CLI"


class OpenPencilBridgeError(RuntimeError):
    """Base error: the bridge refuses to return a partial or unpinned read."""

    code = "open_pencil_error"


class OpenPencilCliMissing(OpenPencilBridgeError):
    code = "cli_missing"


class OpenPencilPinMismatch(OpenPencilBridgeError):
    code = "pin_mismatch"


class OpenPencilInvalidDocument(OpenPencilBridgeError):
    code = "invalid_document"


@dataclass(frozen=True)
class PinStatus:
    ok: bool
    cli_path: str
    cli_version: str | None
    package_version: str | None
    reference_dir: str
    reference_sha: str | None
    problems: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "tag": PIN_TAG,
            "sha": PIN_SHA,
            "released_at": PIN_RELEASED_AT,
            "cli_package": PIN_CLI_PACKAGE,
            "cli_version_pinned": PIN_CLI_VERSION,
            "cli_version_found": self.cli_version,
            "cli_package_version_found": self.package_version,
            "cli_path": self.cli_path,
            "reference_dir": self.reference_dir,
            "reference_sha_found": self.reference_sha,
            "upstream": UPSTREAM_URL,
            "license": LICENSE,
            "latest_non_beta": LATEST_NON_BETA,
            "problems": list(self.problems),
        }


def cli_path() -> Path:
    override = os.environ.get(CLI_ENV_OVERRIDE, "").strip()
    if override:
        return Path(override).expanduser()
    return open_pencil_cli_dir() / "node_modules" / ".bin" / "openpencil"


def _installed_package_version() -> str | None:
    manifest = open_pencil_cli_dir() / "node_modules" / "@open-pencil" / "cli" / "package.json"
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("version") or "") or None
    except (OSError, ValueError):
        return None


def _reference_head_sha() -> str | None:
    head = open_pencil_reference_dir() / ".git" / "HEAD"
    try:
        value = head.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if value.startswith("ref:"):
        ref = open_pencil_reference_dir() / ".git" / value.split(" ", 1)[1].strip()
        try:
            return ref.read_text(encoding="utf-8").strip()
        except OSError:
            return None
    return value or None


def _cli_version(path: Path, timeout: float = 30.0) -> str | None:
    try:
        proc = subprocess.run(
            [str(path), "--version"], capture_output=True, text=True, timeout=timeout,
            env=cli_environment(), stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip().splitlines()[-1].strip() if proc.stdout.strip() else None


def cli_environment() -> dict:
    """Subprocess env for the CLI. Opt-out flags are harmless if unused.

    At this pin the CLI has no usage ping (the anonymous daily ping is in the
    desktop app builds, which Decisions does not install).
    """
    env = dict(os.environ)
    env["DO_NOT_TRACK"] = "1"
    env["OPENPENCIL_TELEMETRY"] = "0"
    env["NO_UPDATE_NOTIFIER"] = "1"
    env["NO_COLOR"] = "1"
    return env


def check_pin(*, require_reference: bool = True) -> PinStatus:
    path = cli_path()
    problems: list[str] = []
    version = None
    package_version = _installed_package_version() if not os.environ.get(CLI_ENV_OVERRIDE) else None
    if not path.exists():
        problems.append(f"OpenPencil CLI not found at {path}. Install it with `npm install` in {open_pencil_cli_dir()}.")
    else:
        version = _cli_version(path)
        if version != PIN_CLI_VERSION:
            problems.append(f"OpenPencil CLI reports {version!r}; DecisionsAI is pinned to {PIN_CLI_VERSION}.")
        if package_version is not None and package_version != PIN_CLI_VERSION:
            problems.append(f"Installed {PIN_CLI_PACKAGE} is {package_version}; pinned {PIN_CLI_VERSION}.")
    ref_sha = _reference_head_sha()
    if require_reference and ref_sha != PIN_SHA:
        problems.append(f"Reference clone {open_pencil_reference_dir()} is at {ref_sha!r}; pinned {PIN_TAG} {PIN_SHA}.")
    return PinStatus(
        ok=not problems, cli_path=str(path), cli_version=version, package_version=package_version,
        reference_dir=str(open_pencil_reference_dir()), reference_sha=ref_sha, problems=tuple(problems),
    )


def require_pin(*, require_reference: bool = True) -> PinStatus:
    status = check_pin(require_reference=require_reference)
    if not status.ok:
        cls = OpenPencilCliMissing if not Path(status.cli_path).exists() else OpenPencilPinMismatch
        raise cls("; ".join(status.problems))
    return status
