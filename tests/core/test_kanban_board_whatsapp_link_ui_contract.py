from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
KANBAN_TEMPLATE = ROOT / "distr/gui/web/templates/kanban/kanban.html"
KANBAN_JS = ROOT / "distr/gui/web/static/kanban/js/kanban.js"
BOARD_JS = ROOT / "distr/gui/web/static/kanban/js/kanban_board.js"
WHATSAPP_JS = ROOT / "distr/gui/web/static/kanban/js/kanban_whatsapp.js"


def test_board_advanced_whatsapp_link_supports_multiple_chat_selector():
    template = KANBAN_TEMPLATE.read_text()
    kanban_js = KANBAN_JS.read_text()
    board_js = BOARD_JS.read_text()
    whatsapp_js = WHATSAPP_JS.read_text()

    assert "kb-bm-wa-chat-select" in template
    assert "multiple size=\"5\"" in template
    assert "Chat Group" in template
    assert "kb-bm-wa-refresh-candidates" in template
    assert "kb-bm-wa-chat-select-wrap" in template
    assert "kb-bm-wa-link-dot" not in template
    assert "kb-custom-select" in template
    assert "kanban_custom_select.js" in template
    assert "kb-bm-wa-links" not in template
    assert "Unlink" not in template
    assert "Linked to" not in template
    assert "kb-bm-wa-person-select" not in template
    assert "Person in Group" not in template
    assert "kb-bm-wa-add-btn" not in template
    assert "kb-wa-msgctx-link" in template
    assert "Link to Board" in template
    assert "kb-wa-msgctx-delete" in template
    assert "Delete Message" in template

    assert "document.getElementById(\"kb-bm-wa-add-btn\")" not in kanban_js
    assert "saveSelectedBoardWaLink" in kanban_js
    assert "saveSelectedBoardWaLink(boardId)" in board_js
    assert "saveSelectedWaLinkForBoard" in whatsapp_js
    assert "selectedOptions" in whatsapp_js
    assert "Promise.all(removals.concat(additions))" in whatsapp_js
    assert "unlinkAllBoardWaLinks" in whatsapp_js
    assert "return unlinkAllBoardWaLinks(boardId).then" not in whatsapp_js
    assert "handleBoardWaChatSelectChange" in whatsapp_js
    assert "Linked - " not in whatsapp_js
    assert "opt.selected = isLinked" in whatsapp_js
    assert "kb-bm-wa-link-dot" not in whatsapp_js
    assert "setWaChatLinkContext" in whatsapp_js
    assert "waMsgCtxLinkToBoard" in whatsapp_js
    assert "openWaChatContextMenuFromRow" in whatsapp_js
    assert "kb-wa-msgctx-link" in kanban_js
    assert "KanbanCustomSelect" in board_js
