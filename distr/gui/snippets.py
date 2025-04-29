from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QIcon, QColor, QPainter, QFontMetrics, QFont
from PyQt6.QtWidgets import (
    QMessageBox, QInputDialog, QApplication, QGridLayout, QSizePolicy,
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QPushButton, QListWidget, QListWidgetItem,
    QMenu, QStyledItemDelegate
)
from PyQt6.Qsci import QsciScintilla, QsciLexerPython
from distr.core.db import get_session, Snippet
from distr.core.constants import ICONS_DIR
import os
import json
from datetime import datetime
from distr.gui.styles.snippetwindowstyles import SnippetWindowStyles

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
        self.label.setStyleSheet(SnippetWindowStyles.TAG_LABEL)

        # Delete button
        self.delete_btn = QPushButton("×")
        self.delete_btn.setFixedSize(16, 16)
        self.delete_btn.setStyleSheet(SnippetWindowStyles.TAG_DELETE_BUTTON)
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
        self.setStyleSheet(SnippetWindowStyles.TAG_LIST)
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

class CodeEditor(QsciScintilla):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_editor()

    def setup_editor(self):
        # Create a proper QFont object
        font = QFont('Menlo', 12)
        self.setFont(font)
        self.setMarginsFont(font)

        # Margin 0 is used for line numbers
        fontmetrics = QFontMetrics(font)
        self.setMarginsFont(font)
        self.setMarginWidth(0, fontmetrics.horizontalAdvance("00000") + 6)
        self.setMarginLineNumbers(0, True)
        
        # Dark theme colors
        self.setMarginsBackgroundColor(QColor("#1e1e2d"))
        self.setMarginsForegroundColor(QColor("#ececf1"))

        # Set paper (background) and text colors
        self.setPaper(QColor("#2d2d3a"))
        self.setColor(QColor("#ececf1"))

        # Brace matching
        self.setBraceMatching(QsciScintilla.BraceMatch.SloppyBraceMatch)

        # Current line visible with special background color
        self.setCaretLineVisible(True)
        self.setCaretLineBackgroundColor(QColor("#363648"))
        self.setCaretForegroundColor(QColor("#ececf1"))

        # Selection colors
        self.setSelectionBackgroundColor(QColor("#3d3d4f"))
        self.setSelectionForegroundColor(QColor("#ececf1"))

        # Edge line
        self.setEdgeMode(QsciScintilla.EdgeMode.EdgeLine)
        self.setEdgeColumn(80)
        self.setEdgeColor(QColor("#565869"))

        # Extra space between lines
        self.setExtraAscent(2)
        self.setExtraDescent(2)

        # Other settings remain the same
        self.setUtf8(True)
        self.setWrapMode(QsciScintilla.WrapMode.WrapWord)
        self.setIndentationsUseTabs(False)
        self.setTabWidth(4)
        self.setIndentationGuides(True)
        self.setTabIndents(True)
        self.setAutoIndent(True)

class SnippetListWidget(QListWidget):
    snippet_selected = pyqtSignal(int)
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
        
        # Only process new selections for snippet items
        if item and item.flags() & Qt.ItemFlag.ItemIsSelectable:
            if item != current:
                super().mousePressEvent(event)
                self.parent_window.on_snippet_selected(item)
        
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

class SnippetItemDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.renaming_item = None

    def paint(self, painter, option, index):
        if index.row() == self.renaming_item:
            painter.fillRect(option.rect, QColor("#2d2d3a"))
        super().paint(painter, option, index)

class SnippetWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.current_snippet_id = None
        self.is_renaming = False
        self.rename_editor = None
        self.snippet_item_delegate = None  # Initialize the delegate variable
        self._setup_window()
        # Load snippets when window is created
        self.load_snippets()

    def _setup_window(self):
        self.setWindowTitle("Snippets Manager")
        # Match ChatWindow dimensions
        self.setGeometry(100, 100, 1000, 600)

        # Center the window on the screen
        screen = QApplication.primaryScreen().geometry()
        window_geometry = self.geometry()
        x = (screen.width() - window_geometry.width()) // 2
        y = (screen.height() - window_geometry.height()) // 2
        self.move(x, y)

        # Left panel setup
        self.left_widget = QWidget()
        self.left_widget.setFixedWidth(260)  # Match ChatWindow width

        # Apply base styles
        self.setStyleSheet(SnippetWindowStyles.MAIN_WINDOW)

        # Main layout
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QHBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Left side: Snippet list
        self._setup_left_panel()
        
        # Right side: Snippet editor
        self._setup_right_panel()

    def _setup_left_panel(self):
        self.left_widget = QWidget()
        self.left_widget.setFixedWidth(200)
        self.left_layout = QVBoxLayout(self.left_widget)
        self.left_layout.setContentsMargins(10, 10, 10, 10)
        self.left_layout.setSpacing(10)

        # Search widget
        self.search_widget = QWidget()
        self.search_layout = QHBoxLayout(self.search_widget)
        self.search_layout.setContentsMargins(0, 0, 0, 0)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search snippets...")
        self.search_input.textChanged.connect(self.filter_snippets)
                
        self.search_layout.addWidget(self.search_input)
        self.left_layout.addWidget(self.search_widget)

        # Snippet list
        self.snippet_list = SnippetListWidget(self)
        self.snippet_list.itemClicked.connect(self.on_snippet_selected)
        self.snippet_list.rename_requested.connect(self.start_renaming)
        self.snippet_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.snippet_list.customContextMenuRequested.connect(self.show_context_menu)
        
        # Set up the delegate
        self.snippet_item_delegate = SnippetItemDelegate(self.snippet_list)
        self.snippet_list.setItemDelegate(self.snippet_item_delegate)
        
        self.left_layout.addWidget(self.snippet_list)

        # Add new snippet button
        self.add_button = QPushButton("+ New Snippet")
        self.add_button.setStyleSheet(SnippetWindowStyles.PRIMARY_BUTTON)
        self.add_button.clicked.connect(self.add_new_snippet)
        self.left_layout.addWidget(self.add_button)

        self.main_layout.addWidget(self.left_widget)

        # Apply consistent styling
        self.left_widget.setStyleSheet(SnippetWindowStyles.LEFT_PANEL)
        self.search_input.setStyleSheet(SnippetWindowStyles.SEARCH_INPUT)
        self.snippet_list.setStyleSheet(SnippetWindowStyles.SNIPPET_LIST)
        self.add_button.setStyleSheet(SnippetWindowStyles.PRIMARY_BUTTON)

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
        self.title_label.setStyleSheet(SnippetWindowStyles.LABEL)
        self.title_input = QLineEdit()
        title_layout.addWidget(self.title_label)
        title_layout.addWidget(self.title_input)

        # Description section
        desc_widget = QWidget()
        desc_layout = QVBoxLayout(desc_widget)
        desc_layout.setContentsMargins(0, 0, 0, 0)
        desc_layout.setSpacing(8)
        self.description_label = QLabel("Description:")
        self.description_label.setStyleSheet(SnippetWindowStyles.LABEL)
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
        self.triggers_label.setStyleSheet(SnippetWindowStyles.LABEL)
        self.add_trigger_button = QPushButton("+")
        self.add_trigger_button.setFixedSize(24, 24)
        self.add_trigger_button.setStyleSheet(SnippetWindowStyles.ICON_BUTTON)
        self.add_trigger_button.clicked.connect(self.add_trigger_word)
        
        triggers_header_layout.addWidget(self.triggers_label)
        triggers_header_layout.addStretch()
        triggers_header_layout.addWidget(self.add_trigger_button)
        triggers_header.setLayout(triggers_header_layout)
        
        self.triggers_list = TagListWidget()
        self.triggers_list.setMinimumHeight(40)
        
        triggers_layout.addWidget(triggers_header)
        triggers_layout.addWidget(self.triggers_list)

        # Add sections to metadata layout
        metadata_layout.addWidget(title_widget)
        metadata_layout.addWidget(desc_widget)
        metadata_layout.addWidget(triggers_widget)

        # Code editor section
        self.code_label = QLabel("Snippet:")
        self.code_label.setStyleSheet(SnippetWindowStyles.LABEL)
        self.code_editor = CodeEditor()

        # Update button
        self.update_button = QPushButton("Update Snippet")
        self.update_button.clicked.connect(self.update_snippet)

        # Add everything to main layout
        self.right_layout.addWidget(metadata_widget)
        self.right_layout.addWidget(self.code_label)
        self.right_layout.addWidget(self.code_editor, 1)
        self.right_layout.addWidget(self.update_button, 0, Qt.AlignmentFlag.AlignRight)

        # Add right widget to main layout
        self.main_layout.addWidget(self.right_widget)

        # Apply styles
        self.title_input.setStyleSheet(SnippetWindowStyles.SEARCH_INPUT)
        self.description_input.setStyleSheet(SnippetWindowStyles.SEARCH_INPUT)
        self.add_trigger_button.setStyleSheet(SnippetWindowStyles.ICON_BUTTON)
        self.update_button.setStyleSheet(SnippetWindowStyles.PRIMARY_BUTTON)

        # Initially disable the right panel
        self.enable_right_panel(False)

    def enable_right_panel(self, enabled=True):
        """Enable or disable the right panel widgets"""
        for widget in [self.title_input, self.description_input, 
                      self.triggers_list, self.add_trigger_button,
                      self.code_editor, self.update_button]:
            widget.setEnabled(enabled)

    def filter_snippets(self):
        """Filter snippets based on search text"""
        search_text = self.search_input.text().lower()
        self.load_snippets(search_text)

    def load_snippets(self, search_text=""):
        """Load snippets into the list widget"""
        self.snippet_list.clear()
        session = get_session()
        try:
            query = session.query(Snippet)
            if search_text:
                query = query.filter(
                    Snippet.title.ilike(f"%{search_text}%") |
                    Snippet.description.ilike(f"%{search_text}%") |
                    Snippet.snippet.ilike(f"%{search_text}%")
                )
            snippets = query.order_by(Snippet.modified_date.desc()).all()
            
            for snippet in snippets:
                item = QListWidgetItem(snippet.title)
                item.setData(Qt.ItemDataRole.UserRole, snippet.id)
                self.snippet_list.addItem(item)

            # Select first item if it exists and no search is active
            if not search_text and self.snippet_list.count() > 0:
                first_item = self.snippet_list.item(0)
                self.snippet_list.setCurrentItem(first_item)
                self.on_snippet_selected(first_item)
        finally:
            session.close()

    def add_new_snippet(self):
        """Add a new snippet"""
        title, ok = QInputDialog.getText(
            self, 
            "New Snippet", 
            "Enter snippet title:",
            QLineEdit.EchoMode.Normal
        )
        if ok and title.strip():
            session = get_session()
            try:
                new_snippet = Snippet(
                    title=title.strip(),
                    description="",
                    additional_trigger_words="[]",
                    snippet="Please add your snippet of text in here",  # Default text
                    created_date=datetime.utcnow(),
                    modified_date=datetime.utcnow()
                )
                session.add(new_snippet)
                session.commit()
                self.load_snippets()
                # Select the new snippet
                self.select_snippet_by_id(new_snippet.id)
            finally:
                session.close()

    def select_snippet_by_id(self, snippet_id):
        """Select a snippet in the list by its ID"""
        for i in range(self.snippet_list.count()):
            item = self.snippet_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == snippet_id:
                self.snippet_list.setCurrentItem(item)
                self.on_snippet_selected(item)
                break

    def on_snippet_selected(self, item):
        """Handle snippet selection"""
        snippet_id = item.data(Qt.ItemDataRole.UserRole)
        self.current_snippet_id = snippet_id
        self.load_snippet_details(snippet_id)
        self.enable_right_panel(True)

    def load_snippet_details(self, snippet_id):
        """Load the details of the selected snippet"""
        session = get_session()
        try:
            snippet = session.query(Snippet).get(snippet_id)
            if snippet:
                self.title_input.setText(snippet.title)
                self.description_input.setText(snippet.description)
                self.code_editor.setText(snippet.snippet)
                
                # Load trigger words
                self.triggers_list.clear()
                trigger_words = json.loads(snippet.additional_trigger_words)
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

    def update_snippet(self):
        """Update the current snippet"""
        if not self.current_snippet_id:
            return

        # Validate required fields
        new_title = self.title_input.text().strip()
        snippet_text = self.code_editor.text().strip()

        if not new_title:
            QMessageBox.warning(self, "Validation Error", "Title is required!")
            self.title_input.setFocus()
            return

        if not snippet_text:
            QMessageBox.warning(self, "Validation Error", "Snippet text is required!")
            self.code_editor.setFocus()
            return

        session = get_session()
        try:
            snippet = session.query(Snippet).get(self.current_snippet_id)
            if snippet:
                snippet.title = new_title
                snippet.description = self.description_input.text().strip()
                snippet.snippet = snippet_text
                snippet.additional_trigger_words = json.dumps(self.triggers_list.get_tags())
                snippet.modified_date = datetime.utcnow()
                session.commit()
                
                # Update the list item's text
                current_item = self.snippet_list.currentItem()
                if current_item:
                    current_item.setText(new_title)
                
                # Show success message with a timer
                self.update_button.setEnabled(False)
                self.update_button.setText("Updated ✓")
                self.update_button.setStyleSheet(SnippetWindowStyles.PRIMARY_BUTTON_SUCCESS)
                
                # Reset button after 2 seconds
                QTimer.singleShot(2000, self.reset_update_button)
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to update snippet: {str(e)}")
        finally:
            session.close()

    def reset_update_button(self):
        """Reset the update button to its original state"""
        self.update_button.setEnabled(True)
        self.update_button.setText("Update Snippet")
        self.update_button.setStyleSheet(SnippetWindowStyles.PRIMARY_BUTTON)

    def closeEvent(self, event):
        """Handle window close event"""
        event.ignore()
        self.hide()

    def showEvent(self, event):
        """Called when window is shown"""
        super().showEvent(event)
        # Refresh snippets when window is shown
        self.load_snippets()

    def show_context_menu(self, position):
        """Show context menu for snippet items"""
        item = self.snippet_list.itemAt(position)
        if item is not None:
            snippet_id = item.data(Qt.ItemDataRole.UserRole)
            if snippet_id is not None:
                menu = QMenu(self)
                rename_action = menu.addAction("Rename Snippet")
                remove_action = menu.addAction("Delete Snippet")

                action = menu.exec(self.snippet_list.mapToGlobal(position))
                if action == rename_action:
                    self.start_renaming(item)
                elif action == remove_action:
                    self.remove_snippet(snippet_id)

    def start_renaming(self, item=None):
        """Start the renaming process for a snippet"""
        if not self.is_renaming:
            try:
                current_item = item or self.snippet_list.currentItem()
                if current_item and current_item.flags() & Qt.ItemFlag.ItemIsSelectable:
                    # Clean up any existing rename editor first
                    self.cleanup_rename_editor()
                    
                    self.is_renaming = True
                    self.renaming_item = current_item
                    
                    if self.snippet_item_delegate:
                        self.snippet_item_delegate.renaming_item = self.snippet_list.row(current_item)
                    
                    self.rename_editor = SafeLineEdit(self.snippet_list)
                    self.rename_editor.setText(current_item.text())
                    self.rename_editor.selectAll()
                    
                    rect = self.snippet_list.visualItemRect(current_item)
                    self.rename_editor.setGeometry(rect)
                    self.rename_editor.setStyleSheet(SnippetWindowStyles.RENAME_EDITOR)
                    
                    # Connect the key press event handler
                    self.rename_editor.keyPressEvent = lambda event: self.rename_editor_key_press(event)
                    
                    self.rename_editor.show()
                    self.rename_editor.setFocus()
                    
                    self.snippet_list.update()
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
                    snippet_id = self.renaming_item.data(Qt.ItemDataRole.UserRole)
                    self.rename_snippet(snippet_id, new_title)
        except Exception as e:
            print(f"Error in finish_renaming: {e}")
        finally:
            self.cleanup_rename_editor()

    def cancel_renaming(self):
        """Aggressively cancel renaming"""
        try:
            if self.is_renaming:
                if self.rename_editor:
                    self.rename_editor.blockSignals(True)
                    self.rename_editor.hide()
                    self.rename_editor.setParent(None)
                    self.rename_editor.deleteLater()
                    self.rename_editor = None

                self.is_renaming = False
                if self.snippet_item_delegate:
                    self.snippet_item_delegate.renaming_item = None
                self.renaming_item = None
                
                self.snippet_list.viewport().update()
                QApplication.processEvents()
        except Exception as e:
            print(f"Error in cancel_renaming: {e}")
            # Ensure cleanup even if there's an error
            self.is_renaming = False
            self.rename_editor = None
            self.renaming_item = None
            if self.snippet_item_delegate:
                self.snippet_item_delegate.renaming_item = None

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
        if self.snippet_item_delegate:
            self.snippet_item_delegate.renaming_item = None
        self.renaming_item = None
        self.snippet_list.viewport().update()
        QApplication.processEvents()

    def rename_snippet(self, snippet_id, new_title):
        """Rename a snippet in the database"""
        session = get_session()
        try:
            snippet = session.query(Snippet).get(snippet_id)
            if snippet:
                snippet.title = new_title
                snippet.modified_date = datetime.utcnow()
                session.commit()
                
                # Update the list item's text
                for i in range(self.snippet_list.count()):
                    item = self.snippet_list.item(i)
                    if item.data(Qt.ItemDataRole.UserRole) == snippet_id:
                        item.setText(new_title)
                        break
                
                # Update the title input if this is the current snippet
                if self.current_snippet_id == snippet_id:
                    self.title_input.setText(new_title)
                    
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to rename snippet: {str(e)}")
        finally:
            session.close()

    def remove_snippet(self, snippet_id):
        """Delete a snippet after confirmation"""
        confirm = QMessageBox.question(
            self,
            "Confirm Deletion",
            "Are you sure you want to delete this snippet?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if confirm == QMessageBox.StandardButton.Yes:
            session = get_session()
            try:
                snippet = session.query(Snippet).get(snippet_id)
                if snippet:
                    session.delete(snippet)
                    session.commit()
                    self.load_snippets()
                    if self.current_snippet_id == snippet_id:
                        self.current_snippet_id = None
                        self.enable_right_panel(False)
                        self.clear_right_panel()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete snippet: {str(e)}")
            finally:
                session.close()

    def clear_right_panel(self):
        """Clear all inputs in the right panel"""
        self.title_input.clear()
        self.description_input.clear()
        self.triggers_list.clear()
        self.code_editor.clear()

    def handle_enter_key(self):
        """Handle Enter key press"""
        if self.is_renaming:
            self.finish_renaming()
        else:
            current_item = self.snippet_list.currentItem()
            if current_item and current_item.flags() & Qt.ItemFlag.ItemIsSelectable:
                self.start_renaming(current_item)

    def handle_delete_key(self):
        """Handle Delete key press"""
        current_item = self.snippet_list.currentItem()
        if current_item and current_item.flags() & Qt.ItemFlag.ItemIsSelectable:
            snippet_id = current_item.data(Qt.ItemDataRole.UserRole)
            self.remove_snippet(snippet_id)

    def rename_editor_key_press(self, event):
        """Handle key press events in the rename editor"""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish_renaming()
        elif event.key() == Qt.Key.Key_Escape:
            self.cancel_renaming()
        else:
            # Call the original keyPressEvent of QLineEdit
            QLineEdit.keyPressEvent(self.rename_editor, event)
