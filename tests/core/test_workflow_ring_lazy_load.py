from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_ring_view_is_loaded_only_when_requested() -> None:
    main = (ROOT / "distr/gui/web/static/workflows/js/workflows.js").read_text(encoding="utf-8")
    template = (ROOT / "distr/gui/web/templates/workflows/workflows.html").read_text(encoding="utf-8")
    ring = (ROOT / "distr/gui/web/static/workflows/js/ring_view.js").read_text(encoding="utf-8")

    assert '<script src="/workflows/static/js/ring_view.js"' not in template
    assert 'script.src = "/workflows/static/js/ring_view.js"' in main
    assert 'window.DecisionsWorkflowRingView = { render: render, bind: bind }' in ring
    assert 'var workflowLoopViewMode = "list"' in main


def test_hidden_workspace_memory_and_presets_are_deferred() -> None:
    main = (ROOT / "distr/gui/web/static/workflows/js/workflows.js").read_text(encoding="utf-8")

    assert "function loadWorkflowWorkspaceMemory()" in main
    assert 'if (targetTab === "loop" || targetTab === "cli")' in main
    assert 'window.requestIdleCallback(checkPresetsExist' in main
