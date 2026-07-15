#!/usr/bin/env python3
"""Fail fast when DecisionsAI is run with an unsupported Python stack."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import sys
from dataclasses import asdict, dataclass


MIN_PYTHON = (3, 12, 8)
MAX_PYTHON = (3, 13, 0)
REQUIRED_IMPORTS = (
    "fastapi",
    "browser_use",
    "pydantic",
    "PyQt6",
    "sqlalchemy",
    "pipecat",
    "PIL",
    "playwright",
)
PACKAGE_NAMES = {
    "PyQt6": "PyQt6",
    "PIL": "Pillow",
    "pipecat": "pipecat-ai",
}


@dataclass(frozen=True)
class RuntimeReport:
    ok: bool
    python: str
    executable: str
    errors: tuple[str, ...]
    versions: dict[str, str]


def inspect_runtime() -> RuntimeReport:
    errors: list[str] = []
    version_info = tuple(sys.version_info[:3])
    if not (MIN_PYTHON <= version_info < MAX_PYTHON):
        errors.append(
            "DecisionsAI requires Python 3.12.8 or newer within the 3.12 series; "
            f"found {sys.version.split()[0]}"
        )

    versions: dict[str, str] = {}
    for module_name in REQUIRED_IMPORTS:
        try:
            importlib.import_module(module_name)
            package_name = PACKAGE_NAMES.get(module_name, module_name)
            versions[module_name] = importlib.metadata.version(package_name)
        except Exception as exc:
            errors.append(f"{module_name} is unavailable: {exc}")

    try:
        pipecat_version = tuple(
            int(part) for part in versions["pipecat"].split(".")[:3]
        )
        if not ((0, 0, 95) <= pipecat_version < (0, 0, 101)):
            errors.append(
                "pipecat must be >=0.0.95,<0.0.101; "
                f"found {versions['pipecat']}"
            )
    except (KeyError, TypeError, ValueError):
        pass

    try:
        pillow_major = int(versions["PIL"].split(".", 1)[0])
        if pillow_major >= 12:
            errors.append(
                "Pillow must be >=11.1,<12 for Pipecat compatibility; "
                f"found {versions['PIL']}"
            )
    except (KeyError, TypeError, ValueError):
        pass

    if versions.get("browser_use") != "0.11.13":
        errors.append(
            "browser-use must be 0.11.13 while Pipecat requires Pillow <12; "
            f"found {versions.get('browser_use', 'unavailable')}"
        )

    return RuntimeReport(
        ok=not errors,
        python=sys.version.split()[0],
        executable=sys.executable,
        errors=tuple(errors),
        versions=versions,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output.")
    args = parser.parse_args()
    report = inspect_runtime()
    if args.json:
        print(json.dumps(asdict(report), indent=2, sort_keys=True))
    else:
        state = "PASS" if report.ok else "FAIL"
        print(f"{state}: Python {report.python} ({report.executable})")
        for name, version in sorted(report.versions.items()):
            print(f"- {name}: {version}")
        for error in report.errors:
            print(f"- ERROR: {error}", file=sys.stderr)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
