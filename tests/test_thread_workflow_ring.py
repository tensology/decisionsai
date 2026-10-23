"""Static checks for the shared workflow Loop ring in the thread runner card."""
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "distr" / "gui" / "web"
DEV = WEB / "static" / "development"
SHARED_BUST = "v=20261022-threadring2"
WATCH_BUST = "v=20261023-workflowwatch1"
STEERING_BUST = "v=20261023-steering1"
APP_BUST = "v=20261023-terminal-parity1"


def test_shared_module_exists_and_exports():
    src = (DEV / "shared" / "workflow-loop.js").read_text()
    for name in ("export function workflowLoopHtml", "export function workflowViewSwitchHtml",
                 "export function readWorkflowViewMode", "export function writeWorkflowViewMode",
                 "decisions.workflowViewMode", "compact", "activeStepId", "idPrefix"):
        assert name in src


def test_both_pages_use_shared_module():
    for rel in ("workflows/index.js", "threads/transcript/index.js"):
        src = (DEV / rel).read_text()
        assert f"shared/workflow-loop.js?{SHARED_BUST}" in src
        assert "workflowViewSwitchHtml(" in src


def test_thread_card_toggle_markup():
    src = (DEV / "threads" / "transcript" / "index.js").read_text()
    assert "data-thread-workflow-view" in src
    assert 'data-view="${viewMode}"' in src
    assert "thread-workflow-ring" in src
    assert "workflowStepNumbersHtml(" in src and "bindWorkflowStepNumbers(" in src
    assert 'class="thread-workflow-nodes"' not in src  # flat strip replaced
    css = (DEV / "threads" / "styles.css").read_text()
    assert '.thread-workflow-runner[data-view="loop"] .thread-workflow-steps' in css
    assert ".workflow-loop-node.current" in css


def test_workflows_defaults_to_loop():
    src = (DEV / "shared" / "workflow-loop.js").read_text()
    assert "=== 'list' ? 'list' : 'loop'" in src
    assert "workflowViewMode: readWorkflowViewMode()" in (DEV / "workflows" / "index.js").read_text()


def test_cache_busters_bumped():
    assert f"threads/styles.css?{STEERING_BUST}" in (DEV / "styles.css").read_text()
    app = (DEV / "app.js").read_text()
    assert f"workflows/index.js?{SHARED_BUST}" in app and f"threads/transcript/index.js?{WATCH_BUST}" in app
    html = (WEB / "templates" / "development" / "index.html").read_text()
    assert f"development/styles.css?{STEERING_BUST}" in html and f"development/app.js?{APP_BUST}" in html


def test_numbered_steps_shared_and_keyboard():
    src = (DEV / "shared" / "workflow-loop.js").read_text()
    for name in ("export function workflowStepNumbersHtml", "export function bindWorkflowStepNumbers",
                 "ArrowRight", "Home", "End", "Awaiting gate", "data-step-output", "role=\"tabpanel\""):
        assert name in src
    assert "workflowStepNumbersHtml(" in (DEV / "workflows" / "index.js").read_text()
    assert ".workflow-step-number-item.current" in (DEV / "shared" / "components.css").read_text()
