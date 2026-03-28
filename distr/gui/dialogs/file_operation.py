"""
File Operation Confirmation Dialog - Shows confirmation before destructive file operations.
Enhanced to support plan display and confirmation phrase requirement per spec.
"""

import logging
import os
from pathlib import Path
from typing import List, Tuple, Dict, Optional
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QTextEdit, QMessageBox, QLineEdit, QCheckBox
)
from PyQt6.QtCore import Qt

logger = logging.getLogger(__name__)


class FileOperationConfirmationDialog(QDialog):
    """Dialog for confirming file operations before execution."""
    
    def __init__(self, operation_type: str, operation_details: str, files_affected: List[str], 
                 plan: Optional[Dict] = None, require_confirmation_phrase: bool = True,
                 confirmation_phrase: str = "confirm file changes", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Confirm {operation_type}")
        self.setMinimumSize(700, 600)
        self.setModal(True)
        
        # Store confirmation requirements
        self.require_confirmation_phrase = require_confirmation_phrase
        self.confirmation_phrase = confirmation_phrase.lower()
        self.plan = plan
        
        # Set window flags to keep dialog on top
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowTitleHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        
        # Result
        self._confirmed = False
        
        # Layout
        layout = QVBoxLayout(self)
        
        # Warning icon and message
        warning_label = QLabel(f"WARNING: {operation_type.upper()} OPERATION")
        warning_label.setStyleSheet("""
            QLabel {
                color: #ffaa00;
                font-size: 18px;
                font-weight: bold;
                padding: 10px;
                background-color: #1a1a1a;
                border-radius: 4px;
            }
        """)
        layout.addWidget(warning_label)
        
        # Show plan if available
        if plan:
            plan_text = self._format_plan(plan)
            plan_label = QLabel("PLAN:")
            plan_label.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: bold; padding-top: 10px;")
            layout.addWidget(plan_label)
            
            plan_text_widget = QTextEdit()
            plan_text_widget.setReadOnly(True)
            plan_text_widget.setMinimumHeight(300)  # Increased height to show more content
            plan_text_widget.setMaximumHeight(500)  # Allow more space for plan/outcome/code
            plan_text_widget.setStyleSheet("""
                QTextEdit {
                    background-color: #0e1638;
                    color: #ececf1;
                    border: 1px solid #565869;
                    border-radius: 4px;
                    font-family: 'Monaco', 'Courier New', monospace;
                    font-size: 11px;
                    padding: 8px;
                }
            """)
            plan_text_widget.setPlainText(plan_text)
            layout.addWidget(plan_text_widget)
        else:
            # Operation details (fallback if no plan)
            details_label = QLabel(operation_details)
            details_label.setWordWrap(True)
            details_label.setStyleSheet("color: #ffffff; font-size: 14px; padding: 8px;")
            layout.addWidget(details_label)
        
        # Files affected section removed - information is now in the PLAN section at the top
        
        # Confirmation phrase input
        if self.require_confirmation_phrase:
            phrase_label = QLabel(f"Type '{self.confirmation_phrase}' to confirm:")
            phrase_label.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: bold; padding-top: 10px;")
            layout.addWidget(phrase_label)
            
            self.phrase_input = QLineEdit()
            self.phrase_input.setStyleSheet("""
                QLineEdit {
                    background-color: #0e1638;
                    color: #ececf1;
                    border: 1px solid #565869;
                    border-radius: 4px;
                    padding: 8px;
                    font-size: 14px;
                }
            """)
            self.phrase_input.textChanged.connect(self._check_phrase)
            # Connect Enter key in text field to trigger confirm if button is enabled
            self.phrase_input.returnPressed.connect(self._on_enter_pressed)
            layout.addWidget(self.phrase_input)
        
        # "Don't show this again" checkbox
        self.dont_show_again_checkbox = QCheckBox("Don't show this again")
        self.dont_show_again_checkbox.setStyleSheet("""
            QCheckBox {
                color: #ececf1;
                font-size: 12px;
                padding: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 1px solid #565869;
                border-radius: 3px;
                background-color: #0e1638;
            }
            QCheckBox::indicator:checked {
                background-color: #00aaff;
                border-color: #00aaff;
            }
        """)
        layout.addWidget(self.dont_show_again_checkbox)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        self.cancel_button.setStyleSheet("""
            QPushButton {
                background-color: #454655;
                color: #ececf1;
                border: 1px solid #565869;
                padding: 10px 20px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 14px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #565869;
            }
            QPushButton:pressed {
                background-color: #2d2d3a;
            }
        """)
        button_layout.addWidget(self.cancel_button)
        
        self.confirm_button = QPushButton(f"Confirm {operation_type}")
        self.confirm_button.clicked.connect(self._on_confirm)
        self.confirm_button.setEnabled(not self.require_confirmation_phrase)  # Disabled until phrase matches
        self.confirm_button.setDefault(True)  # Make it the default button so Enter activates it
        self.confirm_button.setStyleSheet("""
            QPushButton {
                background-color: #ff4444;
                color: #ffffff;
                border: 1px solid #ff4444;
                padding: 10px 20px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 14px;
                min-width: 100px;
            }
            QPushButton:hover:enabled {
                background-color: #ff6666;
            }
            QPushButton:pressed:enabled {
                background-color: #ff2222;
            }
            QPushButton:disabled {
                background-color: #2d2d3a;
                color: #666666;
                border-color: #333333;
            }
        """)
        button_layout.addWidget(self.confirm_button)
        
        layout.addLayout(button_layout)
        
        # Apply dark theme
        self.setStyleSheet("""
            QDialog {
                background-color: #0e1638;
                color: #ececf1;
            }
        """)
        
        # Set a reasonable size before centering
        self.resize(800, 600)
        
        # Center on screen
        self._center_on_screen()
        
        # Ensure dialog is visible
        self.setModal(True)
        logger.info(f"Dialog initialized: size={self.size()}, position={self.pos()}")
    
    def _format_plan(self, plan: Dict) -> str:
        """Format plan dict as readable text. Plan and Outcome at top, Code at bottom."""
        lines = []
        
        # ============================================================
        # TOP SECTION: PLAN AND OUTCOME
        # ============================================================
        
        # Show intent FIRST and prominently
        intent = plan.get('intent', 'N/A')
        lines.append(f"{'='*60}")
        lines.append(f"PLAN:")
        lines.append(f"{'='*60}")
        lines.append(f"INTENT: {intent}")
        lines.append("")
        
        # Show predicted outcome if available (from LLM analysis)
        if plan.get('predicted_outcome'):
            lines.append("PREDICTED OUTCOME:")
            lines.append(plan['predicted_outcome'])
            lines.append("")
        
        # Show outcome summary (what will actually happen)
        if plan.get('outcome_summary'):
            lines.append("WHAT WILL HAPPEN:")
            lines.append(plan['outcome_summary'])
            lines.append("")
        
        # Show operation-specific counts for better clarity
        has_dynamic = any(op.get('is_dynamic') for op in plan.get('operations', []))
        
        # Get operation-specific counts
        files_to_create = plan.get('files_to_create', 0)
        files_to_modify = plan.get('files_to_modify', 0)
        files_to_delete = plan.get('files_to_delete', 0)
        files_to_move = plan.get('files_to_move', 0)
        files_to_copy = plan.get('files_to_copy', 0)
        files_to_rename = plan.get('files_to_rename', 0)
        
        # Show counts based on what operations will actually do
        operation_counts = []
        if files_to_create > 0:
            operation_counts.append(f"Will create: {files_to_create} file(s)")
        if files_to_modify > 0:
            operation_counts.append(f"Will modify: {files_to_modify} file(s)")
        if files_to_rename > 0:
            operation_counts.append(f"Will rename: {files_to_rename} file(s)")
        if files_to_move > 0:
            operation_counts.append(f"Will move: {files_to_move} file(s)")
        if files_to_copy > 0:
            operation_counts.append(f"Will copy: {files_to_copy} file(s)")
        if files_to_delete > 0:
            operation_counts.append(f"Will delete: {files_to_delete} file(s)")
        
        if operation_counts:
            lines.append("OPERATION SUMMARY:")
            for count_line in operation_counts:
                lines.append(f"  • {count_line}")
            lines.append("")
        
        # Show rename preview if available (especially important for cleanup operations)
        if plan.get('renames') and len(plan.get('renames', [])) > 0:
            lines.append(f"RENAME OPERATIONS ({len(plan['renames'])} file(s)/folder(s) will be renamed):")
            for i, rename in enumerate(plan['renames'][:20], 1):  # Show up to 20 renames
                source = rename.get('source', '')
                dest = rename.get('destination', '')
                if source and dest:
                    source_name = os.path.basename(source)
                    dest_name = os.path.basename(dest)
                    lines.append(f"  {i}. {source_name} → {dest_name}")
            if len(plan['renames']) > 20:
                lines.append(f"  ... and {len(plan['renames']) - 20} more renames")
            lines.append("")
        
        # Filter out placeholder strings from file lists
        overwrite_files = [f for f in plan.get('overwrite_files', []) 
                          if f and f not in ['(dynamic)', '(dynamic path)', '$target'] and not f.startswith('$')]
        delete_files = [f for f in plan.get('delete_files', []) 
                       if f and f not in ['(dynamic)', '(dynamic path)', '$target'] and not f.startswith('$')]
        
        # Show specific files that will be created, renamed, or deleted
        created_files = [op.get('destination') or op.get('source') for op in plan.get('operations', []) 
                        if op.get('type') == 'WRITE' and not Path(op.get('destination') or op.get('source', '')).exists()]
        renamed_files = [(op.get('source'), op.get('destination')) for op in plan.get('operations', []) 
                        if op.get('type') == 'MOVE' and op.get('source') and op.get('destination')]
        deleted_files = [op.get('source') for op in plan.get('operations', []) 
                        if op.get('type') == 'DELETE' and op.get('source')]
        
        if created_files:
            lines.append("Files to be created:")
            for f in created_files[:10]:
                if f and f not in ['(dynamic)', '(dynamic path)', '$target'] and not f.startswith('$'):
                    lines.append(f"  • {os.path.basename(f)}")
            if len(created_files) > 10:
                lines.append(f"  ... and {len(created_files) - 10} more")
            lines.append("")
        
        if renamed_files:
            lines.append("Files to be renamed:")
            for old, new in renamed_files[:10]:
                if old and new and old not in ['(dynamic)', '(dynamic path)', '$target']:
                    lines.append(f"  • {os.path.basename(old)} → {os.path.basename(new)}")
            if len(renamed_files) > 10:
                lines.append(f"  ... and {len(renamed_files) - 10} more")
            lines.append("")
        
        if deleted_files:
            lines.append("Files/Folders to be deleted:")
            for f in deleted_files[:10]:
                if f and f not in ['(dynamic)', '(dynamic path)', '$target'] and not f.startswith('$'):
                    lines.append(f"  • {os.path.basename(f)}")
            if len(deleted_files) > 10:
                lines.append(f"  ... and {len(deleted_files) - 10} more")
            lines.append("")
        
        if plan.get('will_overwrite'):
            if overwrite_files:
                lines.append(f"WARNING: Will overwrite {len(overwrite_files)} file(s):")
                for f in overwrite_files[:10]:
                    lines.append(f"  - {os.path.basename(f)}")
                if len(overwrite_files) > 10:
                    lines.append(f"  ... and {len(overwrite_files) - 10} more")
            else:
                # All paths are dynamic - show count from operations
                dynamic_overwrites = sum(1 for op in plan.get('operations', []) 
                                        if op.get('will_overwrite') and op.get('is_dynamic'))
                if dynamic_overwrites > 0:
                    lines.append(f"WARNING: Will overwrite {dynamic_overwrites} file(s) (paths determined at runtime)")
            lines.append("")
        
        if plan.get('will_delete'):
            if delete_files:
                lines.append(f"WARNING: Will delete {len(delete_files)} file(s):")
                for f in delete_files[:10]:
                    lines.append(f"  - {os.path.basename(f)}")
                if len(delete_files) > 10:
                    lines.append(f"  ... and {len(delete_files) - 10} more")
            else:
                # All paths are dynamic - show count from operations
                dynamic_deletes = sum(1 for op in plan.get('operations', []) 
                                     if op.get('will_delete') and op.get('is_dynamic'))
                if dynamic_deletes > 0:
                    lines.append(f"WARNING: Will delete {dynamic_deletes} file(s) (paths determined at runtime)")
            lines.append("")
        
        # Show warning if paths are dynamic (could not be extracted)
        if plan.get('warning'):
            lines.append(f"WARNING: {plan.get('warning')}")
            lines.append("")
        
        # Rollback strategy
        lines.append("Rollback strategy:")
        lines.append(plan.get('rollback_strategy', 'N/A'))
        lines.append("")
        
        # ============================================================
        # BOTTOM SECTION: CODE TO EXECUTE
        # ============================================================
        
        # Show code preview at the bottom
        code_preview = plan.get('code_preview', '')
        if not code_preview:
            # Try to get code from operations
            for op in plan.get('operations', []):
                if op.get('code_snippet'):
                    code_preview = op.get('code_snippet')
                    break
        
        # Also check operations for code snippets
        if not code_preview:
            for op in plan.get('operations', []):
                if op.get('code_snippet'):
                    code_preview = op.get('code_snippet', '')
                    break
        
        if code_preview or has_dynamic:
            lines.append(f"{'='*60}")
            lines.append("CODE TO EXECUTE:")
            if has_dynamic:
                lines.append("(review carefully - paths are determined dynamically at runtime)")
            lines.append(f"{'='*60}")
            
            if code_preview:
                # Limit preview length but show more for dynamic operations
                max_length = 3000 if has_dynamic else 1500
                if len(code_preview) > max_length:
                    code_preview = code_preview[:max_length] + "\n... (truncated - see full code in execution log)"
                lines.append(code_preview)
            else:
                lines.append("(Code preview not available)")
            lines.append(f"{'='*60}")
        
        return "\n".join(lines)
    
    def _check_phrase(self):
        """Check if entered phrase matches required phrase."""
        if not self.require_confirmation_phrase:
            return
        
        entered = self.phrase_input.text().strip().lower()
        matches = entered == self.confirmation_phrase
        self.confirm_button.setEnabled(matches)
        logger.info(f"[DIALOG] _check_phrase: entered='{entered}', required='{self.confirmation_phrase}', matches={matches}, button_enabled={matches}")
    
    def _on_enter_pressed(self):
        """Handle Enter key press in phrase input."""
        logger.info("[DIALOG] Enter pressed in phrase input")
        if self.confirm_button.isEnabled():
            logger.info("[DIALOG] Button is enabled, triggering confirm")
            self._on_confirm()
        else:
            logger.info("[DIALOG] Button is disabled, Enter ignored")
    
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
                    logger.info(f"Dialog positioned at ({x}, {y}) on screen {screen_geometry}")
        except Exception as e:
            logger.error(f"Error centering dialog: {e}", exc_info=True)
            # Fallback: try to move to a safe position
            try:
                self.move(100, 100)
            except Exception:
                pass
    
    def _on_confirm(self):
        """Handle confirm button click."""
        logger.info(f"[DIALOG] _on_confirm CALLED! Button was clicked.")
        
        # Double-check phrase if required
        if self.require_confirmation_phrase:
            entered = self.phrase_input.text().strip().lower()
            logger.info(f"[DIALOG] Checking phrase: entered='{entered}', required='{self.confirmation_phrase}'")
            if entered != self.confirmation_phrase:
                # Use a non-modal message box to avoid nested event loop issues
                # that can cause crashes when called from within exec()
                try:
                    msg = QMessageBox(self)
                    msg.setIcon(QMessageBox.Icon.Warning)
                    msg.setWindowTitle("Confirmation Required")
                    msg.setText(f"Please type '{self.confirmation_phrase}' exactly to confirm.")
                    msg.setStandardButtons(QMessageBox.StandardButton.Ok)
                    msg.setModal(False)  # Non-modal to avoid nested event loop
                    msg.setWindowFlags(msg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
                    msg.show()
                    msg.raise_()
                    msg.activateWindow()
                    # Don't use exec() - just show it and let it close when user clicks OK
                    # The dialog will close itself when OK is clicked
                except Exception as e:
                    logger.error(f"Error showing confirmation phrase warning: {e}", exc_info=True)
                    # Fallback: just log the error
                    logger.warning(f"Please type '{self.confirmation_phrase}' exactly to confirm.")
                return
        
        # Save "Don't show again" preference if checked
        if self.dont_show_again_checkbox.isChecked():
            try:
                from distr.core.settings import load_settings_from_db, save_settings_to_db
                settings = load_settings_from_db()
                settings['initiative_ask_file_changes'] = False
                save_settings_to_db(settings)
                logger.info("User disabled file operation confirmations")
            except Exception as e:
                logger.error(f"Error saving 'don't show again' preference: {e}")
        
        self._confirmed = True
        logger.info(f"[DIALOG] _confirmed set to True! User has confirmed.")
        
        # CRITICAL: Defer accept() call using QTimer to avoid calling it directly
        # from within the button click handler, which can cause crashes when
        # the dialog is shown via exec() from a QTimer callback
        try:
            from PyQt6.QtCore import QTimer
            from PyQt6.QtWidgets import QDialog
            
            # Use QTimer to defer the accept() call to the next event loop iteration
            # This prevents nested event loop issues that can crash the application
            def deferred_accept():
                """Defer accept() to avoid event loop conflicts."""
                try:
                    self.accept()
                except Exception as e:
                    logger.error(f"Error calling dialog.accept() in deferred call: {e}", exc_info=True)
                    # Try alternative close method
                    try:
                        self.done(QDialog.DialogCode.Accepted)
                    except Exception as e2:
                        logger.error(f"Error calling dialog.done() in deferred call: {e2}", exc_info=True)
                        # Last resort - just close
                        try:
                            self.close()
                        except Exception:
                            pass

            # Schedule accept() for the next event loop iteration
            QTimer.singleShot(0, deferred_accept)
            
        except Exception as e:
            logger.error(f"Error setting up deferred accept(): {e}", exc_info=True)
            # Fallback: try immediate accept (less safe but better than nothing)
            try:
                self.accept()
            except Exception as e2:
                logger.error(f"Error calling dialog.accept() in fallback: {e2}", exc_info=True)
                try:
                    from PyQt6.QtWidgets import QDialog
                    self.done(QDialog.DialogCode.Accepted)
                except Exception:
                    try:
                        self.close()
                    except Exception:
                        pass

    def is_confirmed(self) -> bool:
        """Check if operation was confirmed."""
        return self._confirmed


def check_file_operations_require_confirmation(code: str) -> List[Tuple[str, str, List[str]]]:
    """
    Check if code contains file operations that require confirmation.
    
    Returns:
        List of tuples: (operation_type, operation_details, files_affected)
        Empty list if no dangerous operations found.
    """
    import re
    import os
    
    operations = []
    
    # Patterns for dangerous file operations
    patterns = [
        # Remove/delete operations
        (r'\brm\s+-[rf]*\s+([^\s;|&]+)', 'DELETE', 'remove'),
        (r'\brm\s+([^\s;|&]+)', 'DELETE', 'remove'),
        (r'\bunlink\s*\(["\']([^"\']+)["\']', 'DELETE', 'unlink'),
        (r'\bos\.remove\s*\(["\']([^"\']+)["\']', 'DELETE', 'os.remove'),
        (r'\bos\.unlink\s*\(["\']([^"\']+)["\']', 'DELETE', 'os.unlink'),
        (r'\bpathlib\.Path\s*\(["\']([^"\']+)["\']\)\.unlink\s*\(\)', 'DELETE', 'Path.unlink'),
        (r'\bshutil\.rmtree\s*\(["\']([^"\']+)["\']', 'DELETE', 'shutil.rmtree'),
        
        # Move/rename operations
        (r'\bmv\s+([^\s;|&]+)\s+([^\s;|&]+)', 'MOVE', 'move'),
        (r'\bos\.rename\s*\(["\']([^"\']+)["\'],\s*["\']([^"\']+)["\']', 'MOVE', 'os.rename'),
        (r'\bshutil\.move\s*\(["\']([^"\']+)["\'],\s*["\']([^"\']+)["\']', 'MOVE', 'shutil.move'),
        
        # Copy operations (less dangerous but should confirm)
        # Note: Order matters - check cp with flags first, then without
        (r'\bcp\s+-[rf]*\s+([^\s;|&]+)\s+([^\s;|&]+)', 'COPY', 'copy'),  # cp with flags
        (r'\bcp\s+([^\s;|&]+)\s+([^\s;|&]+)', 'COPY', 'copy'),  # cp without flags
        (r'\bshutil\.copy\s*\(["\']([^"\']+)["\'],\s*["\']([^"\']+)["\']', 'COPY', 'shutil.copy'),
        (r'\bshutil\.copy2\s*\(["\']([^"\']+)["\'],\s*["\']([^"\']+)["\']', 'COPY', 'shutil.copy2'),
        (r'\bshutil\.copytree\s*\(["\']([^"\']+)["\'],\s*["\']([^"\']+)["\']', 'COPY', 'shutil.copytree'),
    ]
    
    for pattern, op_type, op_name in patterns:
        matches = re.finditer(pattern, code, re.IGNORECASE | re.MULTILINE)
        for match in matches:
            groups = match.groups()
            if op_type == 'DELETE':
                file_path = groups[0] if groups else match.group(1)
                # Resolve path if it's a variable or needs expansion
                try:
                    if file_path.startswith('~'):
                        file_path = os.path.expanduser(file_path)
                    elif not os.path.isabs(file_path):
                        # Try to resolve relative paths
                        file_path = os.path.abspath(file_path)
                except (OSError, ValueError):
                    pass
                
                operations.append((
                    op_type,
                    f"Delete file: {file_path}",
                    [file_path] if os.path.exists(file_path) else [file_path]
                ))
            elif op_type in ('MOVE', 'COPY'):
                src = groups[0] if groups else match.group(1)
                dst = groups[1] if len(groups) > 1 else match.group(2)
                
                # Resolve paths
                try:
                    if src.startswith('~'):
                        src = os.path.expanduser(src)
                    if dst.startswith('~'):
                        dst = os.path.expanduser(dst)
                except (OSError, ValueError):
                    pass
                
                operations.append((
                    op_type,
                    f"{op_name.capitalize()}: {src} -> {dst}",
                    [src, dst]
                ))
    
    return operations


def confirm_file_operations(operations: List[Tuple[str, str, List[str]]], 
                           plan: Optional[Dict] = None,
                           require_confirmation_phrase: bool = True,
                           confirmation_phrase: str = "confirm file changes") -> bool:
    """
    Show confirmation dialog for file operations.
    
    Args:
        operations: List of (operation_type, details, files) tuples
        plan: Optional plan dict from file_safety module
        require_confirmation_phrase: Whether to require exact confirmation phrase
        confirmation_phrase: The exact phrase required to confirm
        
    Returns:
        True if confirmed, False if cancelled
    """
    if not operations:
        return True  # No operations to confirm
    
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QCoreApplication
        
        # Check if we have QApplication
        app_instance = QCoreApplication.instance()
        if app_instance is None:
            # No GUI - can't show dialog, default to deny for safety
            logger.warning("No QApplication - denying file operations for safety")
            return False
        
        is_qapplication = isinstance(app_instance, QApplication) if app_instance else False
        if not is_qapplication:
            # Only QCoreApplication - can't show dialog, default to deny for safety
            logger.warning("No QApplication (only QCoreApplication) - denying file operations for safety")
            return False
        
        # Group operations by type
        from collections import defaultdict
        grouped = defaultdict(list)
        for op_type, details, files in operations:
            grouped[op_type].append((details, files))
        
        # Show dialog for each operation type
        for op_type, op_list in grouped.items():
            all_files = []
            all_details = []
            for details, files in op_list:
                all_files.extend(files)
                all_details.append(details)
            
            dialog = FileOperationConfirmationDialog(
                operation_type=op_type,
                operation_details="\n".join(all_details[:5]),  # Show first 5 details
                files_affected=list(set(all_files)),  # Remove duplicates
                plan=plan,
                require_confirmation_phrase=require_confirmation_phrase,
                confirmation_phrase=confirmation_phrase
            )
            
            result = dialog.exec()
            if result != QDialog.DialogCode.Accepted:
                logger.info(f"User cancelled {op_type} operation")
                return False
        
        return True
        
    except Exception as e:
        logger.error(f"Error showing file operation confirmation: {e}", exc_info=True)
        # On error, default to deny for safety
        return False


def confirm_file_operations_with_plan(plan: Dict, 
                                      require_confirmation_phrase: bool = True,
                                      confirmation_phrase: str = "confirm file changes",
                                      parent_window=None) -> bool:
    """
    Show confirmation dialog using a plan from file_safety module.
    
    Args:
        plan: Plan dict from file_safety.generate_plan()
        require_confirmation_phrase: Whether to require exact confirmation phrase
        confirmation_phrase: The exact phrase required to confirm
        
    Returns:
        True if confirmed, False if cancelled
    """
    if not plan or not plan.get('operations'):
        return True  # No operations to confirm
    
    # Check if user has disabled confirmations in settings
    # Respect user's preference - if disabled, auto-approve all operations
    try:
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        always_confirm = settings.get('initiative_ask_file_changes', True)
        
        # If user has disabled confirmations, auto-approve all operations
        if not always_confirm:
            logger.info("File operation confirmations disabled in settings - auto-approving all operations")
            return True  # Auto-approve if disabled
    except Exception as e:
        logger.debug(f"Could not check file operation confirmation setting: {e}")
        # Continue to show dialog if we can't check the setting (default to requiring confirmation)
    
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QCoreApplication
        
        # Check if we have QApplication
        app_instance = QCoreApplication.instance()
        if app_instance is None:
            logger.warning("No QApplication - denying file operations for safety")
            return False
        
        is_qapplication = isinstance(app_instance, QApplication) if app_instance else False
        if not is_qapplication:
            logger.warning("No QApplication (only QCoreApplication) - denying file operations for safety")
            return False
        
        # Ensure we're in the main thread and process events before showing dialog
        app_instance.processEvents()
        
        # Determine operation type from plan
        has_delete = plan.get('will_delete', False)
        has_overwrite = plan.get('will_overwrite', False)
        
        if has_delete:
            op_type = "DELETE"
        elif has_overwrite:
            op_type = "WRITE"
        else:
            op_type = "WRITE"
        
        # Get all affected files
        all_files = plan.get('files', []) + plan.get('directories', [])
        
        # Create and show dialog - use simpler approach to avoid bus errors
        dialog = None
        try:
            # Ensure QApplication is fully initialized
            import time
            app_instance.processEvents()
            time.sleep(0.05)  # Small delay to ensure GUI is ready
            app_instance.processEvents()
            
            # Use provided parent_window, or try to find the main window as parent
            if parent_window is None:
                try:
                    from PyQt6.QtWidgets import QApplication
                    app = QApplication.instance()
                    if app:
                        # Try to find the main window
                        for widget in app.allWidgets():
                            if widget.isWindow() and widget.isVisible():
                                parent_window = widget
                                break
                except Exception:
                    pass
            
            logger.info(f"Creating file operation confirmation dialog: {op_type}, {len(all_files)} files (parent: {parent_window})")
            dialog = FileOperationConfirmationDialog(
                operation_type=op_type,
                operation_details=plan.get('intent', 'File operations'),
                files_affected=all_files,
                plan=plan,
                require_confirmation_phrase=require_confirmation_phrase,
                confirmation_phrase=confirmation_phrase,
                parent=parent_window  # Set parent to ensure proper window management
            )
            
            # Ensure dialog is visible and on top
            dialog.setWindowFlags(dialog.windowFlags() | dialog.windowFlags().WindowStaysOnTopHint)
            dialog.raise_()
            dialog.activateWindow()
            
            # Process events after creation
            app_instance.processEvents()
            
            # Show dialog explicitly before exec()
            dialog.show()
            app_instance.processEvents()
            
            logger.info(f"Dialog created and shown, waiting for user response...")
            
            # Use exec() which handles the event loop properly
            # This is the safest way to show a modal dialog
            # BUT: If called from a QTimer callback, it can cause nested event loop issues
            # So we wrap it very carefully and ensure proper cleanup
            result = None
            try:
                # Ensure we're in the main thread before calling exec()
                from PyQt6.QtCore import QThread
                current_thread = QThread.currentThread()
                main_thread = app_instance.thread()
                
                if current_thread != main_thread:
                    logger.error("Dialog.exec() called from non-main thread - this will cause issues!")
                    return False
                
                # CRITICAL: Set dialog as active window and ensure it has focus before exec()
                # This helps prevent event loop conflicts
                dialog.raise_()
                dialog.activateWindow()
                dialog.setFocus()
                
                # Process events before exec() to ensure dialog is fully ready
                app_instance.processEvents()
                
                # Call exec() with additional safety
                # exec() will block until user responds, but it should be safe if we're in main thread
                # CRITICAL: Don't do anything after exec() returns except check the result
                # Processing events or accessing the dialog after exec() can cause crashes
                result = dialog.exec()
                logger.info(f"Dialog closed with result: {result} (QDialog.DialogCode.Accepted={QDialog.DialogCode.Accepted})")
                
                # CRITICAL: Check both exec() result AND dialog.is_confirmed() because deferred_accept()
                # might cause exec() to return before accept() is fully processed
                # If either indicates confirmation, treat it as confirmed
                # Give a tiny moment for deferred_accept() to complete if it was scheduled
                app_instance.processEvents()
                
                is_confirmed = dialog.is_confirmed() if dialog else False
                logger.info(f"Dialog is_confirmed() check: {is_confirmed} (after processEvents)")
                logger.info(f"Dialog exec() result: {result}, QDialog.DialogCode.Accepted={QDialog.DialogCode.Accepted}")
                
                # Use is_confirmed() as the primary check since deferred_accept() sets _confirmed=True
                # before calling accept(), so it's more reliable
                if is_confirmed:
                    result = QDialog.DialogCode.Accepted
                    logger.info("Using is_confirmed() result: True (overriding exec() result)")
                elif result == QDialog.DialogCode.Accepted:
                    logger.info("Using exec() result: Accepted")
                else:
                    logger.warning(f"Dialog result indicates rejection: exec()={result}, is_confirmed()={is_confirmed}")
                
                # Immediately check result and return - don't process events or clean up here
                # The dialog will be cleaned up by Qt's garbage collection
                
            except RuntimeError as runtime_error:
                # RuntimeError often indicates Qt event loop issues
                logger.error(f"RuntimeError during dialog.exec() (likely event loop issue): {runtime_error}", exc_info=True)
                # Try to get result from dialog state - use is_confirmed() as primary check
                try:
                    if dialog and dialog.is_confirmed():
                        result = QDialog.DialogCode.Accepted
                        logger.info("Dialog confirmed despite exec() RuntimeError (checked is_confirmed())")
                    else:
                        result = QDialog.DialogCode.Rejected
                        logger.info("Dialog rejected due to exec() RuntimeError (is_confirmed()=False)")
                except Exception as check_error:
                    logger.error(f"Error checking is_confirmed() after RuntimeError: {check_error}", exc_info=True)
                    result = QDialog.DialogCode.Rejected
                    logger.warning("Could not determine dialog result after exec() RuntimeError")
            except Exception as exec_error:
                logger.error(f"Error during dialog.exec(): {exec_error}", exc_info=True)
                # If exec() fails, try to get result from dialog state - use is_confirmed() as primary check
                try:
                    if dialog and dialog.is_confirmed():
                        result = QDialog.DialogCode.Accepted
                        logger.info("Dialog confirmed despite exec() error (checked is_confirmed())")
                    else:
                        result = QDialog.DialogCode.Rejected
                        logger.info("Dialog rejected due to exec() error (is_confirmed()=False)")
                except Exception as check_error:
                    logger.error(f"Error checking is_confirmed() after exec() error: {check_error}", exc_info=True)
                    result = QDialog.DialogCode.Rejected
                    logger.warning("Could not determine dialog result after exec() error")
            
            # CRITICAL: Do NOT process events or clean up dialog after exec() returns
            # This can cause crashes because the dialog's event loop has already ended
            # The dialog will be automatically cleaned up by Qt when it goes out of scope
            # Only check the result and return immediately
            
            if result != QDialog.DialogCode.Accepted:
                logger.info(f"User cancelled {op_type} operation")
                return False
            
            return True
            
        except Exception as dialog_error:
            logger.error(f"Error creating/showing dialog: {dialog_error}", exc_info=True)
            # Try to show a simple message box as fallback
            try:
                from PyQt6.QtWidgets import QMessageBox
                msg = QMessageBox()
                msg.setIcon(QMessageBox.Icon.Warning)
                msg.setWindowTitle("File Operation Confirmation")
                msg.setText(f"File operations detected:\n{plan.get('intent', 'Unknown')}\n\n{len(all_files)} file(s) will be affected.")
                msg.setInformativeText("This is a fallback dialog. The full confirmation dialog failed to show.")
                msg.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
                result = msg.exec()
                return result == QMessageBox.StandardButton.Ok
            except Exception:
                # If even fallback fails, deny for safety
                logger.error("Even fallback dialog failed - denying operation")
                return False
        
    except Exception as e:
        logger.error(f"Error showing file operation confirmation: {e}", exc_info=True)
        return False

