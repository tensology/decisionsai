from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QIcon, QAction, QMovie, QColor, QPainter, QFontMetrics, QBrush, QPixmap
from PyQt6.QtWidgets import QPushButton, QListWidgetItem, QMenu, QMessageBox, QInputDialog, QLineEdit, QListWidget, QStyledItemDelegate, QApplication
from PyQt6 import QtWidgets, QtCore
from distr.core.db import get_session, Chat
from distr.core.constants import ICONS_DIR
from distr.core.signals import signal_manager
import os
from datetime import datetime, timedelta
import json
from sqlalchemy import or_
from sqlalchemy.orm.exc import NoResultFound
from distr.gui.styles.chatwindowstyles import ChatWindowStyles
import hashlib

class RoundButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QPushButton {
                background-color: white;
                color: #2d2d3a;
                border-radius: 20px;
                font-size: 20px;
                font-weight: bold;
                border: none;
            }
            QPushButton:hover {
                background-color: #f0f0f0;
            }
            QPushButton:pressed {
                background-color: #e8e8e8;
            }
        """)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw white circle background
        painter.setBrush(QColor("white"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(self.rect())

        # Draw plus symbol in dark background color (#2d2d3a)
        painter.setPen(QColor("#2d2d3a"))
        painter.setFont(self.font())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "+")

    def enterEvent(self, event):
        # Optional: Add any additional hover effects here
        super().enterEvent(event)

    def leaveEvent(self, event):
        # Optional: Reset any additional hover effects here
        super().leaveEvent(event)

class ChatListWidget(QListWidget):
    chat_selected = pyqtSignal(int)
    rename_requested = pyqtSignal(QListWidgetItem)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.active_item = None
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Prevent focus changes

    def set_active_item(self, item):
        """Set the active item and force a repaint"""
        self.active_item = item
        self.viewport().update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.parent_window.handle_enter_key()
        elif event.key() == Qt.Key.Key_Delete:
            self.parent_window.handle_delete_key()
        elif event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            current_row = self.currentRow()
            super().keyPressEvent(event)
            new_item = self.currentItem()
            if new_item and new_item.flags() & Qt.ItemFlag.ItemIsSelectable and self.currentRow() != current_row:
                self.parent_window.on_chat_item_clicked(new_item)
        else:
            super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event):
        item = self.itemAt(event.pos())
        if item and item.flags() & Qt.ItemFlag.ItemIsSelectable:
            self.rename_requested.emit(item)
        else:
            super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if self.parent_window.is_renaming:
            try:
                # Get the text before any cleanup
                text = None
                if self.parent_window.rename_editor:
                    text = self.parent_window.rename_editor.text().strip()
                
                # Cancel or finish based on text content
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
        
        # Only process new selections for chat items
        if item and item.flags() & Qt.ItemFlag.ItemIsSelectable:
            if item != current:
                super().mousePressEvent(event)
                self.parent_window.on_chat_item_clicked(item)
        
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

    # Add method to maintain current selection
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

class ChatItemDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.renaming_item = None

    def paint(self, painter, option, index):
        if index.row() == self.renaming_item:
            painter.fillRect(option.rect, QColor("#2d2d3a"))
        super().paint(painter, option, index)

class SafeLineEdit(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)
        self.installEventFilter(self)
        # Store initial text in case we need to validate
        self.initial_text = ""
    
    def setText(self, text):
        super().setText(text)
        self.initial_text = text
    
    def focusOutEvent(self, event):
        """When focus is lost, commit the current text if valid"""
        if self.parent() and hasattr(self.parent(), 'parent_window'):
            text = self.text().strip()
            if text and text != self.initial_text:  # Only proceed if text changed and not empty
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

class ChatWindow(QtWidgets.QMainWindow):
    def __init__(self, chat_manager):
        super().__init__()
        self.chat_manager = chat_manager
        # Disable input methods globally for this window
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)
        
        # Initialize typing indicator before setup_window
        self.typing_indicator = QtWidgets.QLabel("AI is typing...")
        self.typing_indicator.setStyleSheet("""
            QLabel {
                color: #8e8ea0;
                font-style: italic;
                padding: 8px 20px;
                margin-bottom: 4px;
            }
        """)
        self.typing_indicator.hide()
        
        # Connect new chat signal
        signal_manager.trigger_new_chat.connect(self.add_new_chat)
        
        # Connect streaming signals
        signal_manager.chat_stream_started.connect(self.on_stream_started)
        signal_manager.chat_stream_token.connect(self.on_stream_token)
        signal_manager.chat_stream_finished.connect(self.on_stream_finished)
        signal_manager.chat_stream_error.connect(self.on_stream_error)
        signal_manager.typing_indicator_changed.connect(self.on_typing_indicator_changed)
        
        self.current_streaming_chat_id = None
        self.streaming_response = ""
        
        # Now call setup after all initializations
        self._setup_window()
        self._apply_styles()

    def _apply_styles(self):
        # Apply main window style
        self.setStyleSheet(ChatWindowStyles.MAIN_WINDOW)
        
        # Apply component styles
        self.left_widget.setStyleSheet(ChatWindowStyles.LEFT_WIDGET)
        self.search_widget.setStyleSheet(ChatWindowStyles.SEARCH_WIDGET)
        self.search_input.setStyleSheet(ChatWindowStyles.SEARCH_INPUT)
        self.search_icon_label.setStyleSheet(ChatWindowStyles.SEARCH_ICON)
        self.chat_list.setStyleSheet(ChatWindowStyles.CHAT_LIST)
        self.new_chat_button.setStyleSheet(ChatWindowStyles.NEW_CHAT_BUTTON)
        self.chat_thread_view.setStyleSheet(ChatWindowStyles.CHAT_THREAD_VIEW)
        self.input_area.setStyleSheet(ChatWindowStyles.INPUT_AREA)
        self.send_button.setStyleSheet(ChatWindowStyles.SEND_BUTTON)

    def _setup_window(self):
        self.setWindowTitle("DecisionsAI - Your Chat History")
        self.setGeometry(100, 100, 1000, 600)

        # Center the window on the screen
        screen = QtWidgets.QApplication.primaryScreen().geometry()
        window_geometry = self.geometry()
        x = (screen.width() - window_geometry.width()) // 2
        y = (screen.height() - window_geometry.height()) // 2
        self.move(x, y)

        self.central_widget = QtWidgets.QWidget()
        self.setCentralWidget(self.central_widget)

        # Main layout
        self.main_layout = QtWidgets.QHBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Left side: Chat list
        self.left_widget = QtWidgets.QWidget()
        self.left_widget.setFixedWidth(260)  # Slightly narrower
        self.left_layout = QtWidgets.QVBoxLayout(self.left_widget)
        self.left_layout.setContentsMargins(10, 10, 10, 10)
        self.left_layout.setSpacing(10)

        # Search widget with relative positioning
        self.search_widget = QtWidgets.QWidget()
        self.search_widget.setFixedHeight(44)
        
        # Create search input
        self.search_input = QtWidgets.QLineEdit()
        # Disable input method for search input
        self.search_input.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)
        self.search_input.setPlaceholderText("Search")
        
        # Create search icon with correct size
        self.search_icon_label = QtWidgets.QLabel(self.search_input)  # Make the label a child of search_input
        search_icon_path = os.path.join(ICONS_DIR, "search.png")
        search_icon = QIcon(search_icon_path)
        pixmap = search_icon.pixmap(QSize(14, 14))
        self.search_icon_label.setPixmap(pixmap)
        
        # Position the icon inside the input field
        self.search_icon_label.setStyleSheet(ChatWindowStyles.SEARCH_ICON)
        self.search_icon_label.move(self.search_input.width() - 28, 8)  # Position from right edge
        
        # Create layout
        self.search_layout = QtWidgets.QHBoxLayout(self.search_widget)
        self.search_layout.setContentsMargins(0, 0, 0, 0)
        self.search_layout.setSpacing(0)
        self.search_layout.addWidget(self.search_input)
        
        # Connect the search functionality
        self.search_input.textChanged.connect(self.filter_chats)
        
        # Add resize event to keep icon positioned correctly
        self.search_input.resizeEvent = lambda e: self.search_icon_label.move(
            self.search_input.width() - 28,
            (self.search_input.height() - self.search_icon_label.height()) // 2
        )

        self.left_layout.addWidget(self.search_widget)

        # Container for chat list and spinner
        self.list_container = QtWidgets.QWidget()
        self.list_container_layout = QtWidgets.QStackedLayout(self.list_container)
        self.list_container_layout.setStackingMode(QtWidgets.QStackedLayout.StackingMode.StackAll)

        # Chat list
        self.chat_list = ChatListWidget(self)
        self.chat_list.parent_window = self
        self.chat_item_delegate = ChatItemDelegate(self.chat_list)
        self.chat_list.setItemDelegate(self.chat_item_delegate)
        self.chat_list.setStyleSheet("""
            QListWidget {
                background-color: transparent;
                border: none;
            }
            QListWidget::item {
                height: 40px;
                padding-left: 20px;
                padding-right: 20px;
                border-radius: 5px;
                font-size: 16px;
            }
            QListWidget::item:selected {
                background-color: #e0e0e0;
                color: black;
            }
        """)
        self.chat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_list.customContextMenuRequested.connect(self.show_context_menu)
        self.chat_list.itemClicked.connect(self.on_chat_item_clicked)
        self.chat_list.rename_requested.connect(self.start_renaming)
        self.list_container_layout.addWidget(self.chat_list)

        # Spinner container
        self.spinner_container = QtWidgets.QWidget()
        self.spinner_container_layout = QtWidgets.QVBoxLayout(self.spinner_container)
        self.spinner_container_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Spinner
        self.spinner = QtWidgets.QLabel()
        spinner_path = os.path.join(ICONS_DIR, "spinner.gif")
        self.spinner_movie = QMovie(spinner_path)
        self.spinner_movie.setScaledSize(QSize(60, 60))
        self.spinner.setMovie(self.spinner_movie)
        self.spinner.setFixedSize(60, 60)
        self.spinner_container_layout.addWidget(self.spinner)

        self.list_container_layout.addWidget(self.spinner_container)
        self.spinner_container.hide()  # Initially hide the spinner

        self.left_layout.addWidget(self.list_container)
        self.main_layout.addWidget(self.left_widget)

        # Right side: Chat thread view
        self.right_widget = QtWidgets.QWidget()
        self.right_layout = QtWidgets.QVBoxLayout(self.right_widget)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.setSpacing(0)
        
        # Chat thread view
        self.chat_thread_view = QtWidgets.QTextEdit()
        self.chat_thread_view.setReadOnly(True)
        self.chat_thread_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.chat_thread_view.document().setDocumentMargin(0)
        self.chat_thread_view.setViewportMargins(20, 20, 20, 20)
        self.chat_thread_view.setStyleSheet("""
            QTextEdit {
                background-color: #2d2d3a;
                border: none;
            }
        """)
        self.right_layout.addWidget(self.chat_thread_view, 1)

        # Input container at the bottom
        self.input_container = QtWidgets.QWidget()
        self.input_container_layout = QtWidgets.QHBoxLayout(self.input_container)
        self.input_container_layout.setContentsMargins(20, 0, 20, 20)
        self.input_container_layout.setSpacing(0)
        
        # Create a wrapper widget for input and button
        self.input_wrapper = QtWidgets.QWidget()
        self.input_wrapper_layout = QtWidgets.QHBoxLayout(self.input_wrapper)
        self.input_wrapper_layout.setContentsMargins(0, 0, 0, 0)
        self.input_wrapper_layout.setSpacing(0)
        
        # Input area
        self.input_area = QtWidgets.QTextEdit()
        self.input_area.setPlaceholderText("Send a message...")
        self.input_area.setFixedHeight(52)
        # Connect the keyPress event
        self.input_area.installEventFilter(self)
        
        # Create button container
        self.button_container = QtWidgets.QWidget()
        self.button_layout = QtWidgets.QHBoxLayout(self.button_container)
        self.button_layout.setContentsMargins(0, 0, 0, 0)
        self.button_layout.setSpacing(12)  # Increased from 8 to 12
        
        # Send button
        self.send_button = QtWidgets.QPushButton()
        self.send_button.setFixedSize(40, 40)  # Increased from 32 to 40
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.clicked.connect(self.send_message)
        self.send_button.setStyleSheet("""
            QPushButton {
                padding: 8px;
                border: none;
                border-radius: 8px;
                background-color: transparent;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
            }
        """)
        
        # Voice button
        self.voice_button = QtWidgets.QPushButton()
        self.voice_button.setFixedSize(40, 40)
        self.voice_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.voice_button.clicked.connect(self.handle_voice)
        self.voice_button.setStyleSheet("""
            QPushButton {
                padding: 8px;
                border: none;
                border-radius: 8px;
                background-color: white;
            }
            QPushButton:hover {
                background-color: #f0f0f0;
            }
        """)
        
        # Create SVG for send icon (white fill)
        send_icon_svg = """
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://ww w.w3.org/2000/svg">
            <path d="M22 2L15 22L11 13L2 9L22 2Z" fill="white" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        """
        
        # Create SVG for microphone icon (black fill)
        mic_icon_svg = """
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M12 1C10.3431 1 9 2.34315 9 4V12C9 13.6569 10.3431 15 12 15C13.6569 15 15 13.6569 15 12V4C15 2.34315 13.6569 1 12 1Z" fill="black"/>
            <path d="M7 11V12C7 14.7614 9.23858 17 12 17C14.7614 17 17 14.7614 17 12V11" stroke="black" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M12 17V23" stroke="black" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M8 23H16" stroke="black" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        """
        
        # Convert SVGs to QIcons
        for button, svg in [(self.send_button, send_icon_svg), (self.voice_button, mic_icon_svg)]:
            svg_bytes = svg.encode('utf-8')
            icon = QIcon()
            pixmap = QPixmap()
            pixmap.loadFromData(svg_bytes)
            icon.addPixmap(pixmap)
            button.setIcon(icon)
            button.setIconSize(QSize(24, 24))  # Increased from 20 to 24
        
        # Add buttons to container
        self.button_layout.addWidget(self.send_button)
        self.button_layout.addWidget(self.voice_button)
        
        # Add widgets to wrapper
        self.input_wrapper_layout.addWidget(self.input_area)
        self.input_wrapper_layout.addWidget(self.button_container)
        
        # Add wrapper to container
        self.input_container_layout.addWidget(self.input_wrapper)
        
        self.right_layout.addWidget(self.typing_indicator)
        self.right_layout.addWidget(self.input_container)

        self.main_layout.addWidget(self.right_widget, 2)

        # New Chat button
        self.new_chat_button = RoundButton(self)
        self.new_chat_button.setToolTip("New Chat")
        self.new_chat_button.clicked.connect(self.add_new_chat)
        self.new_chat_button.raise_()

        self.current_chat_id = None
        self.load_chat_list()

        self.position_new_chat_button()

        self.rename_editor = None
        self.is_renaming = False
        self.renaming_item = None

        # Connect signals
        self.chat_manager.chat_created.connect(self.on_chat_created)
        self.chat_manager.chat_updated.connect(self.on_chat_updated)
        self.chat_manager.chat_deleted.connect(self.on_chat_deleted)
        self.chat_manager.current_chat_changed.connect(self.on_current_chat_changed)
        
        # Load initial chat
        self.load_initial_chat()

    def load_initial_chat(self):
        """Load the initial chat, either from last saved or most recent"""
        print("ChatWindow: Loading initial chat...")
        current_chat_id = self.chat_manager.get_current_chat()
        
        if current_chat_id:
            print(f"ChatWindow: Loading last saved chat ID: {current_chat_id}")
            self.select_chat_by_id(current_chat_id)
            self.load_chat_thread(current_chat_id)
        else:
            print("ChatWindow: No last chat ID found, looking for most recent...")
            session = get_session()
            try:
                most_recent_chat = session.query(Chat).order_by(Chat.modified_date.desc()).first()
                if most_recent_chat:
                    print(f"ChatWindow: Loading most recent chat ID: {most_recent_chat.id}")
                    self.select_chat_by_id(most_recent_chat.id)
                    self.load_chat_thread(most_recent_chat.id)
                    # Save this as the current chat
                    self.chat_manager.set_current_chat(most_recent_chat.id)
                else:
                    print("ChatWindow: No chats found in database")
            finally:
                session.close()

    def select_chat_by_id(self, chat_id):
        """Select a chat by its ID without triggering recursive events"""
        if self._selecting_chat or chat_id == self._current_chat_id:
            return
            
        try:
            self._selecting_chat = True
            self._current_chat_id = chat_id
            
            # Find and select the item
            for i in range(self.chat_list.count()):
                item = self.chat_list.item(i)
                if item and item.data(Qt.ItemDataRole.UserRole) == chat_id:
                    self.chat_list.setCurrentItem(item)
                    break
                    
            # Load chat content directly without triggering additional events
            session = get_session()
            try:
                chat = session.query(Chat).get(chat_id)
                if chat:
                    self.load_chat_content(chat)
            finally:
                session.close()
                
        finally:
            self._selecting_chat = False

    def _load_chat_direct(self, chat_id):
        """Load chat without triggering additional events"""
        try:
            session = get_session()
            chat = session.query(Chat).get(chat_id)
            
            if chat:
                self.current_chat = chat
                self.load_messages(chat)
                self.update_chat_title(chat.title)
            
            session.close()
        except Exception as e:
            print(f"Error loading chat directly: {e}")

    def show_spinner(self):
        self.chat_list.hide()
        self.spinner_container.show()
        self.spinner_movie.start()

    def hide_spinner(self):
        self.spinner_movie.stop()
        self.spinner_container.hide()
        self.chat_list.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_new_chat_button()

    def filter_chats(self):
        self.show_spinner()
        search_text = self.search_input.text().lower()
        QTimer.singleShot(100, lambda: self.load_chat_list(search_text))

    def load_chat_list(self, search_text=""):
        """Load chats into the list widget with optional search filtering"""
        current_chat_id = None
        if self.chat_list.currentItem():
            current_chat_id = self.chat_list.currentItem().data(Qt.ItemDataRole.UserRole)
            
        self.chat_list.clear()
        session = get_session()
        try:
            query = session.query(Chat).filter(Chat.parent_id.is_(None))
            
            if search_text:
                # Modified search to include title, input, and response
                query = query.filter(
                    or_(
                        Chat.title.ilike(f"%{search_text}%"),
                        Chat.input.ilike(f"%{search_text}%"),
                        Chat.response.ilike(f"%{search_text}%"),
                        # Also search in child messages (replies)
                        Chat.children.any(
                            or_(
                                Chat.title.ilike(f"%{search_text}%"),
                                Chat.input.ilike(f"%{search_text}%"),
                                Chat.response.ilike(f"%{search_text}%")
                            )
                        )
                    )
                )
            
            chats = query.order_by(Chat.created_date.desc()).all()
            
            today = datetime.now().date()
            yesterday = today - timedelta(days=1)
            
            current_date = None
            for chat in chats:
                chat_date = chat.created_date.date()
                
                if chat_date != current_date:
                    if chat_date == today:
                        header_text = "Today"
                    elif chat_date == yesterday:
                        header_text = "Yesterday"
                    elif today - chat_date <= timedelta(days=6):
                        header_text = f"{(today - chat_date).days} days ago"
                    else:
                        header_text = chat_date.strftime("%B %d, %Y")
                    
                    self.add_date_header(header_text)
                    current_date = chat_date
                
                self.add_chat_item(chat)
            
            # After loading all items, select the first chat item (not header)
            if self.chat_list.count() > 0:
                # Find first selectable item (skip headers)
                for i in range(self.chat_list.count()):
                    item = self.chat_list.item(i)
                    if item and item.flags() & Qt.ItemFlag.ItemIsSelectable:
                        self.chat_list.setCurrentItem(item)
                        # Trigger the chat selection
                        self.on_chat_item_clicked(item)
                        break
                
            # Restore selection or select first item
            if current_chat_id:
                self.select_chat_by_id(current_chat_id)
            else:
                self.chat_list.maintain_selection()
                
        except Exception as e:
            print(f"Error loading chat list: {e}")
        finally:
            session.close()
            self.hide_spinner()

    def add_date_header(self, header_text):
        header_item = QListWidgetItem(header_text)
        # Prevent any interaction with headers
        header_item.setFlags(Qt.ItemFlag.NoItemFlags)
        header_item.setForeground(Qt.GlobalColor.gray)
        font = header_item.font()
        font.setBold(True)
        font.setPointSize(9)
        header_item.setFont(font)
        # Set data to identify as header
        header_item.setData(Qt.ItemDataRole.UserRole + 1, "header")
        self.chat_list.addItem(header_item)

    def add_chat_item(self, chat):
        """Keep showing title in chat list"""
        item = QListWidgetItem(chat.title)  # Show title here
        item.setData(Qt.ItemDataRole.UserRole, chat.id)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsSelectable)
        self.chat_list.addItem(item)

    def show_context_menu(self, position):
        item = self.chat_list.itemAt(position)
        if item and item.data(Qt.ItemDataRole.UserRole) is not None:
            chat_id = item.data(Qt.ItemDataRole.UserRole)
            
            # Create MD5 hash of chat_id and take first 6 characters
            chat_id_str = str(chat_id)
            md5_hash = hashlib.md5(chat_id_str.encode()).hexdigest()
            short_hash = md5_hash[:6]
            
            menu = QMenu(self)
            # Show shortened hash in context menu
            id_action = menu.addAction(f"Chat: #{short_hash}")
            id_action.setEnabled(False)
            
            menu.addSeparator()
            rename_action = menu.addAction("Rename Chat")
            archive_action = menu.addAction("Archive Chat")
            copy_action = menu.addAction("Copy Chat")
            remove_action = menu.addAction("Remove Chat")

            action = menu.exec(self.chat_list.mapToGlobal(position))
            if action == rename_action:
                self.start_renaming(item)
            elif action == archive_action:
                self.archive_chat(chat_id)
            elif action == copy_action:
                self.copy_chat(chat_id)
            elif action == remove_action:
                self.remove_chat(chat_id)

    def archive_chat(self, chat_id):
        # Implement archive functionality
        pass

    def copy_chat(self, chat_id):
        # Implement copy functionality
        pass

    def remove_chat(self, chat_id):
        confirm = QMessageBox.question(
            self, 
            "Confirm Deletion", 
            "Are you sure you want to delete this chat and all its replies?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if confirm == QMessageBox.StandardButton.Yes:
            # Clear the chat history cache before deleting
            self.chat_manager.clear_chat_history(chat_id)
            self.chat_manager.delete_chat(chat_id)
            self.load_chat_list(self.search_input.text())
            if self.current_chat_id == chat_id:
                self.chat_thread_view.clear()
                self.current_chat_id = None

    def on_chat_item_clicked(self, item):
        if item and item.flags() & Qt.ItemFlag.ItemIsSelectable:
            chat_id = item.data(Qt.ItemDataRole.UserRole)
            if chat_id:
                print(f"ChatWindow: Chat clicked, ID: {chat_id}")  # Add debug logging
                self.load_chat_thread(chat_id)
                # Make sure to save the current chat
                self.chat_manager.set_current_chat(chat_id)

    def load_chat_thread(self, chat_id):
        """Load a chat thread and update the current chat"""
        self.current_chat_id = chat_id
        
        # This will now also load/refresh the Ollama context
        self.chat_manager.set_current_chat(chat_id)
        
        session = get_session()
        try:
            chat = session.query(Chat).get(chat_id)
            self.chat_thread_view.clear()
            if chat:
                self.display_chat(chat)
                if chat.children:
                    for child in chat.children:
                        self.display_chat(child, is_child=True)
            else:
                self.chat_thread_view.append("Chat not found.")
        finally:
            session.close()

    def display_chat(self, chat, is_child=False):
        """Display chat with consistent formatting"""
        cursor = self.chat_thread_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)

        # Add spacing before new message
        if not cursor.atStart():
            cursor.insertBlock()
            cursor.insertBlock()

        # User message formatting
        title_format = cursor.blockFormat()
        title_format.setLineHeight(120, 1)
        title_format.setTopMargin(8)
        title_format.setBottomMargin(4)
        cursor.setBlockFormat(title_format)
        
        title_char_format = cursor.charFormat()
        title_char_format.setFontPointSize(16)  # Slightly smaller
        title_char_format.setFontWeight(600)  # Semi-bold
        title_char_format.setForeground(QColor("#ececf1"))
        cursor.setCharFormat(title_char_format)
        cursor.insertText(chat.input)
        
        # Response formatting
        if chat.response and chat.id != self.current_streaming_chat_id:
            cursor.insertBlock()
            cursor.insertBlock()  # Double space after user message
            
            # Split response into paragraphs and handle each one
            paragraphs = [p for p in chat.response.split('\n') if p.strip()]
            
            for i, paragraph in enumerate(paragraphs):
                if i > 0:  # Add spacing between paragraphs
                    cursor.insertBlock()
                    cursor.insertBlock()  # Add extra newline between paragraphs
                
                response_format = cursor.blockFormat()
                response_format.setLineHeight(120, 1)
                response_format.setTopMargin(4)
                response_format.setBottomMargin(4)
                cursor.setBlockFormat(response_format)
                
                response_char_format = cursor.charFormat()
                response_char_format.setFontPointSize(15)
                response_char_format.setFontWeight(400)
                response_char_format.setForeground(QColor("#8e8ea0"))
                cursor.setCharFormat(response_char_format)
                
                cursor.insertText(paragraph.strip())

    def add_to_chat_thread(self):
        if not self.current_chat_id:
            QtWidgets.QMessageBox.warning(self, "Warning", "Please select a chat thread first.")
            return

        input_text = self.input_area.toPlainText().strip()
        if not input_text:
            return

        try:
            cursor = self.chat_thread_view.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            
            # Add newlines if needed
            if not cursor.atStart():
                cursor.insertBlock()
                cursor.insertBlock()
            
            # Show title in bold white
            title_format = cursor.blockFormat()
            title_format.setTopMargin(8)
            title_format.setBottomMargin(4)
            cursor.setBlockFormat(title_format)
            
            title_char_format = cursor.charFormat()
            title_char_format.setFontPointSize(20)
            title_char_format.setFontWeight(700)
            title_char_format.setForeground(QColor("#ececf1"))
            cursor.setCharFormat(title_char_format)
            cursor.insertText(input_text)
            
            # Scroll to bottom after adding user input
            self.scroll_to_bottom()
            
            # Process through Ollama
            new_chat_id, _ = self.chat_manager.process_chat_response(
                self.current_chat_id, 
                input_text
            )
            
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to process chat: {str(e)}")
            self.input_area.setEnabled(True)
            self.send_button.setEnabled(True)

    def scroll_to_bottom(self):
        """Scroll the chat view to the bottom"""
        scrollbar = self.chat_thread_view.verticalScrollBar()
        # Store current value
        current_value = scrollbar.value()
        # Set to maximum
        scrollbar.setValue(scrollbar.maximum())
        # If scroll position changed significantly, animate the scroll
        if scrollbar.maximum() - current_value > 100:
            animation = QtCore.QPropertyAnimation(scrollbar, b"value", self)
            animation.setDuration(300)
            animation.setStartValue(current_value)
            animation.setEndValue(scrollbar.maximum())
            animation.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
            animation.start()

    def add_new_chat(self):
        title, ok = QtWidgets.QInputDialog.getText(
            self,
            "Start a New Conversation", 
            "What would you like to discuss with the DecisionsAI LLM Agent?\n\nThis will be the start of your conversation:",
            QtWidgets.QLineEdit.EchoMode.Normal,
            "My conversation with DecisionsAI"  # Default text
        )
        
        if ok and title.strip():
            try:
                new_chat_id = self.chat_manager.create_chat(title.strip())
                self.load_chat_list(self.search_input.text())
                self.select_chat_by_id(new_chat_id)
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Error", f"Failed to create chat: {str(e)}")

    def select_chat_by_id(self, chat_id):
        for i in range(self.chat_list.count()):
            item = self.chat_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == chat_id:
                self.chat_list.setCurrentItem(item)
                self.on_chat_item_clicked(item)
                break


    def handle_enter_key(self):
        if self.is_renaming:
            self.finish_renaming()
        else:
            current_item = self.chat_list.currentItem()
            if current_item and current_item.flags() & Qt.ItemFlag.ItemIsSelectable:
                self.start_renaming(current_item)

    def handle_delete_key(self):
        current_item = self.chat_list.currentItem()
        if current_item and current_item.flags() & Qt.ItemFlag.ItemIsSelectable:
            chat_id = current_item.data(Qt.ItemDataRole.UserRole)
            self.remove_chat(chat_id)

    def start_renaming(self, item=None):
        if not self.is_renaming:
            try:
                current_item = item or self.chat_list.currentItem()
                if current_item and current_item.flags() & Qt.ItemFlag.ItemIsSelectable:
                    self.is_renaming = True
                    self.renaming_item = current_item
                    self.chat_item_delegate.renaming_item = self.chat_list.row(current_item)
                    
                    self.rename_editor = SafeLineEdit(self.chat_list)
                    self.rename_editor.setText(current_item.text())
                    self.rename_editor.selectAll()
                    
                    rect = self.chat_list.visualItemRect(current_item)
                    self.rename_editor.setGeometry(rect)
                    
                    self.rename_editor.setStyleSheet("""
                        QLineEdit {
                            background-color: #2d2d3a;
                            color: #ececf1;
                            border: 1px solid #565869;
                            border-radius: 4px;
                            padding-left: 12px;
                            padding-right: 12px;
                            font-size: 13px;
                        }
                        QLineEdit:focus {
                            border-color: #565869;
                        }
                    """)
                    
                    self.rename_editor.show()
                    self.rename_editor.setFocus()
                    self.rename_editor.keyPressEvent = self.rename_editor_key_press
                    
                    self.chat_list.update()
            except Exception as e:
                print(f"Error in start_renaming: {e}")
                self.cancel_renaming()

    def rename_editor_key_press(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish_renaming()
        else:
            QLineEdit.keyPressEvent(self.rename_editor, event)

    def cancel_renaming(self):
        """Aggressively cancel renaming"""
        if self.is_renaming:
            if self.rename_editor:
                # Immediately hide and remove the editor
                self.rename_editor.blockSignals(True)  # Prevent any pending signals
                self.rename_editor.hide()
                self.rename_editor.setParent(None)
                self.rename_editor.deleteLater()
                self.rename_editor = None

            # Clear all states immediately
            self.is_renaming = False
            if self.chat_item_delegate:
                self.chat_item_delegate.renaming_item = None
            self.renaming_item = None
            
            # Force immediate UI update
            self.chat_list.viewport().update()
            QApplication.processEvents()

    def finish_renaming(self):
        """Enhanced finish_renaming with safer cleanup"""
        if not self.is_renaming or not self.renaming_item:
            return
        
        try:
            if self.rename_editor:
                new_title = self.rename_editor.text().strip()
                if new_title:
                    chat_id = self.renaming_item.data(Qt.ItemDataRole.UserRole)
                    self.rename_chat(chat_id, new_title)
                    self.renaming_item.setText(new_title)
                    
                    # If this is the current chat, update the chat manager
                    if chat_id == self.current_chat_id:
                        print(f"ChatWindow: Updating current chat title to: {new_title}")
                        # Force a chat update to refresh Oracle menu and other UI elements
                        self.chat_manager.chat_updated.emit(chat_id)
                        # Re-select the chat to refresh the view
                        self.load_chat_thread(chat_id)
        except Exception as e:
            print(f"Error in finish_renaming: {e}")
        finally:
            self.cleanup_rename_editor()

    def cleanup_rename_editor(self):
        """Safely clean up the rename editor and related states"""
        if hasattr(self, 'rename_editor') and self.rename_editor is not None:
            try:
                editor = self.rename_editor
                self.rename_editor = None  # Clear reference first
                editor.hide()
                editor.setParent(None)
                editor.deleteLater()
            except Exception as e:
                print(f"Error cleaning up rename editor: {e}")
        
        # Clear all states
        self.is_renaming = False
        if self.chat_item_delegate:
            self.chat_item_delegate.renaming_item = None
        self.renaming_item = None
        self.chat_list.viewport().update()
        QApplication.processEvents()

    def rename_chat(self, chat_id, new_title):
        """Updates the chat title in the database."""
        session = get_session()
        try:
            chat = session.query(Chat).filter(Chat.id == chat_id).one()
            chat.title = new_title
            chat.modified_date = datetime.utcnow()
            session.commit()
        except NoResultFound:
            raise Exception(f"Chat with id {chat_id} not found.")
        except Exception as e:
            raise Exception(f"Failed to rename chat: {str(e)}")
        finally:
            session.close()

    def position_new_chat_button(self):
        button_size = self.new_chat_button.width()
        self.new_chat_button.move(
            self.width() - button_size - 15,
            15
        )

    def on_chat_created(self, chat_id):
        print(f"New chat created with ID: {chat_id}")
        self.load_chat_list(self.search_input.text())

    def on_chat_updated(self, chat_id):
        print(f"Chat updated with ID: {chat_id}")
        self.load_chat_list(self.search_input.text())

    def on_chat_deleted(self, chat_id):
        print(f"Chat deleted with ID: {chat_id}")
        self.load_chat_list(self.search_input.text())

    def __del__(self):
        """Cleanup resources when the window is destroyed."""
        try:
            if hasattr(self, 'spinner_movie') and self.spinner_movie is not None:
                self.spinner_movie.stop()
                self.spinner_movie = None
        except (RuntimeError, AttributeError):
            # Ignore errors if Qt objects are already deleted
            pass

    def closeEvent(self, event):
        """Handle window close event.
        
        Args:
            event: The close event
        """
        event.ignore()
        self.hide()

    def adjust_input_height(self):
        # Get the document size and adjust height
        doc = self.input_area.document()
        doc_height = doc.size().height()
        # Set minimum and maximum heights
        min_height = 40
        max_height = 200
        new_height = min(max(doc_height + 20, min_height), max_height)
        self.input_area.setFixedHeight(int(new_height))

    def eventFilter(self, source, event):
        if source == self.input_area and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Return and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                # Enter without Shift triggers send
                self.send_message()
                return True
            elif event.key() == Qt.Key.Key_Return and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                # Shift+Enter allows newline
                return False
        return super().eventFilter(source, event)

    def send_message(self):
        """Handle sending messages from both button and Enter key"""
        message = self.input_area.toPlainText().strip()
        if message:
            self.add_to_chat_thread()
            # Don't clear the input area here, it will be cleared in on_stream_started
            self.input_area.setEnabled(True)  # Ensure input stays enabled

    def handle_voice(self):
        # Implement voice functionality
        pass

    def on_search_text_changed(self, text):
        """Handle search text changes"""
        if self.is_renaming:
            try:
                # Force finish renaming with cancel=True
                self.finish_renaming(cancel=True)
            except Exception as e:
                # If finish_renaming fails, force cleanup
                if self.rename_editor:
                    self.rename_editor.hide()
                    self.rename_editor.setParent(None)
                    self.rename_editor.deleteLater()
                    self.rename_editor = None
                self.is_renaming = False
                if self.chat_item_delegate:
                    self.chat_item_delegate.renaming_item = None
                self.renaming_item = None
                self.chat_list.viewport().update()
                QApplication.processEvents()
        
        # Now load the chat list
        self.load_chat_list(text)

    def on_current_chat_changed(self, chat_id):
        """Handle current chat changes"""
        print(f"ChatWindow: Received current_chat_changed signal with ID: {chat_id}")
        if chat_id:
            self.current_chat_id = chat_id
            self.select_chat_by_id(chat_id)
            self.load_chat_thread(chat_id)

    def on_stream_started(self, chat_id):
        """Handle start of streaming response"""
        self.current_streaming_chat_id = chat_id
        self.streaming_response = ""
        self.input_area.setEnabled(False)
        self.send_button.setEnabled(False)
        self.input_area.setPlaceholderText("AI is responding...")

    def on_stream_finished(self, chat_id):
        """Handle end of streaming response"""
        self.current_streaming_chat_id = None
        self.input_area.setEnabled(True)
        self.send_button.setEnabled(True)
        self.input_area.clear()  # Clear the input
        self.input_area.setPlaceholderText("Send a message...")  # Reset placeholder
        self.input_area.setFocus()  # Set focus back to input
        # Remove auto-scroll

    def on_stream_error(self, error_msg):
        """Handle streaming errors"""
        QtWidgets.QMessageBox.critical(self, "Error", f"Chat error: {error_msg}")
        self.current_streaming_chat_id = None
        self.input_area.setEnabled(True)
        self.send_button.setEnabled(True)
        self.input_area.setPlaceholderText("Send a message...")

    def on_typing_indicator_changed(self, visible):
        """Show/hide typing indicator"""
        self.typing_indicator.setVisible(visible)

    def on_stream_token(self, token):
        """Handle incoming token from stream"""
        if not self.current_streaming_chat_id:
            return
        
        self.streaming_response += token
        
        # Check if scrollbar is at bottom before updating
        scrollbar = self.chat_thread_view.verticalScrollBar()
        was_at_bottom = scrollbar.value() >= scrollbar.maximum() - 10  # Add small threshold
        
        # Update the current response in the view with correct styling
        cursor = self.chat_thread_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        
        # First token handling - set up initial formatting
        if len(self.streaming_response) == len(token):
            cursor.insertBlock()
            cursor.insertBlock()  # Double space before response
            
            response_format = cursor.blockFormat()
            response_format.setLineHeight(120, 1)
            response_format.setTopMargin(4)
            response_format.setBottomMargin(4)
            cursor.setBlockFormat(response_format)
            
            # Set up text format
            response_char_format = cursor.charFormat()
            response_char_format.setFontPointSize(15)
            response_char_format.setFontWeight(400)
            response_char_format.setForeground(QColor("#8e8ea0"))
            cursor.setCharFormat(response_char_format)
        
        cursor.insertText(token)
        
        # If we were at bottom, keep it there
        if was_at_bottom:
            scrollbar.setValue(scrollbar.maximum())