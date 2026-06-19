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
    assert 'KanbanTicket.source_label == "smoke-test"' not in cleanup
    assert "Project.name.like" not in cleanup
    assert "KanbanBoard.name.like" not in cleanup
