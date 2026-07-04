#!/usr/bin/env python3
"""Run local DecisionsAI harness preflight checks.

This script composes the existing doctor/setup/smoke scripts into one command
for local development. It is intentionally conservative: the smoke fixture is
opt-in because it creates and deletes local DB/project artifacts.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TESTS = (
    "tests/core/test_harness_operational_proof.py",
    "tests/core/test_harness_event_intake.py",
    "tests/core/test_agent_harness_setup.py",
    "tests/core/test_codex_workflow_backend_regression.py",
    "tests/core/step_runner",
)


@dataclass(frozen=True)
class PreflightStep:
    name: str
    command: tuple[str, ...]
    required: bool = True
    always_run: bool = False


@dataclass
class StepResult:
    name: str
    command: tuple[str, ...]
    returncode: int
    required: bool
    always_run: bool

    @property
    def ok(self) -> bool:
        return self.returncode == 0 or not self.required


Runner = Callable[[PreflightStep], int]


def _python(root: Path) -> str:
    return sys.executable or "python3"


def build_steps(
    *,
    root: Path,
    home: Path | None = None,
    strict_doctor: bool = False,
    smoke_fixture: bool = False,
    skip_setup: bool = False,
    skip_tests: bool = False,
    pytest_args: Iterable[str] = DEFAULT_TESTS,
) -> list[PreflightStep]:
    """Return preflight steps in execution order."""
    python = _python(root)
    steps: list[PreflightStep] = [
        PreflightStep(
            "harness doctor",
            (
                python,
                str(root / "scripts" / "harness_doctor.py"),
                "--root",
                str(root),
                *(() if home is None else ("--home", str(home))),
            ),
            required=strict_doctor,
        ),
    ]
    if not skip_setup:
        steps.append(
            PreflightStep(
                "verify agent harness setup",
                (
                    python,
                    str(root / "scripts" / "verify_agent_harness_setup.py"),
                    "--root",
                    str(root),
                    "--quiet",
                ),
                required=True,
            )
        )
    if smoke_fixture:
        steps.append(
            PreflightStep(
                "create workflow loop smoke fixture",
                (
                    python,
                    str(root / "scripts" / "setup_workflow_loop_smoke.py"),
                    "--replace",
                ),
                required=True,
            )
        )
        steps.append(
            PreflightStep(
                "cleanup workflow loop smoke fixture",
                (
                    python,
                    str(root / "scripts" / "cleanup_workflow_loop_smoke.py"),
                    "--yes",
                ),
                required=True,
                always_run=True,
            )
        )
    if not skip_tests:
        steps.append(
            PreflightStep(
                "focused harness pytest",
                (
                    python,
                    "-m",
                    "pytest",
                    "-q",
                    *tuple(pytest_args),
                    "--tb=short",
                ),
                required=True,
            )
        )
    return steps


def _default_runner(step: PreflightStep) -> int:
    completed = subprocess.run(step.command, cwd=str(REPO_ROOT), check=False)
    return int(completed.returncode)


def _quiet_runner(step: PreflightStep) -> int:
    completed = subprocess.run(
        step.command,
        cwd=str(REPO_ROOT),
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return int(completed.returncode)


def run_preflight(steps: Iterable[PreflightStep], *, runner: Runner = _default_runner) -> list[StepResult]:
    """Execute steps and stop after the first required failure.

    Steps marked always_run still execute after a prior required failure. This
    lets smoke cleanup run even when fixture setup partially fails.
    """
    results: list[StepResult] = []
    blocked = False
    for step in steps:
        if blocked and not step.always_run:
            break
        code = runner(step)
        result = StepResult(step.name, step.command, code, step.required, step.always_run)
        results.append(result)
        if code != 0 and step.required:
            blocked = True
    return results


def _print_text(results: list[StepResult]) -> None:
    print("DecisionsAI local harness preflight")
    for result in results:
        if result.returncode == 0:
            state = "PASS"
        elif result.required:
            state = "FAIL"
        else:
            state = "WARN"
        print(f"- {state}: {result.name}")


def _json_payload(results: list[StepResult]) -> dict[str, object]:
    return {
        "ok": all(result.ok for result in results),
        "steps": [
            {
                "name": result.name,
                "command": list(result.command),
                "returncode": result.returncode,
                "required": result.required,
                "always_run": result.always_run,
                "ok": result.ok,
            }
            for result in results
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local DecisionsAI harness preflight checks.")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="DecisionsAI repository root.")
    parser.add_argument("--home", type=Path, default=None, help="Home directory for harness doctor checks.")
    parser.add_argument("--strict-doctor", action="store_true", help="Fail when harness_doctor reports missing/stale items.")
    parser.add_argument("--smoke-fixture", action="store_true", help="Create and clean the workflow-loop smoke fixture.")
    parser.add_argument("--skip-setup", action="store_true", help="Skip idempotent agent harness setup verification.")
    parser.add_argument("--skip-tests", action="store_true", help="Skip focused harness pytest checks.")
    parser.add_argument("--pytest-arg", action="append", default=[], help="Override focused pytest paths/args. Can be repeated.")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary.")
    args = parser.parse_args()

    root = args.root.resolve()
    pytest_args = tuple(args.pytest_arg) if args.pytest_arg else DEFAULT_TESTS
    steps = build_steps(
        root=root,
        home=args.home,
        strict_doctor=bool(args.strict_doctor),
        smoke_fixture=bool(args.smoke_fixture),
        skip_setup=bool(args.skip_setup),
        skip_tests=bool(args.skip_tests),
        pytest_args=pytest_args,
    )
    results = run_preflight(steps, runner=_quiet_runner if args.json else _default_runner)
    if args.json:
        print(json.dumps(_json_payload(results), indent=2))
    else:
        _print_text(results)
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
