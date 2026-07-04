from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "preflight_local.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("preflight_local", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_default_preflight_steps_are_safe_and_focused():
    mod = _load_module()
    root = Path("/repo/DecisionsAI")

    steps = mod.build_steps(root=root)

    assert [step.name for step in steps] == [
        "harness doctor",
        "verify agent harness setup",
        "focused harness pytest",
    ]
    assert steps[0].required is False
    assert "harness_doctor.py" in steps[0].command[1]
    assert "verify_agent_harness_setup.py" in steps[1].command[1]
    assert steps[2].command[1:4] == ("-m", "pytest", "-q")
    assert "tests/core/step_runner" in steps[2].command


def test_strict_doctor_makes_doctor_required():
    mod = _load_module()

    steps = mod.build_steps(root=Path("/repo/DecisionsAI"), strict_doctor=True)

    assert steps[0].name == "harness doctor"
    assert steps[0].required is True


def test_smoke_fixture_adds_create_and_cleanup_steps_before_tests():
    mod = _load_module()

    steps = mod.build_steps(root=Path("/repo/DecisionsAI"), smoke_fixture=True)

    names = [step.name for step in steps]
    assert names == [
        "harness doctor",
        "verify agent harness setup",
        "create workflow loop smoke fixture",
        "cleanup workflow loop smoke fixture",
        "focused harness pytest",
    ]
    assert "setup_workflow_loop_smoke.py" in steps[2].command[1]
    assert "--replace" in steps[2].command
    assert "cleanup_workflow_loop_smoke.py" in steps[3].command[1]
    assert "--yes" in steps[3].command
    assert steps[3].always_run is True


def test_non_strict_doctor_failure_is_warning_and_preflight_continues():
    mod = _load_module()
    steps = [
        mod.PreflightStep("doctor", ("doctor",), required=False),
        mod.PreflightStep("pytest", ("pytest",), required=True),
    ]
    codes = {"doctor": 1, "pytest": 0}
    seen = []

    def runner(step):
        seen.append(step.name)
        return codes[step.name]

    results = mod.run_preflight(steps, runner=runner)

    assert seen == ["doctor", "pytest"]
    assert [result.returncode for result in results] == [1, 0]
    assert all(result.ok for result in results)


def test_required_failure_stops_preflight():
    mod = _load_module()
    steps = [
        mod.PreflightStep("setup", ("setup",), required=True),
        mod.PreflightStep("pytest", ("pytest",), required=True),
    ]
    seen = []

    def runner(step):
        seen.append(step.name)
        return 7

    results = mod.run_preflight(steps, runner=runner)

    assert seen == ["setup"]
    assert len(results) == 1
    assert results[0].ok is False


def test_always_run_cleanup_runs_after_required_failure():
    mod = _load_module()
    steps = [
        mod.PreflightStep("create", ("create",), required=True),
        mod.PreflightStep("cleanup", ("cleanup",), required=True, always_run=True),
        mod.PreflightStep("pytest", ("pytest",), required=True),
    ]
    codes = {"create": 9, "cleanup": 0, "pytest": 0}
    seen = []

    def runner(step):
        seen.append(step.name)
        return codes[step.name]

    results = mod.run_preflight(steps, runner=runner)

    assert seen == ["create", "cleanup"]
    assert [result.returncode for result in results] == [9, 0]
    assert results[0].ok is False
    assert results[1].ok is True
