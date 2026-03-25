"""
Comprehensive test for chat window click crash scenarios.
Tests various edge cases that could cause crashes when clicking on chat items.
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
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
import tempfile
_icons_dir = tempfile.gettempdir()
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
        self.id = id
        self.title = title
        self.input = input_text
        self.response = response
        self.created_date = datetime.now()
        self.modified_date = datetime.now()
        self.children = []
        self.model_name = "test_model"
        self.parent_id = None

class MockSettings:
    def __init__(self):
        self.agent_model = "test_model"
        self.voice_enabled = True

# Setup DB Mock
mock_session = MagicMock()
start_mock.get_session.return_value = mock_session
start_mock.Chat = MockChat
start_mock.Settings = MockSettings

# Now import PyQt6 and chat module
from PyQt6.QtWidgets import QApplication, QListWidgetItem
from PyQt6.QtCore import Qt

# Create QApplication before importing chat module
if not QApplication.instance():
    app = QApplication(sys.argv)
else:
    app = QApplication.instance()

# Create dummy icons
for icon_file in ["user_icon.png", "ai_icon.png", "search.png", "spinner.gif"]:
    with open(os.path.join(_icons_dir, icon_file), "wb") as f:
        f.write(b"")

from distr.gui.chat import ChatWindow, ChatListWidget


class TestChatClickCrash(unittest.TestCase):
    """Test suite for chat item click crash scenarios"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.chat_manager = MagicMock()
        self.chat_manager.get_current_chat.return_value = 1
        self.chat_manager._current_chat_id = 1
        
        # Setup mock chats
        self.chat1 = MockChat(1, "Chat 1", "Hello", "Hi there")
        self.chat2 = MockChat(2, "Chat 2", "How are you?", "I am fine")
        self.chat_none_title = MockChat(3, None, "Test", "Response")  # Chat with None title
        self.chat_empty = MockChat(4, "", "", "")  # Chat with empty content
        
        # Setup query mock
        mock_session.query.return_value.get.side_effect = lambda x: {
            1: self.chat1, 
            2: self.chat2,
            3: self.chat_none_title,
            4: self.chat_empty,
            999: None  # Non-existent chat
        }.get(x)
        
        q1 = mock_session.query.return_value
        q2 = q1.filter.return_value
        q3 = q2.order_by.return_value
        q3.all.return_value = [self.chat1, self.chat2]
        
        mock_session.query.return_value.first.return_value = MockSettings()
        
    def test_click_normal_chat(self):
        """Test clicking on a normal chat item"""
        window = ChatWindow(self.chat_manager)
        window.show()
        app.processEvents()
        
        # Find a selectable item
        item = None
        for i in range(window.chat_list.count()):
            it = window.chat_list.item(i)
            if it.flags() & Qt.ItemFlag.ItemIsSelectable:
                item = it
                break
        
        self.assertIsNotNone(item, "Should find a selectable item")
        
        # Click the item - should not crash
        window.on_chat_item_clicked(item)
        app.processEvents()
        
        window.close()
        
    def test_click_chat_with_none_title(self):
        """Test clicking on a chat with None title"""
        window = ChatWindow(self.chat_manager)
        window.show()
        app.processEvents()
        
        # Create a mock item with chat ID 3 (None title)
        item = QListWidgetItem("Chat")
        item.setData(Qt.ItemDataRole.UserRole, 3)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsSelectable)
        
        # Click should not crash even with None title
        window.on_chat_item_clicked(item)
        app.processEvents()
        
        window.close()
        
    def test_click_nonexistent_chat(self):
        """Test clicking on a non-existent chat"""
        window = ChatWindow(self.chat_manager)
        window.show()
        app.processEvents()
        
        # Create a mock item with non-existent chat ID
        item = QListWidgetItem("Deleted Chat")
        item.setData(Qt.ItemDataRole.UserRole, 999)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsSelectable)
        
        # Click should not crash
        window.on_chat_item_clicked(item)
        app.processEvents()
        
        window.close()
        
    def test_click_with_none_chat_manager(self):
        """Test clicking when chat_manager has issues"""
        window = ChatWindow(self.chat_manager)
        window.show()
        app.processEvents()
        
        # Temporarily break the chat_manager
        original_manager = window.chat_manager
        window.chat_manager = None
        
        item = QListWidgetItem("Chat")
        item.setData(Qt.ItemDataRole.UserRole, 1)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsSelectable)
        
        # Click should not crash even with None chat_manager
        window.on_chat_item_clicked(item)
        app.processEvents()
        
        # Restore
        window.chat_manager = original_manager
        window.close()
        
    def test_click_with_missing_chat_manager_attribute(self):
        """Test clicking when chat_manager is missing _current_chat_id"""
        mock_manager = MagicMock(spec=[])  # Empty spec - no attributes
        window = ChatWindow(self.chat_manager)  # Use valid manager for init
        window.show()
        app.processEvents()
        
        # Replace with manager missing attributes
        window.chat_manager = mock_manager
        
        item = QListWidgetItem("Chat")
        item.setData(Qt.ItemDataRole.UserRole, 1)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsSelectable)
        
        # Click should not crash
        try:
            window.load_chat_thread(1)
        except Exception as e:
            self.fail(f"load_chat_thread raised exception: {e}")
            
        app.processEvents()
        window.close()
        
    def test_click_null_item(self):
        """Test clicking with None item"""
        window = ChatWindow(self.chat_manager)
        window.show()
        app.processEvents()
        
        # Should not crash with None item
        window.on_chat_item_clicked(None)
        app.processEvents()
        
        window.close()
        
    def test_display_chat_with_none(self):
        """Test display_chat with None chat"""
        window = ChatWindow(self.chat_manager)
        window.show()
        app.processEvents()
        
        # Should not crash with None
        window.display_chat(None)
        app.processEvents()
        
        window.close()
        
    def test_rapid_clicking(self):
        """Test rapid clicking between chats"""
        window = ChatWindow(self.chat_manager)
        window.show()
        app.processEvents()
        
        # Create items
        item1 = QListWidgetItem("Chat 1")
        item1.setData(Qt.ItemDataRole.UserRole, 1)
        item1.setFlags(item1.flags() | Qt.ItemFlag.ItemIsSelectable)
        
        item2 = QListWidgetItem("Chat 2")
        item2.setData(Qt.ItemDataRole.UserRole, 2)
        item2.setFlags(item2.flags() | Qt.ItemFlag.ItemIsSelectable)
        
        # Rapid clicking should not crash
        for _ in range(10):
            window.on_chat_item_clicked(item1)
            window.on_chat_item_clicked(item2)
            app.processEvents()
            
        window.close()
        
    def test_update_methods_with_missing_widgets(self):
        """Test update methods when widgets are missing"""
        window = ChatWindow(self.chat_manager)
        window.show()
        app.processEvents()
        
        # Temporarily remove widgets
        original_chat_list = window.chat_list
        window.chat_list = None
        
        # Should not crash
        window.update_chat_list_visuals()
        window.update_load_button_visibility()
        app.processEvents()
        
        # Restore
        window.chat_list = original_chat_list
        window.close()


class TestChatListWidgetMousePress(unittest.TestCase):
    """Test ChatListWidget mousePressEvent"""
    
    def setUp(self):
        self.chat_manager = MagicMock()
        self.chat_manager.get_current_chat.return_value = 1
        self.chat_manager._current_chat_id = 1
        
        self.chat1 = MockChat(1, "Chat 1", "Hello", "Hi there")
        mock_session.query.return_value.get.side_effect = lambda x: {1: self.chat1}.get(x)
        
        q1 = mock_session.query.return_value
        q2 = q1.filter.return_value
        q3 = q2.order_by.return_value
        q3.all.return_value = [self.chat1]
        
        mock_session.query.return_value.first.return_value = MockSettings()
        
    def test_mouse_press_with_none_parent_window(self):
        """Test mouse press when parent_window is None"""
        window = ChatWindow(self.chat_manager)
        window.show()
        app.processEvents()
        
        # Temporarily set parent_window to None
        original_parent = window.chat_list.parent_window
        window.chat_list.parent_window = None
        
        # Create a mock event
        from PyQt6.QtGui import QMouseEvent
        from PyQt6.QtCore import QPointF
        from PyQt6.QtCore import Qt as QtCore_Qt
        
        event = MagicMock()
        event.pos.return_value = QPointF(10, 10).toPoint()
        event.accept = MagicMock()
        
        # Should not crash
        window.chat_list.mousePressEvent(event)
        
        # Restore
        window.chat_list.parent_window = original_parent
        window.close()


if __name__ == '__main__':
    print("\n" + "="*60)
    print("Running Chat Click Crash Test Suite")
    print("="*60 + "\n")
    
    unittest.main(verbosity=2)






