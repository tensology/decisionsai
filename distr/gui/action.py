from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QIcon, QColor, QPainter, QFontMetrics, QFont
from PyQt6.QtWidgets import (
    QMessageBox, QInputDialog, QApplication, QGridLayout, QSizePolicy,
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QPushButton, QListWidget, QListWidgetItem,
    QMenu, QStyledItemDelegate, QCheckBox
)
from distr.core.db import get_session, Action
from distr.core.constants import ICONS_DIR
import os
import json
from datetime import datetime
from distr.gui.styles.actionwindowstyles import ActionWindowStyles

class SafeLineEdit(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)
        self.installEventFilter(self)
        self.initial_text = ""
    
    def setText(self, text):
        super().setText(text)
        self.initial_text = text
    
    def focusOutEvent(self, event):
        if self.parent() and hasattr(self.parent(), 'parent_window'):
            text = self.text().strip()
            if text and text != self.initial_text:
                self.parent().parent_window.finish_renaming()
            else:
                self.parent().parent_window.cancel_renaming()
        super().focusOutEvent(event)

    def eventFilter(self, obj, event):
        if event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.parent().parent_window.cancel_renaming()
                return True
            elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.parent().parent_window.finish_renaming()
                return True
        return False

    def inputMethodEvent(self, event):
        event.ignore()

class TagWidget(QWidget):
    deleted = pyqtSignal(str)
    edited = pyqtSignal(str, str)

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = text
        self.setup_ui()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(5)

        # Tag text
        self.label = QLabel(self.text)
        self.label.setStyleSheet(ActionWindowStyles.TAG_LABEL)

        # Delete button
        self.delete_btn = QPushButton("×")
        self.delete_btn.setFixedSize(16, 16)
        self.delete_btn.setStyleSheet(ActionWindowStyles.TAG_DELETE_BUTTON)
        self.delete_btn.clicked.connect(lambda: self.deleted.emit(self.text))

        layout.addWidget(self.label)
        layout.addWidget(self.delete_btn)
        layout.addStretch()

    def mouseDoubleClickEvent(self, event):
        new_text, ok = QInputDialog.getText(
            self, "Edit Trigger Word", 
            "Edit trigger word:", 
            QLineEdit.EchoMode.Normal, 
            self.text
        )
        if ok and new_text.strip():
            old_text = self.text
            self.text = new_text.strip()
            self.label.setText(self.text)
            self.edited.emit(old_text, self.text)

class TagListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSpacing(5)
        self.setStyleSheet(ActionWindowStyles.TAG_LIST)
        self.setup_ui()

    def setup_ui(self):
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.setAcceptDrops(False)

    def add_tag(self, text):
        item = QListWidgetItem(self)
        widget = TagWidget(text)
        widget.deleted.connect(lambda t: self.remove_tag(t))
        widget.edited.connect(self.edit_tag)
        item.setSizeHint(widget.sizeHint())
        self.addItem(item)
        self.setItemWidget(item, widget)

    def remove_tag(self, text):
        for i in range(self.count()):
            item = self.item(i)
            widget = self.itemWidget(item)
            if widget.text == text:
                self.takeItem(i)
                break

    def edit_tag(self, old_text, new_text):
        for i in range(self.count()):
            item = self.item(i)
            widget = self.itemWidget(item)
            if widget.text == old_text:
                widget.text = new_text
                widget.label.setText(new_text)
                break

    def get_tags(self):
        return [self.itemWidget(self.item(i)).text 
                for i in range(self.count())]

class ActionListWidget(QListWidget):
    action_selected = pyqtSignal(int)
    rename_requested = pyqtSignal(QListWidgetItem)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.active_item = None
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_active_item(self, item):
        self.active_item = item
        self.viewport().update()

    def mouseDoubleClickEvent(self, event):
        item = self.itemAt(event.pos())
        if item and item.flags() & Qt.ItemFlag.ItemIsSelectable:
            self.rename_requested.emit(item)
        else:
            super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if self.parent_window.is_renaming:
            try:
                text = None
                if self.parent_window.rename_editor:
                    text = self.parent_window.rename_editor.text().strip()
                
                if text:
                    self.parent_window.finish_renaming()
                else:
                    self.parent_window.cancel_renaming()
            except Exception as e:
                print(f"Error handling rename during mouse press: {e}")
                self.parent_window.cancel_renaming()
            finally:
                event.accept()
                return

        item = self.itemAt(event.pos())
        current = self.currentItem()
        
        # Always maintain current selection
        if current and current.flags() & Qt.ItemFlag.ItemIsSelectable:
            self.setCurrentItem(current)
        
        # Only process new selections for action items
        if item and item.flags() & Qt.ItemFlag.ItemIsSelectable:
            if item != current:
                super().mousePressEvent(event)
                self.parent_window.on_action_selected(item)
        
        event.accept()

    def mouseReleaseEvent(self, event):
        # Maintain selection on mouse release
        current = self.currentItem()
        if current and current.flags() & Qt.ItemFlag.ItemIsSelectable:
            self.setCurrentItem(current)
        event.accept()

    def mouseMoveEvent(self, event):
        # Prevent text selection by accepting the event
        event.accept()

    def maintain_selection(self):
        """Ensures the current selection is maintained"""
        current = self.currentItem()
        if not current or not (current.flags() & Qt.ItemFlag.ItemIsSelectable):
            # Find the last selected item
            for i in range(self.count()):
                item = self.item(i)
                if item and item.flags() & Qt.ItemFlag.ItemIsSelectable:
                    self.setCurrentItem(item)
                    break

class ActionItemDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.renaming_item = None

    def paint(self, painter, option, index):
        if index.row() == self.renaming_item:
            painter.fillRect(option.rect, QColor("#2d2d3a"))
        super().paint(painter, option, index)

class ActionWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.current_action_id = None
        self.is_renaming = False
        self.rename_editor = None
        self.action_item_delegate = None
        
        # Initialize all UI elements first
        self._setup_window()
        
        # Load actions after UI is fully set up
        QTimer.singleShot(0, self.load_actions)

    def _setup_window(self):
        # Update window sizing to match SnippetWindow
        self.setWindowTitle("Actions Manager")
        self.setGeometry(100, 100, 1000, 600)

        # Center the window on the screen
        screen = QApplication.primaryScreen().geometry()
        window_geometry = self.geometry()
        x = (screen.width() - window_geometry.width()) // 2
        y = (screen.height() - window_geometry.height()) // 2
        self.move(x, y)

        # Apply base styles
        self.setStyleSheet(ActionWindowStyles.MAIN_WINDOW)

        # Main layout
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QHBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Left side: Action list
        self._setup_left_panel()
        
        # Right side: Action editor
        self._setup_right_panel()

    def _setup_left_panel(self):
        self.left_widget = QWidget()
        self.left_widget.setFixedWidth(200)
        self.left_layout = QVBoxLayout(self.left_widget)
        self.left_layout.setContentsMargins(10, 10, 10, 20)
        self.left_layout.setSpacing(10)

        # Search widget
        self.search_widget = QWidget()
        self.search_layout = QHBoxLayout(self.search_widget)
        self.search_layout.setContentsMargins(0, 0, 0, 0)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search actions...")
        self.search_input.textChanged.connect(self.filter_actions)
                
        self.search_layout.addWidget(self.search_input)
        self.left_layout.addWidget(self.search_widget)

        # Action list
        self.action_list = ActionListWidget(self)
        self.action_list.itemClicked.connect(self.on_action_selected)
        self.action_list.rename_requested.connect(self.start_renaming)
        self.action_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.action_list.customContextMenuRequested.connect(self.show_context_menu)
        
        # Set up the delegate
        self.action_item_delegate = ActionItemDelegate(self.action_list)
        self.action_list.setItemDelegate(self.action_item_delegate)
        
        self.left_layout.addWidget(self.action_list)

        # Add new action button
        self.add_button = QPushButton("+ New Action")
        self.add_button.clicked.connect(self.add_new_action)
        self.left_layout.addSpacing(10)
        self.left_layout.addWidget(self.add_button)

        self.main_layout.addWidget(self.left_widget)

        # Apply consistent styling
        self.left_widget.setStyleSheet(ActionWindowStyles.LEFT_PANEL)
        self.search_input.setStyleSheet(ActionWindowStyles.SEARCH_INPUT)
        self.action_list.setStyleSheet(ActionWindowStyles.ACTION_LIST)
        self.add_button.setStyleSheet(ActionWindowStyles.PRIMARY_BUTTON)

    def _setup_right_panel(self):
        self.right_widget = QWidget()
        self.right_layout = QVBoxLayout(self.right_widget)
        self.right_layout.setContentsMargins(20, 20, 20, 20)
        self.right_layout.setSpacing(20)

        # Create metadata section with more spacing
        metadata_widget = QWidget()
        metadata_layout = QVBoxLayout(metadata_widget)
        metadata_layout.setSpacing(15)
        metadata_layout.setContentsMargins(0, 0, 0, 0)

        # Title section
        title_widget = QWidget()
        title_layout = QVBoxLayout(title_widget)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(8)
        self.title_label = QLabel("Title:")
        self.title_label.setStyleSheet(ActionWindowStyles.LABEL)
        self.title_input = QLineEdit()
        title_layout.addWidget(self.title_label)
        title_layout.addWidget(self.title_input)

        # Description section
        desc_widget = QWidget()
        desc_layout = QVBoxLayout(desc_widget)
        desc_layout.setContentsMargins(0, 0, 0, 0)
        desc_layout.setSpacing(8)
        self.description_label = QLabel("Description:")
        self.description_label.setStyleSheet(ActionWindowStyles.LABEL)
        self.description_input = QLineEdit()
        desc_layout.addWidget(self.description_label)
        desc_layout.addWidget(self.description_input)

        # Triggers section
        triggers_widget = QWidget()
        triggers_layout = QVBoxLayout(triggers_widget)
        triggers_layout.setContentsMargins(0, 0, 0, 0)
        triggers_layout.setSpacing(8)
        
        # Triggers header section
        triggers_header = QWidget()
        triggers_header_layout = QHBoxLayout(triggers_header)
        triggers_header_layout.setContentsMargins(0, 0, 0, 0)
        self.triggers_label = QLabel("Trigger Words:")
        self.triggers_label.setStyleSheet(ActionWindowStyles.LABEL)
        self.add_trigger_button = QPushButton("+")
        self.add_trigger_button.setFixedSize(24, 24)
        self.add_trigger_button.clicked.connect(self.add_trigger_word)
        
        triggers_header_layout.addWidget(self.triggers_label)
        triggers_header_layout.addStretch()
        triggers_header_layout.addWidget(self.add_trigger_button)
        
        self.triggers_list = TagListWidget()
        self.triggers_list.setMinimumHeight(40)
        
        triggers_layout.addWidget(triggers_header)
        triggers_layout.addWidget(self.triggers_list)

        # Play Sticky Checkbox
        self.play_sticky_checkbox = QCheckBox("Play Sticky")
        self.play_sticky_checkbox.setStyleSheet(ActionWindowStyles.CHECKBOX)

        # Add sections to metadata layout
        metadata_layout.addWidget(title_widget)
        metadata_layout.addWidget(desc_widget)
        metadata_layout.addWidget(triggers_widget)
        metadata_layout.addWidget(self.play_sticky_checkbox)

        # Bottom buttons container
        bottom_buttons = QWidget()
        bottom_layout = QHBoxLayout(bottom_buttons)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(10)

        # Create buttons
        self.play_action_button = QPushButton("Play Action")
        self.update_button = QPushButton("Update Action")
        self.start_recording_button = QPushButton("Start Recording")
        self.stop_recording_button = QPushButton("Stop Recording")
        self.stop_recording_button.setEnabled(False)

        # Add buttons to layout
        bottom_layout.addWidget(self.play_action_button)
        bottom_layout.addWidget(self.start_recording_button)
        bottom_layout.addWidget(self.stop_recording_button)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.update_button)

        # Add everything to main layout
        self.right_layout.addWidget(metadata_widget)
        self.right_layout.addStretch()
        self.right_layout.addWidget(bottom_buttons)

        # Add right widget to main layout
        self.main_layout.addWidget(self.right_widget)

        # Apply styles
        self.title_input.setStyleSheet(ActionWindowStyles.SEARCH_INPUT)
        self.description_input.setStyleSheet(ActionWindowStyles.SEARCH_INPUT)
        self.add_trigger_button.setStyleSheet(ActionWindowStyles.ICON_BUTTON)
        self.play_action_button.setStyleSheet(ActionWindowStyles.PRIMARY_BUTTON_SUCCESS)  # Green style
        self.start_recording_button.setStyleSheet(ActionWindowStyles.RECORD_BUTTON)
        self.stop_recording_button.setStyleSheet(ActionWindowStyles.STOP_BUTTON)
        self.update_button.setStyleSheet(ActionWindowStyles.PRIMARY_BUTTON)

        # Connect button signals
        self.update_button.clicked.connect(self.update_action)
        self.play_action_button.clicked.connect(self.play_action)
        self.start_recording_button.clicked.connect(self.start_recording)
        self.stop_recording_button.clicked.connect(self.stop_recording)

        # Initially disable the right panel
        self.enable_right_panel(False)

    def enable_right_panel(self, enabled=True):
        """Enable or disable the right panel widgets"""
        for widget in [self.title_input, self.description_input, 
                      self.triggers_list, self.add_trigger_button,
                      self.play_sticky_checkbox, self.update_button,
                      self.start_recording_button, self.stop_recording_button,
                      self.play_action_button]:
            widget.setEnabled(enabled)

    def filter_actions(self):
        """Filter actions based on search text"""
        search_text = self.search_input.text().lower()
        self.load_actions(search_text)

    def load_actions(self, search_text=""):
        """Load actions into the list widget"""
        try:
            self.action_list.clear()
            session = get_session()
            try:
                query = session.query(Action)
                if search_text:
                    query = query.filter(
                        Action.title.ilike(f"%{search_text}%") |
                        Action.description.ilike(f"%{search_text}%")
                    )
                actions = query.order_by(Action.modified_date.desc()).all()
                
                for action in actions:
                    item = QListWidgetItem(action.title)
                    item.setData(Qt.ItemDataRole.UserRole, action.id)
                    self.action_list.addItem(item)

                # Select first item if it exists and no search is active
                if not search_text and self.action_list.count() > 0:
                    first_item = self.action_list.item(0)
                    if first_item:
                        self.action_list.setCurrentItem(first_item)
                        # Use QTimer to ensure UI is ready
                        QTimer.singleShot(0, lambda: self.on_action_selected(first_item))
            finally:
                session.close()
        except Exception as e:
            print(f"Error loading actions: {e}")

    def add_new_action(self):
        """Add a new action"""
        title, ok = QInputDialog.getText(
            self, 
            "New Action", 
            "Enter action title:",
            QLineEdit.EchoMode.Normal
        )
        if ok and title.strip():
            session = get_session()
            try:
                new_action = Action(
                    title=title.strip(),
                    description="",
                    additional_trigger_words="[]",
                    play_sticky=False,
                    action="{}",  # Default empty JSON object
                    created_date=datetime.utcnow(),
                    modified_date=datetime.utcnow()
                )
                session.add(new_action)
                session.commit()
                self.load_actions()
                # Select the new action
                self.select_action_by_id(new_action.id)
            finally:
                session.close()

    def select_action_by_id(self, action_id):
        """Select an action in the list by its ID"""
        for i in range(self.action_list.count()):
            item = self.action_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == action_id:
                self.action_list.setCurrentItem(item)
                self.on_action_selected(item)
                break

    def on_action_selected(self, item):
        """Handle action selection"""
        try:
            if not item:
                return
            
            action_id = item.data(Qt.ItemDataRole.UserRole)
            self.current_action_id = action_id
            self.load_action_details(action_id)
            self.enable_right_panel(True)
        except Exception as e:
            print(f"Error in action selection: {e}")

    def load_action_details(self, action_id):
        """Load the details of the selected action"""
        session = get_session()
        try:
            action = session.query(Action).get(action_id)
            if action:
                self.title_input.setText(action.title)
                self.description_input.setText(action.description)
                self.play_sticky_checkbox.setChecked(action.play_sticky)
                
                # Load trigger words
                self.triggers_list.clear()
                trigger_words = json.loads(action.additional_trigger_words)
                for word in trigger_words:
                    self.triggers_list.add_tag(word)
        finally:
            session.close()

    def add_trigger_word(self):
        """Add a new trigger word"""
        word, ok = QInputDialog.getText(
            self,
            "Add Trigger Word",
            "Enter new trigger word:",
            QLineEdit.EchoMode.Normal
        )
        if ok and word.strip():
            self.triggers_list.add_tag(word.strip())

    def update_action(self):
        """Update the current action"""
        if not self.current_action_id:
            return

        # Validate required fields
        new_title = self.title_input.text().strip()

        if not new_title:
            QMessageBox.warning(self, "Validation Error", "Title is required!")
            self.title_input.setFocus()
            return

        session = get_session()
        try:
            action = session.query(Action).get(self.current_action_id)
            if action:
                action.title = new_title
                action.description = self.description_input.text().strip()
                action.additional_trigger_words = json.dumps(self.triggers_list.get_tags())
                action.play_sticky = self.play_sticky_checkbox.isChecked()
                action.modified_date = datetime.utcnow()
                session.commit()
                
                # Update the list item's text
                current_item = self.action_list.currentItem()
                if current_item:
                    current_item.setText(new_title)
                
                # Show success message with a timer
                self.update_button.setEnabled(False)
                self.update_button.setText("Updated ✓")
                self.update_button.setStyleSheet(ActionWindowStyles.PRIMARY_BUTTON_SUCCESS)
                
                # Reset button after 2 seconds
                QTimer.singleShot(2000, self.reset_update_button)
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to update action: {str(e)}")
        finally:
            session.close()

    def reset_update_button(self):
        """Reset the update button to its original state"""
        self.update_button.setEnabled(True)
        self.update_button.setText("Update Action")
        self.update_button.setStyleSheet(ActionWindowStyles.PRIMARY_BUTTON)

    def closeEvent(self, event):
        """Handle window close event"""
        event.ignore()
        self.hide()

    def showEvent(self, event):
        """Called when window is shown"""
        super().showEvent(event)
        # Refresh actions when window is shown
        self.load_actions()

    def show_context_menu(self, position):
        """Show context menu for action items"""
        item = self.action_list.itemAt(position)
        if item is not None:
            action_id = item.data(Qt.ItemDataRole.UserRole)
            if action_id is not None:
                menu = QMenu(self)
                rename_action = menu.addAction("Rename Action")
                remove_action = menu.addAction("Delete Action")

                action = menu.exec(self.action_list.mapToGlobal(position))
                if action == rename_action:
                    self.start_renaming(item)
                elif action == remove_action:
                    self.remove_action(action_id)

    def start_renaming(self, item=None):
        """Start the renaming process for an action"""
        if not self.is_renaming:
            try:
                current_item = item or self.action_list.currentItem()
                if current_item and current_item.flags() & Qt.ItemFlag.ItemIsSelectable:
                    # Clean up any existing rename editor first
                    self.cleanup_rename_editor()
                    
                    self.is_renaming = True
                    self.renaming_item = current_item
                    
                    if self.action_item_delegate:
                        self.action_item_delegate.renaming_item = self.action_list.row(current_item)
                    
                    self.rename_editor = SafeLineEdit(self.action_list)
                    self.rename_editor.setText(current_item.text())
                    self.rename_editor.selectAll()
                    
                    rect = self.action_list.visualItemRect(current_item)
                    self.rename_editor.setGeometry(rect)
                    self.rename_editor.setStyleSheet(ActionWindowStyles.RENAME_EDITOR)
                    
                    self.rename_editor.show()
                    self.rename_editor.setFocus()
                    
                    self.action_list.update()
            except Exception as e:
                print(f"Error in start_renaming: {e}")
                self.cancel_renaming()

    def finish_renaming(self):
        """Complete the renaming process"""
        if not self.is_renaming or not self.renaming_item:
            return
        
        try:
            if self.rename_editor:
                new_title = self.rename_editor.text().strip()
                if new_title:
                    action_id = self.renaming_item.data(Qt.ItemDataRole.UserRole)
                    self.rename_action(action_id, new_title)
        except Exception as e:
            print(f"Error in finish_renaming: {e}")
        finally:
            self.cleanup_rename_editor()

    def cancel_renaming(self):
        """Cancel the renaming process"""
        try:
            if self.is_renaming:
                if self.rename_editor:
                    self.rename_editor.blockSignals(True)
                    self.rename_editor.hide()
                    self.rename_editor.setParent(None)
                    self.rename_editor.deleteLater()
                    self.rename_editor = None

                self.is_renaming = False
                if self.action_item_delegate:
                    self.action_item_delegate.renaming_item = None
                self.renaming_item = None
                
                self.action_list.viewport().update()
                QApplication.processEvents()
        except Exception as e:
            print(f"Error in cancel_renaming: {e}")
            self.cleanup_rename_editor()

    def cleanup_rename_editor(self):
        """Safely clean up the rename editor and related states"""
        if hasattr(self, 'rename_editor') and self.rename_editor is not None:
            try:
                editor = self.rename_editor
                self.rename_editor = None
                editor.hide()
                editor.setParent(None)
                editor.deleteLater()
            except Exception as e:
                print(f"Error cleaning up rename editor: {e}")
        
        self.is_renaming = False
        if self.action_item_delegate:
            self.action_item_delegate.renaming_item = None
        self.renaming_item = None
        self.action_list.viewport().update()
        QApplication.processEvents()

    def rename_action(self, action_id, new_title):
        """Rename an action in the database"""
        session = get_session()
        try:
            action = session.query(Action).get(action_id)
            if action:
                action.title = new_title
                action.modified_date = datetime.utcnow()
                session.commit()
                
                # Update the list item's text
                for i in range(self.action_list.count()):
                    item = self.action_list.item(i)
                    if item.data(Qt.ItemDataRole.UserRole) == action_id:
                        item.setText(new_title)
                        break
                
                # Update the title input if this is the current action
                if self.current_action_id == action_id:
                    self.title_input.setText(new_title)
                    
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to rename action: {str(e)}")
        finally:
            session.close()

    def remove_action(self, action_id):
        """Delete an action after confirmation"""
        confirm = QMessageBox.question(
            self,
            "Confirm Deletion",
            "Are you sure you want to delete this action?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if confirm == QMessageBox.StandardButton.Yes:
            session = get_session()
            try:
                action = session.query(Action).get(action_id)
                if action:
                    session.delete(action)
                    session.commit()
                    self.load_actions()
                    if self.current_action_id == action_id:
                        self.current_action_id = None
                        self.enable_right_panel(False)
                        self.clear_right_panel()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete action: {str(e)}")
            finally:
                session.close()

    def clear_right_panel(self):
        """Clear all inputs in the right panel"""
        self.title_input.clear()
        self.description_input.clear()
        self.triggers_list.clear()
        self.play_sticky_checkbox.setChecked(False)

    def handle_enter_key(self):
        """Handle Enter key press"""
        if self.is_renaming:
            self.finish_renaming()
        else:
            current_item = self.action_list.currentItem()
            if current_item and current_item.flags() & Qt.ItemFlag.ItemIsSelectable:
                self.start_renaming(current_item)

    def handle_delete_key(self):
        """Handle Delete key press"""
        current_item = self.action_list.currentItem()
        if current_item and current_item.flags() & Qt.ItemFlag.ItemIsSelectable:
            action_id = current_item.data(Qt.ItemDataRole.UserRole)
            self.remove_action(action_id)

    def rename_editor_key_press(self, event):
        """Handle key press events in the rename editor"""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish_renaming()
        elif event.key() == Qt.Key.Key_Escape:
            self.cancel_renaming()
        else:
            QLineEdit.keyPressEvent(self.rename_editor, event)

    def start_recording(self):
        """Handle start recording button click"""
        self.start_recording_button.setEnabled(False)
        self.stop_recording_button.setEnabled(True)
        # Add your recording logic here

    def stop_recording(self):
        """Handle stop recording button click"""
        self.start_recording_button.setEnabled(True)
        self.stop_recording_button.setEnabled(False)
        # Add your stop recording logic here

    def play_action(self):
        """Handle play action button click"""
        # Add your play action logic here
        pass
