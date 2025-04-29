"""
Directory Tree Model Implementation

This module provides the CheckableDirModel class which implements a checkable directory tree view.
It handles directory selection, state persistence, and parent-child relationships.

Key Features:
- Checkable directory tree view
- State persistence
- Path validation
- Parent-child relationship management
"""

from PyQt6.QtCore import Qt, QDir, QModelIndex
from PyQt6.QtGui import QStandardItemModel, QStandardItem
import json
import os
import logging

# Constants
SETTINGS_DIR = os.path.join(os.path.expanduser("~"), ".decisionsai", "settings")
INDEX_FOLDERS_FILE = os.path.join(SETTINGS_DIR, "index_folders.json")

class CheckableDirModel(QStandardItemModel):
    """Model for checkable directory tree view"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalHeaderLabels(['Directories'])
        self.itemChanged.connect(self.on_item_changed)
        self.checked_paths = self.load_checked_folders()
        self.populate_root(QDir.homePath())

    def load_checked_folders(self):
        """Load previously checked folders from file"""
        if os.path.exists(INDEX_FOLDERS_FILE):
            try:
                with open(INDEX_FOLDERS_FILE, 'r') as f:
                    return set(json.load(f))
            except json.JSONDecodeError:
                logging.error(f"Error decoding JSON from {INDEX_FOLDERS_FILE}")
            except Exception as e:
                logging.error(f"Error loading checked folders: {str(e)}")
        return set()

    def populate_root(self, path):
        """Populate root directory"""
        self.clear()
        logging.debug(f"Checked paths: {self.checked_paths}")
        root_dir = QDir(path)
        for info in root_dir.entryInfoList(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot):
            if info.isDir():
                item = self.create_item(info)
                self.set_check_state_for_item(item)
                self.appendRow(item)

    def set_check_state_for_item(self, item):
        """Set check state for an item based on checked paths"""
        path = item.data(Qt.ItemDataRole.UserRole)
        if path in self.checked_paths:
            item.setCheckState(Qt.CheckState.Checked)
        elif any(checked_path.startswith(path) for checked_path in self.checked_paths):
            item.setCheckState(Qt.CheckState.PartiallyChecked)
        else:
            item.setCheckState(Qt.CheckState.Unchecked)

    def create_item(self, file_info):
        """Create a new directory item"""
        item = QStandardItem(file_info.fileName())
        item.setCheckable(True)
        item.setData(file_info.filePath(), Qt.ItemDataRole.UserRole)
        if QDir(file_info.filePath()).count() > 2:  # If directory is not empty
            placeholder = QStandardItem("Loading...")
            item.appendRow(placeholder)
        return item

    def populate_directory(self, parent_item):
        """Populate a directory with its subdirectories"""
        path = parent_item.data(Qt.ItemDataRole.UserRole)
        parent_item.removeRows(0, parent_item.rowCount())
        directory = QDir(path)
        for info in directory.entryInfoList(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot):
            if info.isDir():
                child_item = self.create_item(info)
                self.set_check_state_for_item(child_item)
                parent_item.appendRow(child_item)
                if parent_item.checkState() == Qt.CheckState.Checked:
                    child_item.setCheckState(Qt.CheckState.Checked)

    def set_checked_paths(self, paths):
        """Set checked paths and update UI"""
        for path in paths:
            item = self.find_item(self.invisibleRootItem(), path)
            if item:
                item.setCheckState(Qt.CheckState.Checked)
                self.check_parents(item.parent())

    def find_item(self, parent_item, path):
        """Find item by path"""
        for row in range(parent_item.rowCount()):
            child = parent_item.child(row)
            item_path = child.data(Qt.ItemDataRole.UserRole)
            if item_path == path:
                return child
            elif path.startswith(item_path + os.path.sep):
                if child.hasChildren():
                    result = self.find_item(child, path)
                    if result:
                        return result
                else:
                    self.populate_directory(child)
                    return self.find_item(child, path)
        return None

    def hasChildren(self, parent=QModelIndex()):
        """Check if item has children"""
        if not parent.isValid():
            return True
        return self.itemFromIndex(parent).rowCount() > 0

    def on_item_changed(self, item):
        """Handle item check state changes"""
        # Disconnect to prevent recursive calls
        self.itemChanged.disconnect(self.on_item_changed)
        
        if item.isCheckable():
            check_state = item.checkState()
            self.check_children(item, check_state)
            self.check_parents(item.parent())
            self.update_checked_paths()
        
        # Reconnect after processing
        self.itemChanged.connect(self.on_item_changed)

    def check_children(self, parent, check_state):
        """Update check state of child items"""
        if parent.hasChildren():
            for row in range(parent.rowCount()):
                child = parent.child(row)
                child.setCheckState(check_state)
                self.check_children(child, check_state)

    def check_parents(self, parent):
        """Update check state of parent items"""
        if parent is not None:
            checked_count = 0
            partial_count = 0
            total_count = parent.rowCount()
            for row in range(total_count):
                child = parent.child(row)
                if child.checkState() == Qt.CheckState.Checked:
                    checked_count += 1
                elif child.checkState() == Qt.CheckState.PartiallyChecked:
                    partial_count += 1
            
            if checked_count == 0 and partial_count == 0:
                parent.setCheckState(Qt.CheckState.Unchecked)
            elif checked_count == total_count:
                parent.setCheckState(Qt.CheckState.Checked)
            else:
                parent.setCheckState(Qt.CheckState.PartiallyChecked)
            
            self.check_parents(parent.parent())

    def get_checked_paths(self):
        """Get list of checked paths"""
        checked_paths = []
        self._get_checked_paths_recursive(self.invisibleRootItem(), checked_paths)
        return checked_paths

    def _get_checked_paths_recursive(self, parent_item, checked_paths):
        """Recursively get checked paths"""
        for row in range(parent_item.rowCount()):
            child = parent_item.child(row)
            path = child.data(Qt.ItemDataRole.UserRole)
            if path and child.checkState() == Qt.CheckState.Checked:
                checked_paths.append(path)
            elif child.hasChildren():
                self._get_checked_paths_recursive(child, checked_paths)

    def update_checked_paths(self):
        """Update internal set of checked paths"""
        self.checked_paths = set(self.get_checked_paths())
        
        # Save checked paths to file
        try:
            os.makedirs(SETTINGS_DIR, exist_ok=True)
            with open(INDEX_FOLDERS_FILE, 'w') as f:
                json.dump(list(self.checked_paths), f)
            logging.debug(f"Saved checked paths to {INDEX_FOLDERS_FILE}")
        except Exception as e:
            logging.error(f"Error saving checked paths: {str(e)}")

    def flags(self, index):
        """Return item flags"""
        default_flags = super().flags(index)
        return default_flags & ~Qt.ItemFlag.ItemIsEditable 