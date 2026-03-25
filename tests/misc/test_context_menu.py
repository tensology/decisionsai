#!/usr/bin/env python3
"""
Test script to reproduce and debug context menu bus crash.
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication, QMainWindow, QListWidget, QListWidgetItem, QMenu, QVBoxLayout, QWidget
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QContextMenuEvent
import hashlib

class TestChatListWidget(QListWidget):
    """Minimal test version of ChatListWidget"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        print("TestChatListWidget initialized")
        
    def contextMenuEvent(self, event):
        """Test context menu event"""
        print(f"contextMenuEvent called at position: {event.pos()}")
        
        try:
            position = event.pos()
            item = self.itemAt(position)
            print(f"Item at position: {item}")
            
            # Create menu
            menu = QMenu(self)
            print("QMenu created")
            
            if item:
                chat_id = item.data(Qt.ItemDataRole.UserRole)
                print(f"Chat ID: {chat_id}")
                
                if chat_id is not None:
                    # Create hash
                    try:
                        chat_id_str = str(chat_id)
                        md5_hash = hashlib.md5(chat_id_str.encode()).hexdigest()
                        short_hash = md5_hash[:6]
                        print(f"Hash: {short_hash}")
                    except Exception as e:
                        print(f"Hash error: {e}")
                        short_hash = "??????"
                    
                    id_action = menu.addAction(f"Chat: #{short_hash}")
                    id_action.setEnabled(False)
                    print("ID action added")
                    
                    menu.addSeparator()
                    
                    load_action = menu.addAction("Load Chat")
                    rename_action = menu.addAction("Rename Chat")
                    remove_action = menu.addAction("Remove Chat")
                    print("Actions added")
                    
                    menu.addSeparator()
                    clear_all_action = menu.addAction("⚠️ Clear All Chats...")
                    print("Clear all action added")
                    
                    # Execute menu
                    print(f"Executing menu at global pos: {event.globalPos()}")
                    action = menu.exec(event.globalPos())
                    print(f"Menu returned action: {action}")
                    
                    if action == load_action:
                        print("Load action selected")
                    elif action == rename_action:
                        print("Rename action selected")
                    elif action == remove_action:
                        print("Remove action selected")
                    elif action == clear_all_action:
                        print("Clear all action selected")
            else:
                # Empty area
                clear_all_action = menu.addAction("⚠️ Clear All Chats...")
                action = menu.exec(event.globalPos())
                if action == clear_all_action:
                    print("Clear all from empty area")
            
            event.accept()
            print("Event accepted, contextMenuEvent completed successfully")
            
        except Exception as e:
            print(f"ERROR in contextMenuEvent: {e}")
            import traceback
            traceback.print_exc()
            event.accept()


class TestWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Context Menu Test")
        self.setGeometry(100, 100, 400, 300)
        
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        # Create list widget
        self.chat_list = TestChatListWidget(self)
        layout.addWidget(self.chat_list)
        
        # Add some test items
        for i in range(5):
            item = QListWidgetItem(f"Test Chat {i+1} - This is a longer title to test truncation")
            item.setData(Qt.ItemDataRole.UserRole, i + 1)  # Set chat ID
            self.chat_list.addItem(item)
            print(f"Added item {i+1}")
        
        print("TestWindow initialized with 5 items")


def main():
    print("Starting context menu test...")
    print(f"Python version: {sys.version}")
    
    app = QApplication(sys.argv)
    print("QApplication created")
    
    window = TestWindow()
    window.show()
    print("Window shown")
    
    print("\n" + "="*50)
    print("RIGHT-CLICK ON LIST ITEMS TO TEST CONTEXT MENU")
    print("="*50 + "\n")
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()





