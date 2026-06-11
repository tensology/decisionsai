from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_JS = ROOT / "distr/gui/web/static/workflows/js/workflows.js"


def test_workflows_board_lane_no_longer_renders_whatsapp_snapshot_button():
    js = WORKFLOWS_JS.read_text(encoding="utf-8")

    assert "wf-board-whatsapp-snapshot" not in js
    assert "isWhatsappIntakeColumn" not in js


def test_external_board_sync_keeps_spinner_until_cache_ready():
    js = WORKFLOWS_JS.read_text(encoding="utf-8")

    sync_block_start = js.index('if (selected.source !== "database" && data && data.cache_ready === false')
    sync_block = js[sync_block_start:sync_block_start + 500]
    assert 'renderWorkflowBoardSpinner("Syncing board tickets...")' in sync_block
    assert "renderWorkflowBoardTickets(data, selected" not in sync_block
    assert "if (attempt === 0)" in js
