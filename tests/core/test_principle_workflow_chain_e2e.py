"""E2E contracts for the single canonical Development workflow."""

from __future__ import annotations

import json

import pytest

from distr.core.db.workflow import AutoWorkflowStep
from distr.core.workflow.loop_preset_loader import (
    list_preset_catalog_entries,
    load_bundle_by_slug,
)
from tests.core.workflow_e2e_harness import (
    apply_preset_to_workflow,
    cleanup_workflow_run_context,
    make_factory,
    start_preset_run,
)


DEVELOPMENT_SLUG = "development-ticket-to-implementation"


@pytest.fixture(autouse=True)
def _isolate_runs():
    cleanup_workflow_run_context()
    yield
    cleanup_workflow_run_context()


@pytest.fixture()
def workflow_factory(tmp_path):
    return make_factory(tmp_path, memory=False)


def test_only_development_preset_is_user_selectable():
    catalog = list_preset_catalog_entries()
    assert [entry["slug"] for entry in catalog] == [DEVELOPMENT_SLUG]
    assert load_bundle_by_slug("ideation-brief-to-board") is None
    assert load_bundle_by_slug("polish-verify-and-ship") is None
    assert load_bundle_by_slug("ship-pr-until-green") is None


def test_development_covers_plan_build_independent_review_correction_and_memory(
    workflow_factory,
):
    applied = apply_preset_to_workflow(workflow_factory, DEVELOPMENT_SLUG)
    with workflow_factory() as session:
        steps = (
            session.query(AutoWorkflowStep)
            .filter(AutoWorkflowStep.workflow_id == applied["workflow_id"])
            .order_by(AutoWorkflowStep.position.asc())
            .all()
        )
        assert [step.name for step in steps] == [
            "Understand ticket and acceptance criteria",
            "Create the implementation plan",
            "Implement the planned change",
            "Independently review and validate the change",
            "Correct defects found by validation",
            "Report, update ticket, and compact memory",
        ]
        configs = [json.loads(step.config or "{}") for step in steps]
        assert all(step.action_type == "send_to_project_cli" for step in steps)
        assert "playwright" in configs[3]["tools"]
        assert "browser_use" in configs[3]["tools"]
        assert "security" in (steps[3].instruction or "").lower()
        assert "ui work" in (steps[3].instruction or "").lower()
        assert configs[3]["model_policy"]["independent_from"] == "implementation"
        assert configs[-1]["expected_outputs"][-2:] == ["failed_attempts", "lessons"]
        assert steps[3].on_pass_goto == steps[5].id
        assert steps[3].on_fail_goto == steps[4].id
        assert steps[4].on_pass_goto == steps[3].id


def test_development_preset_runs_to_report_and_exits(workflow_factory, tmp_path):
    result = start_preset_run(
        workflow_factory,
        tmp_path,
        DEVELOPMENT_SLUG,
        timeout=45.0,
    )
    assert result["terminal"]["run"].status == "completed"
    assert result["terminal"]["run_data"]["loop_contract"]["max_iterations"] == 6
