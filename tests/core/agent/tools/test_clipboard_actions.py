from unittest.mock import patch

from distr.core.agent.tools.clipboard.clipboard_actions import ClipboardActionTool


def test_clipboard_action_extracts_set_content():
    assert ClipboardActionTool._extract_set_content("set clipboard to hello world") == "hello world"
    assert ClipboardActionTool._extract_set_content("write hello world into my clipboard") == "hello world"


def test_clipboard_action_set_writes_content():
    tool = ClipboardActionTool()

    with patch(
        "distr.core.agent.tools.clipboard.clipboard_actions.set_clipboard_content",
        return_value=True,
    ) as set_clipboard:
        result = tool._run(action="set", content="hello world")

    set_clipboard.assert_called_once_with("hello world")
    assert "Clipboard updated" in result


def test_clipboard_action_set_can_parse_text_request():
    tool = ClipboardActionTool()

    with patch(
        "distr.core.agent.tools.clipboard.clipboard_actions.set_clipboard_content",
        return_value=True,
    ) as set_clipboard:
        result = tool._run(text="set clipboard to hello world")

    set_clipboard.assert_called_once_with("hello world")
    assert "Clipboard updated" in result


def test_pasteboard_snapshot_restores_non_text_types():
    """Synthetic: snapshot all types, overwrite, restore — including a non-text type."""
    from AppKit import NSData, NSPasteboard, NSPasteboardItem

    from distr.core.agent.tools.clipboard.clipboard_actions import (
        restore_pasteboard,
        snapshot_pasteboard,
    )

    original = snapshot_pasteboard()
    try:
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        item = NSPasteboardItem.alloc().init()
        item.setData_forType_(NSData.dataWithBytes_length_(b"secret-text", 11), "public.utf8-plain-text")
        item.setData_forType_(NSData.dataWithBytes_length_(b"PNGFAKE", 7), "public.png")
        assert pb.writeObjects_([item])

        snap = snapshot_pasteboard()
        types = {t for entry in snap for t in entry}
        assert "public.utf8-plain-text" in types
        assert "public.png" in types

        pb.clearContents()
        item2 = NSPasteboardItem.alloc().init()
        item2.setString_forType_("overwrite", "public.utf8-plain-text")
        pb.writeObjects_([item2])

        assert restore_pasteboard(snap)
        restored = snapshot_pasteboard()
        restored_map = {}
        for entry in restored:
            restored_map.update(entry)
        assert restored_map.get("public.utf8-plain-text") == b"secret-text"
        assert restored_map.get("public.png") == b"PNGFAKE"
    finally:
        restore_pasteboard(original)
