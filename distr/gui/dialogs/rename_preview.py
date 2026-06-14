"""
Rename Preview Dialog - Shows a preview of file/folder renames before execution.

This dialog displays a table showing:
- Original file/folder path
- New file/folder path
- Status (pending, will overwrite, etc.)

User must confirm before the renames are executed.
"""

import os
import logging
from typing import List, Dict
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QFrame)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

logger = logging.getLogger(__name__)


class RenamePreviewDialog(QDialog):
    """
    Dialog that shows a preview of file/folder renames before execution.
    
    Args:
        renames: List of dicts with 'source', 'destination', and optional 'type' (file/folder)
        parent: Parent widget
        title: Dialog title
    """
    
    def __init__(self, renames: List[Dict[str, str]], parent=None, title: str = "Rename Preview"):
        super().__init__(parent)
        self.renames = renames
        self.confirmed = False
        
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumSize(900, 500)
        self.resize(1000, 600)
        
        self._setup_ui()
        
        # Center on the same screen as parent (Oracle window)
        self._center_on_screen()
    
    def _setup_ui(self):
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Header
        header_label = QLabel(f"📝 Review {len(self.renames)} Rename Operation(s)")
        header_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #2196F3;")
        layout.addWidget(header_label)
        
        # Info label
        info_label = QLabel("Please review the changes below. Files/folders will be renamed from the original name to the new name.")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; font-size: 13px;")
        layout.addWidget(info_label)
        
        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("background-color: #ddd;")
        layout.addWidget(separator)
        
        # Table for renames
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Type", "Original Name", "→", "New Name"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(2, 30)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #ddd;
                border-radius: 5px;
                background-color: white;
                gridline-color: #eee;
            }
            QTableWidget::item {
                padding: 8px;
            }
            QHeaderView::section {
                background-color: #f5f5f5;
                padding: 8px;
                border: none;
                border-bottom: 2px solid #ddd;
                font-weight: bold;
            }
        """)
        
        # Populate table
        self._populate_table()
        layout.addWidget(self.table)
        
        # Warnings section
        warnings = self._check_for_warnings()
        if warnings:
            warning_frame = QFrame()
            warning_frame.setStyleSheet("background-color: #fff3cd; border: 1px solid #ffc107; border-radius: 5px; padding: 10px;")
            warning_layout = QVBoxLayout(warning_frame)
            warning_label = QLabel("⚠️ Warnings:")
            warning_label.setStyleSheet("font-weight: bold; color: #856404;")
            warning_layout.addWidget(warning_label)
            for warning in warnings:
                w_label = QLabel(f"  • {warning}")
                w_label.setStyleSheet("color: #856404;")
                warning_layout.addWidget(w_label)
            layout.addWidget(warning_frame)
        
        # Button row
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        # Cancel button
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #6c757d;
                color: white;
                border: none;
                padding: 10px 30px;
                border-radius: 5px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #5a6268;
            }
        """)
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        # Confirm button
        confirm_btn = QPushButton(f"✓ Confirm {len(self.renames)} Rename(s)")
        confirm_btn.setStyleSheet("""
            QPushButton {
                background-color: #28a745;
                color: white;
                border: none;
                padding: 10px 30px;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #218838;
            }
        """)
        confirm_btn.clicked.connect(self._on_confirm)
        button_layout.addWidget(confirm_btn)
        
        layout.addLayout(button_layout)
    
    def _populate_table(self):
        """Populate the table with rename operations."""
        self.table.setRowCount(len(self.renames))
        
        for row, rename in enumerate(self.renames):
            source = rename.get('source', '')
            destination = rename.get('destination', '')
            item_type = rename.get('type', 'file')
            
            # Type column (icon)
            type_icon = "📁" if item_type == 'folder' else "📄"
            type_item = QTableWidgetItem(type_icon)
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, type_item)
            
            # Original name
            source_name = os.path.basename(source) if source else source
            source_item = QTableWidgetItem(source_name)
            source_item.setToolTip(source)  # Full path on hover
            self.table.setItem(row, 1, source_item)
            
            # Arrow
            arrow_item = QTableWidgetItem("→")
            arrow_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 2, arrow_item)
            
            # New name
            dest_name = os.path.basename(destination) if destination else destination
            dest_item = QTableWidgetItem(dest_name)
            dest_item.setToolTip(destination)  # Full path on hover
            
            # Highlight if destination already exists
            if os.path.exists(destination):
                dest_item.setBackground(QColor("#fff3cd"))
                dest_item.setToolTip(f"{destination}\n⚠️ This file already exists and will be overwritten!")
            
            self.table.setItem(row, 3, dest_item)
    
    def _check_for_warnings(self) -> List[str]:
        """Check for potential issues with the renames."""
        warnings = []
        
        # Check for overwrites
        overwrites = sum(1 for r in self.renames if os.path.exists(r.get('destination', '')))
        if overwrites:
            warnings.append(f"{overwrites} file(s) will overwrite existing files")
        
        # Check for missing sources
        missing = sum(1 for r in self.renames if not os.path.exists(r.get('source', '')))
        if missing:
            warnings.append(f"{missing} source file(s) no longer exist")
        
        return warnings
    
    def _on_confirm(self):
        """Handle confirm button click."""
        # Double-check for overwrites
        overwrites = [r for r in self.renames if os.path.exists(r.get('destination', ''))]
        if overwrites:
            reply = QMessageBox.warning(
                self,
                "Confirm Overwrite",
                f"{len(overwrites)} file(s) will be overwritten. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        
        self.confirmed = True
        self.accept()
    
    def was_confirmed(self) -> bool:
        """Check if the user confirmed the renames."""
        return self.confirmed
    
    def _center_on_screen(self):
        """Center the dialog on the same screen as the parent window (Oracle), or primary screen if no parent."""
        try:
            from PyQt6.QtWidgets import QApplication
            from PyQt6.QtCore import QPoint
            
            app = QApplication.instance()
            if app:
                # Use parent's screen if available (Oracle window), otherwise use primary screen
                screen = None
                if self.parent():
                    screen = self.parent().screen()
                if not screen:
                    screen = app.primaryScreen()
                
                if screen:
                    screen_geometry = screen.geometry()
                    dialog_size = self.size()
                    
                    # Calculate center position
                    x = screen_geometry.x() + (screen_geometry.width() - dialog_size.width()) // 2
                    y = screen_geometry.y() + (screen_geometry.height() - dialog_size.height()) // 2
                    
                    # Ensure dialog is on screen (not off-screen)
                    x = max(screen_geometry.x(), min(x, screen_geometry.x() + screen_geometry.width() - dialog_size.width()))
                    y = max(screen_geometry.y(), min(y, screen_geometry.y() + screen_geometry.height() - dialog_size.height()))
                    
                    self.move(QPoint(x, y))
        except Exception:
            pass  # If centering fails, just use default position


def show_rename_preview(renames: List[Dict[str, str]], parent=None, title: str = "Rename Preview") -> bool:
    """
    Show a rename preview dialog and return whether the user confirmed.
    
    Args:
        renames: List of dicts with 'source', 'destination', and optional 'type' (file/folder)
                 Example: [{'source': '/path/old.txt', 'destination': '/path/new.txt', 'type': 'file'}]
        parent: Parent widget
        title: Dialog title
    
    Returns:
        True if user confirmed, False if cancelled
    """
    if not renames:
        logger.warning("show_rename_preview called with empty renames list")
        return False
    
    dialog = RenamePreviewDialog(renames, parent=parent, title=title)
    dialog.exec()
    
    result = dialog.was_confirmed()
    logger.info(f"Rename preview dialog: user {'confirmed' if result else 'cancelled'} {len(renames)} rename(s)")
    
    return result


def extract_renames_from_code(code: str) -> List[Dict[str, str]]:
    """
    Extract rename operations from Python code.
    
    Looks for patterns like:
    - os.rename(source, dest)
    - shutil.move(source, dest)
    - Path.rename(dest)
    
    Args:
        code: Python code to analyze
    
    Returns:
        List of rename operations that could be detected
    """
    import re
    
    renames = []
    
    # Pattern for os.rename(source, dest)
    os_rename_pattern = r'os\.rename\s*\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']\s*\)'
    for match in re.finditer(os_rename_pattern, code):
        renames.append({
            'source': match.group(1),
            'destination': match.group(2),
            'type': 'folder' if os.path.isdir(match.group(1)) else 'file'
        })
    
    # Pattern for shutil.move(source, dest)
    shutil_move_pattern = r'shutil\.move\s*\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']\s*\)'
    for match in re.finditer(shutil_move_pattern, code):
        renames.append({
            'source': match.group(1),
            'destination': match.group(2),
            'type': 'folder' if os.path.isdir(match.group(1)) else 'file'
        })
    
    return renames

