import sys
import os
import tempfile
import unittest
import signal
from unittest.mock import MagicMock, patch

_icons_dir = tempfile.gettempdir()
from PyQt6.QtWidgets import QApplication, QListWidgetItem, QMainWindow, QListWidget
from PyQt6.QtCore import Qt, QSize, QTimer
# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, project_root)

# Mocking modules BEFORE importing chat
start_mock = MagicMock()
sys.modules['distr.core.db'] = start_mock
sys.modules['distr.core.utils'] = MagicMock()
sys.modules['distr.core.signals'] = MagicMock()
sys.modules['distr.gui.utils.get_ollama_models'] = MagicMock()
# Mock styles with strings
class MockStyles:
    def __getattr__(self, name):
        return "/* mock style */"

mock_module = MagicMock()
mock_module.ChatWindowStyles = MockStyles()
sys.modules['distr.gui.styles.chatwindowstyles'] = mock_module

# Mock constants (cross-platform temp dir)
sys.modules['distr.core.paths'] = MagicMock()
sys.modules['distr.core.paths'].ICONS_DIR = _icons_dir

# Define Mock Classes
class MockColumn:
    def is_(self, other):
        return MagicMock()
        
    def ilike(self, other):
        return MagicMock()
        
    def any(self, other):
        return MagicMock()
        
    def __eq__(self, other):
        return MagicMock()

    def desc(self):
        return MagicMock()

class MockChat:
    # Class attributes for query filters
    parent_id = MockColumn()
    title = MockColumn()
    input = MockColumn()
    response = MockColumn()
    children = MockColumn()
    created_date = MockColumn()
    modified_date = MockColumn()

    def __init__(self, id, title, input_text, response):
        from datetime import datetime
        self.id = id
        self.title = title
        self.input = input_text
        self.response = response
        self.created_date = datetime.now()
        self.modified_date = datetime.now()
        self.children = []
        self.model_name = "test_model"
        self.parent_id = None # Instance attribute

class MockSettings:
    def __init__(self):
        self.agent_model = "test_model"
        self.voice_enabled = True

# Define Mock Classes
class MockChatWindow(QMainWindow):
    def __init__(self, chat_manager):
        # Create necessary mocks for initialization
        self.chat_manager = chat_manager
        
        # Mocks needed for __init__
        self.chat_list = QListWidget()
        self.chat_list.setMouseTracking(True)
        self.chat_list.mousePressEvent = MagicMock()
        
        self.chat_thread_view = MagicMock()
        self.chat_thread_view.textCursor.return_value = MagicMock()
        self.chat_thread_view.verticalScrollBar.return_value = MagicMock()
        
        # Mock signals that might be called
        self.chat_thread_view.textChanged = MagicMock()
        self.chat_thread_view.verticalScrollBar().rangeChanged = MagicMock()
        
        self.model_combo = MagicMock()
        self.model_selector = MagicMock() # Mock the model_selector object itself
        self.model_selector.model_combo = self.model_combo # And its combo attribute
        
        self.load_btn = MagicMock()
        self.new_chat_btn = MagicMock()
        self.chat_search = MagicMock()
        
        self.user_icon_data_url = "data:image/png;base64,dummy"
        self.ai_icon_data_url = "data:image/png;base64,dummy"
        
        super().__init__()
        
        # We need to manually call _setup_window because we're mocking so much
        # But we want to test the real methods, so we can't mock everything out
        # Let's bypass the actual __init__ and set up what we need manually or 
        # use the real __init__ but mock the parts it calls.
        
        # Option 2: Use real __init__ but patch methods we don't want to run
        # We can't easily do that inside the class definition.
        # So we just set attributes that __init__ would set.
        self.loaded_chat_id = None
        self.current_chat_id = None
        self.is_renaming = False
        self.rename_editor = None
        self.current_streaming_chat_id = None
        self.streaming_response = ""
        
        # Bind real methods we want to test
        self.load_chat_thread = ChatWindow.load_chat_thread.__get__(self, MockChatWindow)
        self.display_chat = ChatWindow.display_chat.__get__(self, MockChatWindow)
        self._get_message_html = ChatWindow._get_message_html.__get__(self, MockChatWindow)
        self.update_chat_title = MagicMock()
        self.scroll_to_bottom = MagicMock()
        self.update_load_button_visibility = MagicMock()
        self.update_chat_list_visuals = MagicMock()

# Setup DB Mock
mock_session = MagicMock()
start_mock.get_session.return_value = mock_session
start_mock.Chat = MockChat
start_mock.Settings = MockSettings

# Now import chat
from distr.gui.chat import ChatWindow, ChatListWidget

class TestChatCrash(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create QApplication if it doesn't exist
        if not QApplication.instance():
            cls.app = QApplication(sys.argv)
        else:
            cls.app = QApplication.instance()
        
        cls._icons_dir = tempfile.gettempdir()
        # Create dummy icons
        for name in ["user_icon.png", "ai_icon.png", "search.png", "spinner.gif"]:
            with open(os.path.join(cls._icons_dir, name), "wb") as f:
                f.write(b"")

    @classmethod
    def tearDownClass(cls):
        for name in ["user_icon.png", "ai_icon.png", "search.png", "spinner.gif"]:
            p = os.path.join(cls._icons_dir, name)
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def setUp(self):
        self.chat_manager = MagicMock()
        self.chat_manager.get_current_chat.return_value = 1
        
        # Setup mock DB queries
        self.chat1 = MockChat(1, "Chat 1", "Hello", "Hi there")
        self.chat2 = MockChat(2, "Chat 2", "How are you?", "I am fine")
        
        mock_session.query.return_value.get.side_effect = lambda x: {1: self.chat1, 2: self.chat2}.get(x)
        # Fix mock chain: query -> filter -> order_by -> all
        # We need to make sure we return the list for the specific chain used in load_chat_list
        q1 = mock_session.query.return_value
        q2 = q1.filter.return_value
        q3 = q2.order_by.return_value
        q3.all.return_value = [self.chat1, self.chat2]
        
        mock_session.query.return_value.first.return_value = MockSettings()

    def test_click_crash(self):
        print("\nRunning Chat Crash Reproduction Test...")
        try:
            # Initialize window
            window = ChatWindow(self.chat_manager)
            window.show()
            
            # Ensure list is populated
            self.app.processEvents()
            
            # Find an item in the list
            list_widget = window.chat_list
            self.assertGreater(list_widget.count(), 0, "Chat list should not be empty")
            
            # Find a chat item (not header)
            item = None
            for i in range(list_widget.count()):
                it = list_widget.item(i)
                if it.flags() & Qt.ItemFlag.ItemIsSelectable:
                    item = it
                    break
            
            self.assertIsNotNone(item, "Could not find a selectable chat item")
            
            print(f"Clicking item: {item.text()}")
            
            # Simulate click
            # We call the method directly to simulate the logic execution
            # But the crash might be in the event handling sequence
            # So let's try to simulate checking the item and calling the handler
            
            # This mimics what mousePressEvent does:
            window.on_chat_item_clicked(item)
            
            print("Successfully clicked item without crash immediately.")
            
            # Process events to allow any deferred crashes to happen
            self.app.processEvents()
            
        except Exception as e:
            print(f"CRASH DETECTED: {e}")
            raise e

if __name__ == '__main__':
    unittest.main()
