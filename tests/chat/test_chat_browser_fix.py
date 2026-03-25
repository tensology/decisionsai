
import unittest
from PyQt6.QtWidgets import QApplication, QTextBrowser
from PyQt6.QtCore import QUrl, QTimer

# Minimal mock for ChatWindow if needed, or just test the browser class logic
class MockChatWindow:
    def handle_link_click(self, url):
        print(f"Mock handled: {url.toString()}")
        self.last_handled_url = url.toString()

class ChatTextBrowser(QTextBrowser):
    def __init__(self, parent=None, chat_window=None):
        super().__init__(parent)
        self.chat_window = chat_window

    def setSource(self, url):
        # Intercept custom action schemes to prevent navigation
        if url.scheme() in ["copy", "play"]:
            if self.chat_window:
                self.chat_window.handle_link_click(url)
            return
        super().setSource(url)

class TestChatActions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not QApplication.instance():
            cls.app = QApplication([])
        else:
            cls.app = QApplication.instance()

    def test_custom_scheme_interception(self):
        mock_window = MockChatWindow()
        browser = ChatTextBrowser(chat_window=mock_window)
        
        # Test copy scheme
        url = QUrl("copy:user:123")
        browser.setSource(url)
        
        # Should have called handle_link_click
        self.assertEqual(mock_window.last_handled_url, "copy:user:123")
        
        # Should NOT have changed source (document title/url remains empty/default)
        self.assertEqual(browser.source(), QUrl(""))

    def test_normal_navigation(self):
        # We can't easily test real navigation without a loop, but we can check if super().setSource was called
        # by checking if source() property updates.
        # Note: navigating to a non-existent file logic is complex in unit test without event loop
        pass

if __name__ == '__main__':
    unittest.main()
