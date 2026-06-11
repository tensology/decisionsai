from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_JS = ROOT / "distr/gui/web/static/workflows/js/workflows.js"


def test_workflows_board_lane_no_longer_renders_whatsapp_snapshot_button():
    js = WORKFLOWS_JS.read_text(encoding="utf-8")

    assert "wf-board-whatsapp-snapshot" not in js
    assert "isWhatsappIntakeColumn" not in js
