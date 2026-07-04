"""
Chat-style URL interception for ``copy:`` / ``play:`` schemes.

Production chat UI may use QTextBrowser or WebEngine; the rule tested here is:
custom schemes delegate to ``chat_window.handle_link_click`` and must not treat
them like normal navigations.

Pure-logic tests avoid relying on C++/sip virtual dispatch for ``QTextBrowser.setSource``.
"""

from __future__ import annotations

import unittest

import pytest

pytest.importorskip("PyQt6.QtCore")
from PyQt6.QtCore import QUrl

_QT_APP = None


def handle_chat_browser_source(url: QUrl, chat_window) -> bool:
    """Return ``True`` if ``url`` was handled as a custom chat scheme."""
    scheme = url.scheme()
    if scheme in ("copy", "play"):
        if chat_window is not None:
            chat_window.handle_link_click(url)
        return True
    return False


class MockChatWindow:
    def __init__(self) -> None:
        self.last_handled_url: str | None = None

    def handle_link_click(self, url: QUrl) -> None:
        self.last_handled_url = url.toString()


class TestChatSchemeLogic(unittest.TestCase):
    def test_custom_scheme_interception_copy(self):
        mock_window = MockChatWindow()
        url = QUrl("copy:user:123")
        self.assertTrue(handle_chat_browser_source(url, mock_window))
        self.assertEqual(mock_window.last_handled_url, "copy:user:123")

    def test_custom_scheme_interception_play(self):
        mock_window = MockChatWindow()
        url = QUrl("play:track:1")
        self.assertTrue(handle_chat_browser_source(url, mock_window))
        self.assertEqual(mock_window.last_handled_url, "play:track:1")

    def test_http_not_intercepted(self):
        mock_window = MockChatWindow()
        url = QUrl("https://example.com")
        self.assertFalse(handle_chat_browser_source(url, mock_window))
        self.assertIsNone(mock_window.last_handled_url)


@pytest.mark.parametrize(
    "url_str,handled",
    [
        ("copy:user:123", True),
        ("play:x:y", True),
        ("https://example.com/", False),
    ],
)
def test_handle_chat_browser_source_table(url_str, handled):
    mock_window = MockChatWindow()
    url = QUrl(url_str)
    assert handle_chat_browser_source(url, mock_window) is handled
    if handled:
        assert mock_window.last_handled_url == url.toString()
    else:
        assert mock_window.last_handled_url is None


# QTextBrowser wiring — kept light; skips cleanly under incomplete Qt stubs.
def test_qtextbrowser_set_source_delegates_when_possible():
    from PyQt6.QtWidgets import QApplication, QTextBrowser
    from PyQt6.QtGui import QTextDocument

    qc = __import__("PyQt6.QtCore", fromlist=["*"])
    if getattr(qc, "_decisions_stub", False):
        pytest.skip("QTextBrowser is stubbed; logic covered by scheme tests above")

    global _QT_APP
    _QT_APP = QApplication.instance() or QApplication([])

    class ChatTextBrowser(QTextBrowser):
        def __init__(self, parent=None, chat_window=None):
            super().__init__(parent)
            self.chat_window = chat_window

        def setSource(self, url, type=QTextDocument.ResourceType.UnknownResource):
            if handle_chat_browser_source(url, self.chat_window):
                return
            super().setSource(url, type)

    mock_window = MockChatWindow()
    browser = ChatTextBrowser(chat_window=mock_window)
    url = QUrl("copy:user:123")
    browser.setSource(url)
    assert mock_window.last_handled_url == "copy:user:123"
    assert browser.source().isEmpty() or browser.source().toString() in ("", "invalid")
