#!/usr/bin/env python3
"""Test full chat feed loading with emojis to ensure no crashes"""

import sys
import os
from unittest.mock import MagicMock, patch, Mock
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# Mock database before importing
sys.modules['distr.core.db'] = MagicMock()
sys.modules['distr.core.utils'] = MagicMock()

# Mock constants with a real temp directory
import tempfile
temp_icons_dir = tempfile.mkdtemp()
mock_constants = MagicMock()
mock_constants.ICONS_DIR = temp_icons_dir
sys.modules['distr.core.paths'] = mock_constants

sys.modules['distr.core.signals'] = MagicMock()
sys.modules['distr.gui.utils.get_ollama_models'] = MagicMock()

# Mock ChatWindowStyles properly
mock_styles = MagicMock()
mock_styles.MODEL_COMBO = ""
mock_styles.CHAT_LIST = ""
mock_styles.SEARCH_INPUT = ""
mock_styles.MAIN_WINDOW = ""
mock_styles.LEFT_WIDGET = ""
mock_styles.SEARCH_WIDGET = ""
mock_styles.SEARCH_ICON = ""
mock_styles.NEW_CHAT_BUTTON = ""
mock_styles.CHAT_THREAD_VIEW = ""
mock_styles.INPUT_AREA = ""
mock_styles.SEND_BUTTON = ""
mock_styles.CONTEXT_MENU = ""
sys.modules['distr.gui.styles.chatwindowstyles'] = MagicMock()
sys.modules['distr.gui.styles.chatwindowstyles'].ChatWindowStyles = mock_styles

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QTimer
from distr.gui.chat import ChatWindow

# Test data with various emoji scenarios - simulating real database chats
TEST_CHATS = [
    {
        "id": 1,
        "title": "Normal chat",
        "input": "Hello",
        "response": "Hi there",
        "created_date": datetime.now(),
        "modified_date": datetime.now(),
        "parent_id": None,
        "children": [],
        "model_name": "llama3.1:8b"
    },
    {
        "id": 2,
        "title": "Chat with 😊 emoji",
        "input": "Test",
        "response": "Response",
        "created_date": datetime.now() - timedelta(hours=1),
        "modified_date": datetime.now() - timedelta(hours=1),
        "parent_id": None,
        "children": [],
        "model_name": "llama3.1:8b"
    },
    {
        "id": 3,
        "title": "Multiple 🎉 emojis 🔥 here",
        "input": "Test",
        "response": "Response",
        "created_date": datetime.now() - timedelta(days=1),
        "modified_date": datetime.now() - timedelta(days=1),
        "parent_id": None,
        "children": [],
        "model_name": "llama3.1:8b"
    },
    {
        "id": 4,
        "title": "Star ★ and arrow →",
        "input": "Test",
        "response": "Response",
        "created_date": datetime.now() - timedelta(days=2),
        "modified_date": datetime.now() - timedelta(days=2),
        "parent_id": None,
        "children": [],
        "model_name": "llama3.1:8b"
    },
    {
        "id": 5,
        "title": None,  # No title
        "input": "Test",
        "response": "Response",
        "created_date": datetime.now() - timedelta(days=3),
        "modified_date": datetime.now() - timedelta(days=3),
        "parent_id": None,
        "children": [],
        "model_name": "llama3.1:8b"
    },
    {
        "id": 6,
        "title": "",  # Empty title
        "input": "Test",
        "response": "Response",
        "created_date": datetime.now() - timedelta(days=4),
        "modified_date": datetime.now() - timedelta(days=4),
        "parent_id": None,
        "children": [],
        "model_name": "llama3.1:8b"
    },
]

class TestChatFeedLoading:
    def __init__(self):
        self.app = None
        self.window = None
        self.crashed = False
        self.patches = []
        
    def setup(self):
        """Set up test environment"""
        if not QApplication.instance():
            self.app = QApplication(sys.argv)
        else:
            self.app = QApplication.instance()
        
        # Mock ChatManager
        self.mock_chat_manager = MagicMock()
        self.mock_chat_manager.get_current_chat.return_value = 1
        self.mock_chat_manager.create_chat.return_value = 1
        self.mock_chat_manager.get_chat_history.return_value = []
        self.mock_chat_manager.agent_prompt = "You are an AI assistant."
        self.mock_chat_manager.set_current_chat = MagicMock()
        self.mock_chat_manager.chat_created = MagicMock()
        self.mock_chat_manager.chat_updated = MagicMock()
        self.mock_chat_manager.chat_deleted = MagicMock()
        self.mock_chat_manager.current_chat_changed = MagicMock()
        
        # Mock get_ollama_models
        patch1 = patch('distr.gui.chat.get_ollama_models', return_value=[])
        patch1.start()
        self.patches.append(patch1)
        
        # Mock signal_manager
        mock_signal_manager = MagicMock()
        mock_signal_manager.chat_stream_started = MagicMock()
        mock_signal_manager.chat_stream_token = MagicMock()
        mock_signal_manager.chat_stream_finished = MagicMock()
        mock_signal_manager.chat_stream_error = MagicMock()
        mock_signal_manager.typing_indicator_changed = MagicMock()
        mock_signal_manager.chat_message_added = MagicMock()
        mock_signal_manager.chat_created = MagicMock()
        mock_signal_manager.chat_updated = MagicMock()
        mock_signal_manager.chat_cleared = MagicMock()
        mock_signal_manager.chat_deleted = MagicMock()
        mock_signal_manager.trigger_new_chat = MagicMock()
        mock_signal_manager.model_hot_reload = MagicMock()
        mock_signal_manager.interrupt_tts = MagicMock()
        mock_signal_manager.push_to_talk_start = MagicMock()
        mock_signal_manager.push_to_talk_stop = MagicMock()
        
        patch2 = patch('distr.gui.chat.signal_manager', mock_signal_manager)
        patch2.start()
        self.patches.append(patch2)
        
        # Create ChatWindow with file operation mocking
        try:
            # Mock file operations for icon loading
            from unittest.mock import mock_open
            mock_file = mock_open(read_data=b'fake_icon_data')
            
            with patch('builtins.open', mock_file):
                with patch('os.path.exists', return_value=True):
                    self.window = ChatWindow(self.mock_chat_manager)
                    print("✅ ChatWindow created successfully")
        except Exception as e:
            print(f"❌ Failed to create ChatWindow: {e}")
            import traceback
            traceback.print_exc()
            self.crashed = True
            return False
        
        return True
    
    def test_load_chat_list_with_emojis(self):
        """Test loading chat list with emojis from database"""
        print("\n=== Testing load_chat_list with emojis ===")
        
        # Mock database session and Chat objects
        from distr.core.db import Chat, Settings
        
        # Create mock Chat objects
        mock_chats = []
        for chat_data in TEST_CHATS:
            mock_chat = MagicMock()
            mock_chat.id = chat_data["id"]
            mock_chat.title = chat_data["title"]
            mock_chat.input = chat_data["input"]
            mock_chat.response = chat_data["response"]
            mock_chat.created_date = chat_data["created_date"]
            mock_chat.modified_date = chat_data["modified_date"]
            mock_chat.parent_id = chat_data["parent_id"]
            mock_chat.children = chat_data["children"]
            mock_chat.model_name = chat_data["model_name"]
            mock_chats.append(mock_chat)
        
        # Mock Settings
        mock_settings = MagicMock()
        mock_settings.agent_model = "llama3.1:8b"
        
        # Mock database session
        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value.all.return_value = mock_chats
        mock_session.query.return_value = mock_query
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = mock_chats
        mock_session.query.return_value.get.return_value = mock_chats[0] if mock_chats else None
        mock_session.close = MagicMock()
        
        # Mock get_session to return our mock session
        with patch('distr.gui.chat.get_session', return_value=mock_session):
            # Also mock Settings query
            mock_settings_query = MagicMock()
            mock_settings_query.first.return_value = mock_settings
            mock_session.query.return_value.filter.return_value.first.return_value = mock_settings
            
            try:
                print("  Attempting to load chat list with emojis...")
                
                # Show window first
                self.window.show()
                QApplication.processEvents()
                
                # Load chat list
                self.window.load_chat_list()
                QApplication.processEvents()
                
                print(f"  ✅ load_chat_list() completed")
                
                # Check if items were added
                item_count = self.window.chat_list.count()
                print(f"  Items in list: {item_count}")
                
                if item_count > 0:
                    print("  ✅ Chat items added successfully")
                    
                    # Check each item's text for emojis
                    for i in range(min(item_count, len(TEST_CHATS))):
                        item = self.window.chat_list.item(i)
                        if item:
                            text = item.text()
                            tooltip = item.toolTip()
                            print(f"    Item {i}: text={repr(text)}, tooltip={repr(tooltip)}")
                            
                            # Verify no emojis in displayed text (except star/arrow)
                            if text and "😊" in text:
                                print(f"    ❌ FAIL: Emoji 😊 still in item text: {text}")
                                return False
                            if text and "🎉" in text:
                                print(f"    ❌ FAIL: Emoji 🎉 still in item text: {text}")
                                return False
                            if text and "🔥" in text:
                                print(f"    ❌ FAIL: Emoji 🔥 still in item text: {text}")
                                return False
                else:
                    print("  ⚠️  No items in list")
                
                # Force a repaint to test delegate painting
                print("  Testing delegate painting...")
                self.window.chat_list.viewport().update()
                QApplication.processEvents()
                print("  ✅ Delegate painting completed without crash")
                
                return True
            except Exception as e:
                print(f"  ❌ Error loading chat list: {e}")
                import traceback
                traceback.print_exc()
                self.crashed = True
                return False
    
    def test_display_chat_with_emoji_title(self):
        """Test displaying a chat that has an emoji in its title"""
        print("\n=== Testing display_chat with emoji title ===")
        
        # First load the chat list
        if not self.test_load_chat_list_with_emojis():
            return False
        
        try:
            # Find the chat with emoji title (ID 2)
            emoji_chat_id = 2
            item = None
            for i in range(self.window.chat_list.count()):
                list_item = self.window.chat_list.item(i)
                if list_item and list_item.data(Qt.ItemDataRole.UserRole) == emoji_chat_id:
                    item = list_item
                    break
            
            if item:
                print(f"  Found chat item with ID {emoji_chat_id}")
                
                # Try to click/select it
                self.window.chat_list.setCurrentItem(item)
                QApplication.processEvents()
                
                # Try to display it
                self.window.on_chat_item_clicked(item)
                QApplication.processEvents()
                
                print("  ✅ Chat with emoji title displayed without crash")
                return True
            else:
                print("  ⚠️  Could not find chat item with emoji title")
                return False
        except Exception as e:
            print(f"  ❌ Error displaying chat: {e}")
            import traceback
            traceback.print_exc()
            self.crashed = True
            return False
    
    def test_window_show_and_hide(self):
        """Test showing and hiding the window multiple times"""
        print("\n=== Testing window show/hide ===")
        try:
            # Show window
            self.window.show()
            QApplication.processEvents()
            print("  ✅ Window shown")
            
            # Hide window
            self.window.hide()
            QApplication.processEvents()
            print("  ✅ Window hidden")
            
            # Show again
            self.window.show()
            QApplication.processEvents()
            print("  ✅ Window shown again")
            
            return True
        except Exception as e:
            print(f"  ❌ Error with window show/hide: {e}")
            import traceback
            traceback.print_exc()
            self.crashed = True
            return False
    
    def cleanup(self):
        """Clean up patches"""
        for patch_obj in self.patches:
            patch_obj.stop()
    
    def run_all_tests(self):
        """Run all tests"""
        print("=" * 60)
        print("Testing Chat Feed Loading with Emojis")
        print("=" * 60)
        
        if not self.setup():
            print("\n❌ Setup failed - cannot continue tests")
            return False
        
        results = []
        results.append(("Load Chat List", self.test_load_chat_list_with_emojis()))
        results.append(("Display Chat with Emoji", self.test_display_chat_with_emoji_title()))
        results.append(("Window Show/Hide", self.test_window_show_and_hide()))
        
        self.cleanup()
        
        print("\n" + "=" * 60)
        print("Test Results:")
        print("=" * 60)
        for test_name, passed in results:
            status = "✅ PASS" if passed else "❌ FAIL"
            print(f"  {test_name}: {status}")
        
        all_passed = all(result[1] for result in results) and not self.crashed
        
        if all_passed:
            print("\n✅ All tests passed!")
        else:
            print("\n❌ Some tests failed or crashed")
        
        return all_passed

if __name__ == "__main__":
    tester = TestChatFeedLoading()
    success = tester.run_all_tests()
    
    if tester.app:
        # Run event loop briefly to see if anything crashes
        QTimer.singleShot(1000, tester.app.quit)
        try:
            tester.app.exec()
        except:
            pass
    
    sys.exit(0 if success else 1)

