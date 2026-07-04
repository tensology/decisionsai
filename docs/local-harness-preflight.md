# Local Harness Preflight

Use this when you want to know whether the local DecisionsAI harness setup is healthy enough to run project/workflow handoffs.

## Daily Check

```bash
rtk python3 scripts/preflight_local.py
```

This runs:

- `scripts/harness_doctor.py` as a non-blocking health report.
- `scripts/verify_agent_harness_setup.py --quiet` to refresh local agent surfaces when available.
- Focused harness pytest coverage:
  - `tests/core/test_harness_operational_proof.py`
  - `tests/core/test_harness_event_intake.py`
  - `tests/core/test_agent_harness_setup.py`
  - `tests/core/test_codex_workflow_backend_regression.py`
  - `tests/core/step_runner`

The doctor is non-blocking by default because optional local tools may not be installed on every machine.

## Strict Doctor

```bash
rtk python3 scripts/preflight_local.py --strict-doctor
```

Use this when the local machine is expected to have every harness and projection configured. Missing or stale doctor items make the command fail.

## Smoke Fixture Lifecycle

```bash
rtk python3 scripts/preflight_local.py --smoke-fixture
```

This creates the workflow-loop smoke fixture and immediately cleans it up before running focused tests. The fixture touches the local database and a temporary project folder, so it is opt-in.

## JSON Output

```bash
rtk python3 scripts/preflight_local.py --json
```

Use JSON output for scripts or launchers that need structured status.

## Useful Variants

Skip setup refresh:

```bash
rtk python3 scripts/preflight_local.py --skip-setup
```

Run only doctor and setup checks:

```bash
rtk python3 scripts/preflight_local.py --skip-tests
```

Override focused pytest paths:

```bash
rtk python3 scripts/preflight_local.py --pytest-arg tests/core/test_harness_operational_proof.py
```
