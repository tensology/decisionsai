#!/usr/bin/env python3
"""Test opening chat window from Oracle (simulating real app scenario)"""

import sys
import os
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime, timedelta
import tempfile

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# Mock database before importing
sys.modules['distr.core.db'] = MagicMock()
sys.modules['distr.core.utils'] = MagicMock()

# Mock constants with a real temp directory
temp_icons_dir = tempfile.mkdtemp()
mock_constants = MagicMock()
mock_constants.ICONS_DIR = temp_icons_dir
sys.modules['distr.core.paths'] = mock_constants

sys.modules['distr.core.signals'] = MagicMock()
sys.modules['distr.gui.utils.get_ollama_models'] = MagicMock()

# Mock ChatWindowStyles
mock_styles = MagicMock()
for attr in ['MODEL_COMBO', 'CHAT_LIST', 'SEARCH_INPUT', 'MAIN_WINDOW', 
             'LEFT_WIDGET', 'SEARCH_WIDGET', 'SEARCH_ICON', 'NEW_CHAT_BUTTON',
             'CHAT_THREAD_VIEW', 'INPUT_AREA', 'SEND_BUTTON', 'CONTEXT_MENU']:
    setattr(mock_styles, attr, "")
sys.modules['distr.gui.styles.chatwindowstyles'] = MagicMock()
sys.modules['distr.gui.styles.chatwindowstyles'].ChatWindowStyles = mock_styles

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QTimer
from distr.gui.oracle import OracleWindow
from distr.gui.chat import ChatWindow

class TestChatWindowFromOracle:
    def __init__(self):
        self.app = None
        self.oracle_window = None
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
        
        # Mock other windows
        mock_about_window = MagicMock()
        mock_player_window = MagicMock()
        mock_eula_window = MagicMock()
        mock_eula_window.is_accepted.return_value = True
        
        # Mock get_ollama_models
        patch1 = patch('distr.gui.chat.get_ollama_models', return_value=[])
        patch1.start()
        self.patches.append(patch1)
        
        # Mock signal_manager
        mock_signal_manager = MagicMock()
        for attr in ['chat_stream_started', 'chat_stream_token', 'chat_stream_finished',
                     'chat_stream_error', 'typing_indicator_changed', 'chat_message_added',
                     'chat_created', 'chat_updated', 'chat_cleared', 'chat_deleted',
                     'trigger_new_chat', 'model_hot_reload', 'interrupt_tts',
                     'push_to_talk_start', 'push_to_talk_stop', 'direct_oracle_change']:
            setattr(mock_signal_manager, attr, MagicMock())
        patch2 = patch('distr.gui.chat.signal_manager', mock_signal_manager)
        patch2.start()
        self.patches.append(patch2)
        
        patch3 = patch('distr.gui.oracle.window.signal_manager', mock_signal_manager)
        patch3.start()
        self.patches.append(patch3)
        
        # Create OracleWindow with file operation mocking
        try:
            with patch('builtins.open', mock_open(read_data=b'fake_icon_data')):
                with patch('os.path.exists', return_value=True):
                    with patch('distr.gui.oracle.window.load_settings_from_db', return_value={'sphere_size': 120}):
                        self.oracle_window = OracleWindow(
                            mock_about_window,
                            mock_player_window,
                            self.mock_chat_manager,
                            mock_eula_window
                        )
                        print("✅ OracleWindow created successfully")
        except Exception as e:
            print(f"❌ Failed to create OracleWindow: {e}")
            import traceback
            traceback.print_exc()
            self.crashed = True
            return False
        
        return True
    
    def test_open_chat_from_oracle(self):
        """Test opening chat window from Oracle (simulating real user action)"""
        print("\n=== Testing open chat window from Oracle ===")
        
        try:
            # Show oracle window first
            self.oracle_window.show()
            QApplication.processEvents()
            print("  ✅ Oracle window shown")
            
            # Simulate opening chat window (like clicking menu item)
            # Mock file operations during window creation
            print("  Attempting to open chat window...")
            with patch('builtins.open', mock_open(read_data=b'fake_icon_data')):
                with patch('os.path.exists', return_value=True):
                    self.oracle_window.show_chat_window()
            QApplication.processEvents()
            print("  ✅ show_chat_window() called")
            
            # Check if chat window was created
            if self.oracle_window.chat_window:
                print("  ✅ ChatWindow created")
                
                # Check if window is visible
                if self.oracle_window.chat_window.isVisible():
                    print("  ✅ ChatWindow is visible")
                else:
                    print("  ⚠️  ChatWindow created but not visible")
                
                # Process events to trigger any rendering
                QApplication.processEvents()
                print("  ✅ Event processing completed")
                
                # Try to trigger a repaint
                self.oracle_window.chat_window.update()
                QApplication.processEvents()
                print("  ✅ Window update completed")
                
                return True
            else:
                print("  ❌ ChatWindow was not created")
                return False
        except Exception as e:
            print(f"  ❌ Error opening chat window: {e}")
            import traceback
            traceback.print_exc()
            self.crashed = True
            return False
    
    def test_chat_window_with_real_database_simulation(self):
        """Test chat window with simulated database chats containing emojis"""
        print("\n=== Testing chat window with emoji chats ===")
        
        # Mock database with emoji chats
        from distr.core.db import Chat, Settings
        
        mock_chats = []
        emoji_titles = [
            "Normal chat",
            "Chat with 😊 emoji",
            "Multiple 🎉 emojis 🔥",
            "Star ★ Arrow →",
        ]
        
        for i, title in enumerate(emoji_titles):
            mock_chat = MagicMock()
            mock_chat.id = i + 1
            mock_chat.title = title
            mock_chat.input = "Test input"
            mock_chat.response = "Test response"
            mock_chat.created_date = datetime.now() - timedelta(hours=i)
            mock_chat.modified_date = datetime.now() - timedelta(hours=i)
            mock_chat.parent_id = None
            mock_chat.children = []
            mock_chat.model_name = "llama3.1:8b"
            mock_chats.append(mock_chat)
        
        mock_settings = MagicMock()
        mock_settings.agent_model = "llama3.1:8b"
        
        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value.all.return_value = mock_chats
        mock_session.query.return_value = mock_query
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = mock_chats
        mock_session.query.return_value.get.return_value = mock_chats[0] if mock_chats else None
        mock_session.close = MagicMock()
        
        with patch('distr.gui.chat.get_session', return_value=mock_session):
            mock_settings_query = MagicMock()
            mock_settings_query.first.return_value = mock_settings
            mock_session.query.return_value.filter.return_value.first.return_value = mock_settings
            
            try:
                # Open chat window with file mocking
                with patch('builtins.open', mock_open(read_data=b'fake_icon_data')):
                    with patch('os.path.exists', return_value=True):
                        self.oracle_window.show_chat_window()
                QApplication.processEvents()
                
                if self.oracle_window.chat_window:
                    # Load chat list
                    print("  Loading chat list with emoji titles...")
                    self.oracle_window.chat_window.load_chat_list()
                    QApplication.processEvents()
                    
                    item_count = self.oracle_window.chat_window.chat_list.count()
                    print(f"  Items loaded: {item_count}")
                    
                    # Check items for emojis
                    for i in range(min(item_count, len(emoji_titles))):
                        item = self.oracle_window.chat_window.chat_list.item(i)
                        if item:
                            text = item.text()
                            if "😊" in text or "🎉" in text or "🔥" in text:
                                print(f"  ❌ FAIL: Emoji found in item {i}: {text}")
                                return False
                    
                    print("  ✅ All emojis sanitized correctly")
                    return True
                else:
                    print("  ❌ ChatWindow not created")
                    return False
            except Exception as e:
                print(f"  ❌ Error: {e}")
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
        print("Testing Chat Window from Oracle")
        print("=" * 60)
        
        if not self.setup():
            print("\n❌ Setup failed - cannot continue tests")
            return False
        
        results = []
        results.append(("Open Chat from Oracle", self.test_open_chat_from_oracle()))
        results.append(("Chat Window with Emoji Chats", self.test_chat_window_with_real_database_simulation()))
        
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
    tester = TestChatWindowFromOracle()
    success = tester.run_all_tests()
    
    if tester.app:
        QTimer.singleShot(1000, tester.app.quit)
        try:
            tester.app.exec()
        except:
            pass
    
    sys.exit(0 if success else 1)

