"""Parametrized E2E matrix: every loop preset applies, runs, and exits with fakes."""

from __future__ import annotations

import json

import pytest

from distr.core.workflow.loop_preset_loader import load_bundle_by_slug
from tests.core.workflow_e2e_harness import (
    all_preset_slugs,
    apply_preset_to_workflow,
    assert_preset_harness_fields,
    cleanup_workflow_run_context,
    load_exit_contracts,
    make_factory,
    start_preset_run,
)


@pytest.fixture(autouse=True)
def _isolate_matrix_workflow_runs():
    cleanup_workflow_run_context()
    yield
    cleanup_workflow_run_context()


DEVELOPMENT_SLUG = "development-ticket-to-implementation"


def runnable_preset_slugs() -> list[str]:
    return all_preset_slugs()


@pytest.fixture()
def matrix_factory(tmp_path):
    return make_factory(tmp_path)


@pytest.mark.parametrize("preset_slug", all_preset_slugs())
def test_preset_exit_contract_fixture_matches_bundle(preset_slug):
    contracts = load_exit_contracts()
    assert preset_slug in contracts, f"missing exit contract for {preset_slug}"
    bundle = load_bundle_by_slug(preset_slug)
    assert bundle is not None
    contract = contracts[preset_slug]
    steps = bundle.get("steps") or []
    assert contract["step_count"] == len(steps)
    assert contract["final_step_name"] == steps[-1]["name"]
    lc = bundle.get("loop_contract") or {}
    assert contract["max_iterations"] == lc.get("max_iterations")


@pytest.mark.parametrize("preset_slug", all_preset_slugs())
def test_preset_applies_with_harness_fields(matrix_factory, tmp_path, preset_slug):
    factory = matrix_factory
    applied = apply_preset_to_workflow(factory, preset_slug)
    assert_preset_harness_fields(factory, applied["workflow_id"])

    session = factory()
    try:
        from distr.core.db.workflow import AutoWorkflow

        wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == applied["workflow_id"]).one()
        wf_input = json.loads(wf.workflow_input or "{}")
        assert wf_input.get("loop_contract") or wf_input.get("goal")
        assert wf.description
    finally:
        session.close()


@pytest.mark.parametrize("preset_slug", runnable_preset_slugs())
def test_preset_runs_and_exits(matrix_factory, tmp_path, preset_slug):
    result = start_preset_run(matrix_factory, tmp_path, preset_slug, timeout=120.0)
    contracts = load_exit_contracts()
    contract = contracts[preset_slug]
    run_data = result["terminal"]["run_data"]
    assert run_data.get("loop_contract", {}).get("max_iterations") == contract["max_iterations"]
    assert result["terminal"]["run"].status == "completed"


@pytest.mark.parametrize(
    "preset_slug",
    [s for s in runnable_preset_slugs() if "playwright" in (load_exit_contracts().get(s) or {}).get("tools", [])],
)
def test_preset_with_playwright_tools_exits(matrix_factory, tmp_path, preset_slug):
    start_preset_run(matrix_factory, tmp_path, preset_slug, timeout=90.0)


def test_development_handoff_retains_loop_context(matrix_factory, tmp_path):
    result = start_preset_run(
        matrix_factory,
        tmp_path,
        DEVELOPMENT_SLUG,
        timeout=120.0,
    )
    assert result["terminal"]["run"].status == "completed"
    assert result["loop_context_seen"] is True
