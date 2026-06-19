from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SETUP_SCRIPT = ROOT / "scripts/setup_workflow_loop_smoke.py"
CLEANUP_SCRIPT = ROOT / "scripts/cleanup_workflow_loop_smoke.py"


def test_smoke_fixture_names_project_and_board_like_a_real_product():
    setup = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert 'DEFAULT_PROJECT_NAME = "Bean & Byte Coffee Co"' in setup
    assert 'DEFAULT_DOMAIN = "beanandbyte.test"' in setup
    assert 'name=project_name' in setup
    assert 'name=project_name,' in setup
    assert 'title": "Set up React frontend and Django backend infrastructure"' in setup
    assert 'title": "Build React frontend + Django backend scaffold"' not in setup
    assert 'React Django board' not in setup


def test_smoke_fixture_cleanup_uses_marker_not_product_names():
    setup = SETUP_SCRIPT.read_text(encoding="utf-8")
    cleanup = CLEANUP_SCRIPT.read_text(encoding="utf-8")

    assert 'SMOKE_MARKER = "[dai-smoke-loop-fixture]"' in setup
    assert 'SMOKE_MARKER = "[dai-smoke-loop-fixture]"' in cleanup
    assert "Project.description.like(marker_like)" in cleanup
    assert "KanbanBoard.description.like(marker_like)" in cleanup
    assert "AutoWorkflow.description.like(marker_like)" in cleanup
    assert "KanbanTicket.description.like(marker_like)" in cleanup
    assert "KanbanTicket).filter(KanbanTicket.id.in_(ticket_ids)).delete" in cleanup
    assert "KanbanLane).filter(KanbanLane.id.in_(lane_ids)).delete" in cleanup
    assert "AutoWorkflowStep).filter(AutoWorkflowStep.workflow_id.in_(workflow_ids)).delete" in cleanup
    assert "AutoWorkflowRun).filter(AutoWorkflowRun.id.in_(run_ids)).delete" in cleanup
    assert 'KanbanTicket.source_label == "smoke-test"' not in cleanup
    assert "Project.name.like" not in cleanup
    assert "KanbanBoard.name.like" not in cleanup


def test_smoke_setup_can_replace_existing_fixture_before_recreate():
    setup = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert 'parser.add_argument("--replace"' in setup
    assert 'cleanup_workflow_loop_smoke.py' in setup
    assert '"--marker", SMOKE_MARKER, "--yes"' in setup
