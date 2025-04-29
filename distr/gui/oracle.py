from PyQt6.QtCore import (
    QTimer, 
    QPropertyAnimation, 
    QEasingCurve, 
    QSequentialAnimationGroup, 
    QParallelAnimationGroup,
    Qt,
    QPoint,
    pyqtProperty
)
from distr.core.utils import (
    load_settings_from_db, 
    save_settings_to_db, 
    get_screens_hash,    
)
from distr.core.constants import ICONS_DIR, IMAGES_DIR, ORACLE_DIR
from distr.core.db import get_session, ScreenPosition, Chat
from distr.core.signals import signal_manager  
from PyQt6 import QtWidgets, QtGui, QtCore
from PyQt6.QtWidgets import QApplication
from distr.gui.chat import ChatWindow 
from distr.gui.snippets import SnippetWindow
from distr.gui.action import ActionWindow
import logging
import os
from PyQt6.QtGui import QAction
import hashlib  # Add this import at the top of the file

logger = logging.getLogger(__name__)


class RoundContainer(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setBrush(QtGui.QColor(255, 255, 255))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawEllipse(self.rect())

    def resizeEvent(self, event):
        path = QtGui.QPainterPath()
        path.addEllipse(0, 0, self.width(), self.height())
        mask = QtGui.QRegion(path.toFillPolygon().toPolygon())
        self.setMask(mask)

class OracleWindow(QtWidgets.QMainWindow):

    def __init__(self, settings_window, about_window, voice_box, chat_manager, parent=None):
        super().__init__(parent)
        self._updating_menu = False
        self.is_exiting = False  # Flag to track exit state
        
        self.settings = load_settings_from_db()
        
        # Initialize size from settings
        self.content_size = self.settings.get('sphere_size', 180)  # Default to 180px
        self.shadow_size = int(self.content_size * 0.022)  # ~4px at 180px
        self.stroke_width = int(self.content_size * 0.033)  # ~6px at 180px
        
        self.total_size = self.content_size + 2 * (self.shadow_size + self.stroke_width)
        
        # Setup window flags and attributes
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Add screen change detection
        self.screen_watcher = QTimer()
        self.screen_watcher.timeout.connect(self.check_screen_changes)
        self.screen_watcher.start(1000)  # Check every second
        self.current_screens_hash = get_screens_hash()
        logging.debug(f"Oracle init - Current screens hash: {self.current_screens_hash}")
        
        self.voice_box = voice_box
        self.chat_manager = chat_manager
        self.settings_window = settings_window
        self.about_window = about_window
        # Connect the OracleWindow's move event to trigger VoiceBox position update
        self.moveEvent = self.on_move_event

        signal_manager.change_oracle.connect(self.next_image)
        signal_manager.show_oracle.connect(self.show_globe)
        signal_manager.hide_oracle.connect(self.hide_globe)

        signal_manager.enable_tray.connect(self.enable_tray)
        signal_manager.disable_tray.connect(self.disable_tray)
        
        self.setWindowFlags(QtCore.Qt.WindowType.FramelessWindowHint | QtCore.Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)

        self._shadow_color = QtGui.QColor(0, 0, 0, 100)  # Initial shadow color
        self._border_color = QtGui.QColor(0, 0, 0)  # Initial black color
        self._inner_shadow_color = QtGui.QColor(0, 0, 0, 100)  # Initial inner shadow color
        self.fill_color = QtGui.QColor(255, 255, 255, 200)  # White with some transparency

        self.animation_group = QParallelAnimationGroup(self)
        self.border_animation_group = QSequentialAnimationGroup(self)
        self.inner_shadow_animation_group = QSequentialAnimationGroup(self)

        self.border_forward_animation = QPropertyAnimation(self, b"border_color")
        self.border_backward_animation = QPropertyAnimation(self, b"border_color")
        self.inner_shadow_forward_animation = QPropertyAnimation(self, b"inner_shadow_color")
        self.inner_shadow_backward_animation = QPropertyAnimation(self, b"inner_shadow_color")
        self.shadow_forward_animation = QPropertyAnimation(self, b"shadow_color")
        self.shadow_backward_animation = QPropertyAnimation(self, b"shadow_color")

        for anim in [self.border_forward_animation, self.border_backward_animation,
                     self.inner_shadow_forward_animation, self.inner_shadow_backward_animation,
                     self.shadow_forward_animation, self.shadow_backward_animation]:
            anim.setDuration(1000)  # 1 second duration
            anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

        self.border_animation_group.addAnimation(self.border_forward_animation)
        self.border_animation_group.addAnimation(self.border_backward_animation)
        self.inner_shadow_animation_group.addAnimation(self.inner_shadow_forward_animation)
        self.inner_shadow_animation_group.addAnimation(self.inner_shadow_backward_animation)

        self.shadow_animation_group = QSequentialAnimationGroup(self)
        self.shadow_animation_group.addAnimation(self.shadow_forward_animation)
        self.shadow_animation_group.addAnimation(self.shadow_backward_animation)

        self.animation_group.addAnimation(self.border_animation_group)
        self.animation_group.addAnimation(self.inner_shadow_animation_group)
        self.animation_group.addAnimation(self.shadow_animation_group)
        self.animation_group.setLoopCount(-1)  # Infinite loop

        screen = QtWidgets.QApplication.primaryScreen().geometry()
        x_position = screen.width() - self.total_size - 40
        y_position = (screen.height() - self.total_size) // 2

        self.setGeometry(x_position, y_position, self.total_size, self.total_size)

        self.dragging = False
        self.offset = QtCore.QPoint()

        self.round_container = RoundContainer(self)
        self.round_container.setGeometry(self.shadow_size + self.stroke_width, 
                                         self.shadow_size + self.stroke_width, 
                                         self.content_size, 
                                         self.content_size)

        self.gif_label = QtWidgets.QLabel(self.round_container)
        self.gif_label.setGeometry(0, 0, self.content_size, self.content_size)
        self.gif_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.gif_label.setScaledContents(True)

        self.current_image_index = 0
        self.forward = True
        self.load_globe_image()

        # Initialize menu items before creating menu
        self.menu = None
        self.chat_id_menu_item = None
        
        # Connect to chat manager signals with proper order
        self.chat_manager = chat_manager
        self.chat_manager.current_chat_changed.connect(self.update_chat_id_menu)
        
        # Create the shared menu first
        self.menu = self.create_menu()
        
        # Then create tray icon with the menu
        self.tray_icon = QtWidgets.QSystemTrayIcon(self)
        self.create_tray_icon()
        
        # Update initial chat ID display
        current_chat_id = self.chat_manager.get_current_chat()
        if current_chat_id:
            QTimer.singleShot(0, lambda: self.update_chat_id_menu(current_chat_id))

        self.globe_visible = True
        self.chat_window = ChatWindow(self.chat_manager)  # Make sure this is initialized
        
        # Connect the direct oracle change signal
        signal_manager.direct_oracle_change.connect(self.load_specific_oracle)

        # Load initial oracle from settings
        if self.settings.get('selected_oracle'):
            self.load_specific_oracle(self.settings['selected_oracle'])
        else:
            self.load_globe_image()  # Default behavior

        if self.settings.get('restore_position'):
            self.restore_screen_position()

        # Add SnippetWindow instance
        self.snippet_window = SnippetWindow()

        self.action_window = None

        # Connect position and size signals
        signal_manager.oracle_position_changed.connect(self.handle_position_change)
        # signal_manager.sphere_size_changed.connect(self.update_sphere_size)

        self.show()

        signal_manager.oracle_size_changed.connect(self.update_size)

        # Initialize listening state from settings
        self.is_listening = self.settings.get('last_listening_state', True)
        
        # Initialize the state properly
        self.initialize_listening_state()

    def initialize_listening_state(self):
        """Initialize the listening state based on saved settings."""
        startup_state = self.settings.get('startup_listening_state', 'remember')
        
        if startup_state == 'remember':
            # Use the last saved state
            should_listen = self.settings.get('last_listening_state', True)
        elif startup_state == 'stop':
            should_listen = False
        elif startup_state == 'start':
            should_listen = True
        else:
            should_listen = True  # Default to listening if something goes wrong
        
        # Update the UI and state
        if should_listen:
            self.enable_tray()
        else:
            self.disable_tray()
        
        logging.debug(f"Initialized listening state: {should_listen}")

    def set_voice_box(self, voice_box):
        self.voice_box = voice_box
        

    def enable_tray(self):
        self.is_listening = True
        icon_path = os.path.join(ICONS_DIR, "tray.png")
        icon = QtGui.QIcon(icon_path)
        self.tray_icon.setIcon(icon)
        self.listen_action.setChecked(True)
        self.listen_action.setText("Listening")
        signal_manager.voice_set_is_listening.emit(True)
        signal_manager.action_set_is_listening.emit(True)
        self.save_listening_state()  # Save state when enabled

    def disable_tray(self):
        self.is_listening = False
        icon_path = os.path.join(ICONS_DIR, "tray-disabled.png")
        icon = QtGui.QIcon(icon_path)
        self.tray_icon.setIcon(icon)
        self.listen_action.setChecked(False)
        self.listen_action.setText("Not Listening")
        signal_manager.voice_set_is_listening.emit(False)
        signal_manager.action_set_is_listening.emit(False)
        self.save_listening_state()  # Save state when disabled

    def save_listening_state(self):
        """Save the current listening state to settings"""
        try:
            settings = load_settings_from_db()
            settings['last_listening_state'] = self.is_listening
            save_settings_to_db(settings)
            logging.debug(f"Saved listening state: {self.is_listening}")
        except Exception as e:
            logging.error(f"Error saving listening state: {e}")

    def create_menu(self):
        # Create a single menu instance that will be shared
        self.menu = QtWidgets.QMenu()
        
        self.listen_action = QAction("Listening", self.menu)
        self.listen_action.setCheckable(True)
        self.listen_action.setChecked(True)
        self.listen_action.triggered.connect(self.toggle_listening)
        self.menu.addAction(self.listen_action)

        self.menu.addSeparator()
        
        # Create actions for functionality that should be disabled when EULA not accepted
        self.record_action_action = QAction("Record an Action", self.menu)
        self.record_action_action.triggered.connect(lambda: None)
        self.menu.addAction(self.record_action_action)

        self.new_chat_action = QAction("New Chat", self.menu)
        self.new_chat_action.triggered.connect(lambda: signal_manager.trigger_new_chat.emit())
        self.menu.addAction(self.new_chat_action)

        self.menu.addSeparator()
        
        # Create a single chat ID menu item that will be shared between both menus
        self.chat_id_menu_item = QAction("No active chat", self.menu)
        self.chat_id_menu_item.setEnabled(False)
        self.menu.addAction(self.chat_id_menu_item)
        
        self.menu.addSeparator()
        
        self.chats_action = QAction("Chats", self.menu)
        self.chats_action.triggered.connect(self.show_chat_window)
        self.menu.addAction(self.chats_action)

        self.actions_action = QAction("Actions", self.menu)
        self.actions_action.triggered.connect(self.show_actions)
        self.menu.addAction(self.actions_action)

        self.snippets_action = QAction("Snippets", self.menu)
        self.snippets_action.triggered.connect(self.show_snippet_window)
        self.menu.addAction(self.snippets_action)
        
        self.menu.addSeparator()
        
        self.toggle_visibility_action = QAction("Hide Oracle", self.menu)
        self.toggle_visibility_action.triggered.connect(self.toggle_visibility)
        self.menu.addAction(self.toggle_visibility_action)
        
        self.change_globe_action = QAction("Change Oracle", self.menu)
        self.change_globe_action.triggered.connect(self.next_image)
        self.menu.addAction(self.change_globe_action)
        
        self.menu.addSeparator()
        
        self.about_action = QAction("About DecisionsAI", self.menu)
        self.about_action.triggered.connect(self.show_about_window)
        self.menu.addAction(self.about_action)
        
        self.menu.addSeparator()
        
        self.preferences_action = QAction("Preferences", self.menu)
        self.preferences_action.triggered.connect(self.show_settings_window)
        self.menu.addAction(self.preferences_action)
        
        self.menu.addSeparator()
        
        self.exit_action = QAction("Quit", self.menu)
        self.exit_action.triggered.connect(self.exit_app)
        self.menu.addAction(self.exit_action)
        
        # Connect the aboutToShow signal to update the menu
        self.menu.aboutToShow.connect(self.update_menu)
        
        return self.menu

    def toggle_listening(self):
        if self.listen_action.isChecked():
            self.enable_tray()
        else:
            self.disable_tray()
        # Save state immediately when toggled
        self.save_listening_state()

    def update_menu(self):
        # Don't update menu during exit
        if hasattr(self, 'is_exiting') and self.is_exiting:
            return
            
        self.change_globe_action.setVisible(self.globe_visible)
        
        # Check EULA acceptance status
        settings = load_settings_from_db()
        eula_accepted = settings.get('accepted_eula', False)
        
        # Enable/disable features based on EULA acceptance
        features_requiring_eula = [
            self.record_action_action,
            self.new_chat_action,
            self.chats_action, 
            self.actions_action,
            self.snippets_action,
            self.change_globe_action,
            self.about_action
        ]
        
        for action in features_requiring_eula:
            action.setEnabled(eula_accepted)
            
        # If EULA not accepted, add tooltips explaining why
        if not eula_accepted:
            tooltip = "Accept EULA in Preferences to enable this feature"
            for action in features_requiring_eula:
                action.setToolTip(tooltip)
        else:
            # Clear tooltips when EULA is accepted
            for action in features_requiring_eula:
                action.setToolTip("")

    def is_globe_window_open(self):
        return self.globe_visible

    def create_tray_icon(self):
        icon_path = os.path.join(ICONS_DIR, "tray.png")
        icon = QtGui.QIcon(icon_path)
        self.tray_icon.setIcon(icon)
        
        # Ensure we're using the exact same menu instance
        if self.menu:
            self.tray_icon.setContextMenu(self.menu)
        self.tray_icon.show()

    def toggle_visibility(self):
        if self.isVisible():
            self.hide_globe()
            new_text = "Show Oracle"
        else:
            self.show_globe()
            new_text = "Hide Oracle"
        
        self.toggle_visibility_action.setText(new_text)

    def load_globe_image(self):
        # Get settings to check for custom oracle
        from distr.core.utils import load_settings_from_db
        settings = load_settings_from_db()
        
        # Determine which gif file to load
        if settings.get('selected_oracle'):
            gif_path = os.path.join(IMAGES_DIR, "oracle", settings.get('selected_oracle'))
        else:
            gif_path = os.path.join(IMAGES_DIR, "oracle", f"{self.current_image_index}.gif")
        
        if not os.path.exists(gif_path):
            logging.error(f"Error: GIF file not found at {gif_path}")
            # Fallback to default
            gif_path = os.path.join(IMAGES_DIR, "oracle", "0.gif")
        
        self.movie = QtGui.QMovie(gif_path)
        if not self.movie.isValid():
            logging.error(f"Error: Invalid GIF file at {gif_path}")
            return
        
        self.movie.frameChanged.connect(self.update_frame)
        self.gif_label.setMovie(self.movie)
        self.movie.start()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        # Draw shadow
        shadow_rect = self.rect().adjusted(self.shadow_size, self.shadow_size, -self.shadow_size, -self.shadow_size)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(self.shadow_color)
        painter.drawEllipse(shadow_rect)

        # Draw filled content
        content_rect = QtCore.QRect(self.shadow_size + self.stroke_width, 
                                    self.shadow_size + self.stroke_width, 
                                    self.content_size, 
                                    self.content_size)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(self.fill_color)
        painter.drawEllipse(content_rect)

        # Draw inner shadow
        inner_shadow_rect = content_rect.adjusted(2, 2, -2, -2)
        center = inner_shadow_rect.center()
        gradient = QtGui.QRadialGradient(
            center.x(), center.y(),
            inner_shadow_rect.width() / 2
        )
        gradient.setColorAt(0.95, QtGui.QColor(0, 0, 0, 0))
        gradient.setColorAt(1, self.inner_shadow_color)
        painter.setBrush(gradient)
        painter.drawEllipse(inner_shadow_rect)

        # Draw animated border
        painter.setPen(QtGui.QPen(self.border_color, self.stroke_width, 
                                  QtCore.Qt.PenStyle.SolidLine, 
                                  QtCore.Qt.PenCapStyle.RoundCap, 
                                  QtCore.Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawEllipse(content_rect)

    def update_frame(self):
        current_frame = self.movie.currentPixmap()
        scaled_frame = current_frame.scaled(
            self.content_size + 75, self.content_size + 75,
            QtCore.Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            QtCore.Qt.TransformationMode.SmoothTransformation
        )
        center = scaled_frame.rect().center()
        target_rect = QtCore.QRect(0, 0, self.content_size, self.content_size)
        target_rect.moveCenter(center)
        cropped_frame = scaled_frame.copy(target_rect)
        self.gif_label.setPixmap(cropped_frame)

    def resizeEvent(self, event):
        path = QtGui.QPainterPath()
        path.addEllipse(0, 0, self.total_size, self.total_size)
        self.setMask(QtGui.QRegion(path.toFillPolygon().toPolygon()))

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.dragging = True
            self.offset = event.position().toPoint()
        elif event.button() == QtCore.Qt.MouseButton.RightButton:
            self.menu.exec(event.globalPosition().toPoint())

    def next_image(self):
        """Change to the next oracle image if EULA is accepted"""
        # Check if EULA is accepted
        if not self._check_eula_accepted():
            return
            
        # Get all available oracle files
        oracle_files = [f for f in os.listdir(ORACLE_DIR) if f.endswith('.gif')]
        oracle_files.sort(key=lambda x: int(os.path.splitext(x)[0]) if os.path.splitext(x)[0].isdigit() else float('inf'))
        
        # Find current index and get next one
        current_file = f"{self.current_image_index}.gif"
        try:
            current_idx = oracle_files.index(current_file)
            next_idx = (current_idx + 1) % len(oracle_files)
            next_file = oracle_files[next_idx]
            
            # Save to settings
            settings = load_settings_from_db()
            settings['selected_oracle'] = next_file
            save_settings_to_db(settings)
            
            # Update current index and load new image
            name = os.path.splitext(next_file)[0]
            if name.isdigit():
                self.current_image_index = int(name)
            self.load_globe_image()
            
            # Emit signal to sync settings window dropdown
            signal_manager.sync_oracle_selection.emit(next_file)
            logging.debug(f"Changed oracle to: {next_file} via context menu")
        except ValueError:
            logging.error(f"Current oracle file {current_file} not found in directory")

    def exit_app(self):
        self.reload_settings()
        print("Exiting app")

        # Hide the oracle window itself
        self.hide_globe()
        
        # Set a flag to prevent any further actions
        self.is_exiting = True
        
        # Emit exit signal first to notify other components
        signal_manager.exit_app.emit()
        
        # Hide all windows first
        if hasattr(self, 'voice_box') and self.voice_box:
            self.voice_box.hide()
        if hasattr(self, 'about_window') and self.about_window:
            self.about_window.hide()
        if hasattr(self, 'settings_window') and self.settings_window:
            self.settings_window.hide()
        if hasattr(self, 'chat_window') and self.chat_window:
            self.chat_window.hide()
        if hasattr(self, 'snippet_window') and self.snippet_window:
            self.snippet_window.hide()
        if hasattr(self, 'action_window') and self.action_window:            
            self.action_window.hide()
            
        # Explicitly clean up any animation resources
        if hasattr(self, 'animation_group') and self.animation_group:
            self.animation_group.stop()
        if hasattr(self, 'movie') and self.movie:
            self.movie.stop()
            
        # Stop tray icon
        if hasattr(self, 'tray_icon') and self.tray_icon:
            self.tray_icon.hide()
            
        # Give time for signal processing and cleanup
        QtCore.QCoreApplication.processEvents()
        
        # Finally quit the application
        QApplication.instance().quit()

    def mouseMoveEvent(self, event):
        if self.dragging:
            # Emit signal to notify that dragging started (first drag movement)
            if not hasattr(self, '_drag_notified'):
                signal_manager.oracle_drag_started.emit()
                self._drag_notified = True
            
            new_position = event.globalPosition().toPoint() - self.offset
            self.move(new_position)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.dragging = False
            if hasattr(self, '_drag_notified'):
                delattr(self, '_drag_notified')

        if self.settings.get('restore_position'):
            pos = self.pos()
            self.save_current_position()


    def on_move_event(self, event):
        super().moveEvent(event)
        signal_manager.update_voice_box_position.emit()

    def play_voice_box(self):
        print("Playing voice box animation")
        signal_manager.update_voice_box_position.emit()
        self.voice_box.setVisible(True)  # Use setVisible
        self.voice_box.play_gif()

    def stop_voice_box(self):
        print("Stopping voice box")
        self.voice_box.stop_gif()
        self.voice_box.hide()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if os.path.isfile(file_path):
                print(f"File dropped: {file_path}")
            elif os.path.isdir(file_path):
                print(f"Directory dropped: {file_path}")
                self.print_directory_tree(file_path)

    def print_directory_tree(self, start_path):
        for root, dirs, files in os.walk(start_path):
            level = root.replace(start_path, '').count(os.sep)
            indent = ' ' * 4 * level
            print(f"{indent}{os.path.basename(root)}/")
            sub_indent = ' ' * 4 * (level + 1)
            for file in files:
                print(f"{sub_indent}{file}")

    def show_about_window(self):
        """Show the About window if EULA is accepted"""
        # Check if EULA is accepted
        if not self._check_eula_accepted():
            return
            
        self.about_window.show()
        self.about_window.raise_()
        self.about_window.activateWindow()

    def show_settings_window(self):
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def show_globe(self):
        self.globe_visible = True
        print(f"Oracle shown. self.isVisible(): {self.isVisible()}, globe_visible: {self.globe_visible}")
        QTimer.singleShot(0, self.show)
        QTimer.singleShot(0, self.gif_label.show)
        QTimer.singleShot(10, self.update)
        QTimer.singleShot(20, self.update_menu)

    def hide_globe(self):
        self.globe_visible = False
        print(f"Oracle hidden. self.isVisible(): {self.isVisible()}, globe_visible: {self.globe_visible}")
        QTimer.singleShot(0, self.hide)
        QTimer.singleShot(10, self.update_menu)

    def show_chat_window(self):
        """Open the chat window if EULA is accepted"""
        # Check if EULA is accepted
        if not self._check_eula_accepted():
            return
            
        if not self.chat_window:
            self.chat_window = ChatWindow(self.chat_manager)
        self.chat_window.show()
        self.chat_window.raise_()
        self.chat_window.activateWindow()

    def show_actions(self):
        """Show the Actions window if EULA is accepted"""
        # Check if EULA is accepted
        if not self._check_eula_accepted():
            return
            
        if not self.action_window:
            self.action_window = ActionWindow()
        self.action_window.show()
        self.action_window.raise_()
        self.action_window.activateWindow()

    # Add this new method
    def closeEvent(self, event):
        if self.settings.get('restore_position'):
            self.save_current_position()
        event.accept()

    def set_color_animation(self, color, animation_speed=1000):
        start_color = QtGui.QColor(0, 0, 0)  # Always start from black
        end_color = QtGui.QColor(*color)
        
        self.border_forward_animation.setStartValue(start_color)
        self.border_forward_animation.setEndValue(end_color)
        self.border_backward_animation.setStartValue(end_color)
        self.border_backward_animation.setEndValue(start_color)

        inner_shadow_start = QtGui.QColor(0, 0, 0, 100)
        inner_shadow_end = QtGui.QColor(color[0], color[1], color[2], 100)
        
        self.inner_shadow_forward_animation.setStartValue(inner_shadow_start)
        self.inner_shadow_forward_animation.setEndValue(inner_shadow_end)
        self.inner_shadow_backward_animation.setStartValue(inner_shadow_end)
        self.inner_shadow_backward_animation.setEndValue(inner_shadow_start)

        # Update shadow animation
        shadow_start = QtGui.QColor(0, 0, 0, 100)
        shadow_end = QtGui.QColor(color[0], color[1], color[2], 100)
        
        self.shadow_forward_animation.setStartValue(shadow_start)
        self.shadow_forward_animation.setEndValue(shadow_end)
        self.shadow_backward_animation.setStartValue(shadow_end)
        self.shadow_backward_animation.setEndValue(shadow_start)

        for anim in [self.border_forward_animation, self.border_backward_animation,
                     self.inner_shadow_forward_animation, self.inner_shadow_backward_animation,
                     self.shadow_forward_animation, self.shadow_backward_animation]:
            anim.setDuration(animation_speed)

        self.animation_group.stop()
        self.animation_group.start()

    @pyqtProperty(QtGui.QColor)
    def border_color(self):
        return self._border_color

    @border_color.setter
    def border_color(self, color):
        self._border_color = color
        self.update()

    @pyqtProperty(QtGui.QColor)
    def inner_shadow_color(self):
        return self._inner_shadow_color

    @inner_shadow_color.setter
    def inner_shadow_color(self, color):
        self._inner_shadow_color = color
        self.update()

    @pyqtProperty(QtGui.QColor)
    def shadow_color(self):
        return self._shadow_color

    @shadow_color.setter
    def shadow_color(self, color):
        self._shadow_color = color
        self.update()

    def set_red_animation(self, animation_speed=1000):
        self.set_color_animation((230, 0, 0), animation_speed)  # Red (#e60000)

    def set_yellow_animation(self, animation_speed=1000):
        self.set_color_animation((227, 215, 18), animation_speed)  # Yellow (#e3d712)

    def set_blue_animation(self, animation_speed=1000):
        self.set_color_animation((8, 201, 236), animation_speed)  # Blue (#08c9ec)

    def set_green_animation(self, animation_speed=1000):
        self.set_color_animation((68, 186, 45), animation_speed)  # Green (#44ba2d)

    def set_white_animation(self, animation_speed=1000):
        self.set_color_animation((255, 255, 255), animation_speed)  # White

    def reset_color_animation(self):
        self.set_color_animation((0, 0, 0), 1000)  # Reset to black
        # Reset shadow color animations
        shadow_color = QtGui.QColor(0, 0, 0, 100)
        self.shadow_forward_animation.setStartValue(shadow_color)
        self.shadow_forward_animation.setEndValue(shadow_color)
        self.shadow_backward_animation.setStartValue(shadow_color)
        self.shadow_backward_animation.setEndValue(shadow_color)

    def load_specific_oracle(self, filename):
        """Load a specific oracle file"""
        if not filename:
            return
        
        name = os.path.splitext(filename)[0]
        if name.isdigit():
            self.current_image_index = int(name)
        
        # Save to settings to ensure persistence
        settings = load_settings_from_db()
        settings['selected_oracle'] = filename
        save_settings_to_db(settings)
        
        self.load_globe_image()
        logging.debug(f"Loaded specific oracle: {filename}")

    def check_screen_changes(self):
        """Handle screen configuration changes"""        
        new_hash = get_screens_hash()
        if new_hash != self.current_screens_hash:
            logger = logging.getLogger(__name__)
            logger.debug("\n=== Screen Configuration Changed ===")
            logger.debug(f"Old hash: {self.current_screens_hash}")
            logger.debug(f"New hash: {new_hash}")
                        
            # Update hash after saving position
            self.current_screens_hash = new_hash
                
            with get_session() as session:
                position = session.query(ScreenPosition).filter_by(
                    screens_id=self.current_screens_hash,
                ).first()
                                        
                if position:
                    logger.debug(f"Final position: ({position.pos_x}, {position.pos_y})")
                    self.move(int(position.pos_x), int(position.pos_y))




    def restore_position_for_screen(self, preferred_screen=None):
        """Restore position with preference for the current screen"""
        logger = logging.getLogger(__name__)
        screens_id = get_screens_hash()
        
        # Get all available screens
        available_screens = QApplication.screens()
        logger.debug(f"Available screens: {[screen.name() for screen in available_screens]}")
        
        # Try to find the preferred screen
        target_screen = None
        if preferred_screen:
            for screen in available_screens:
                if screen.name() == preferred_screen.name():
                    target_screen = screen
                    break
        
        # If no target screen, use primary
        if not target_screen:
            target_screen = QApplication.primaryScreen()
        
        logger.debug(f"Selected target screen: {target_screen.name()}")
        screen_geo = target_screen.geometry()
        
    def reload_settings(self):
        self.settings = load_settings_from_db()

    def moveEvent(self, event):
        super().moveEvent(event)
        if self.settings.get('restore_position'):
            self.save_current_position()
        
        # Update voice box position if needed
        if hasattr(self, 'voice_box'):
            self.voice_box.update_position()

    def save_current_position(self):
        """Save the current window position"""
        # Don't save position during exit
        if hasattr(self, 'is_exiting') and self.is_exiting:
            return
            
        pos = self.pos()
        current_screen = QApplication.screenAt(pos + QPoint(self.total_size // 2, self.total_size // 2))
        
        if not current_screen:
            logger.warning("Could not determine current screen")
            return
        
        screens_id = get_screens_hash()
                
        with get_session() as session:
            # Only look for screens_id since it's the primary key
            position = session.query(ScreenPosition).filter_by(screens_id=screens_id).first()
            
            if position:
                # Update existing record
                position.screen_name = current_screen.name()
                position.pos_x = pos.x() 
                position.pos_y = pos.y() 
            else:
                # Create new record
                position = ScreenPosition(
                    screens_id=screens_id,
                    screen_name=current_screen.name(),
                    pos_x=pos.x(),
                    pos_y=pos.y()
                )
                session.add(position)
            
            try:
                session.commit()
                logger.debug(f"Saved position for {current_screen.name()} in configuration {screens_id}")
            except Exception as e:
                session.rollback()
                logger.error(f"Failed to save position: {e}")
                raise


    def restore_screen_position(self):
        """Restore the window position from saved settings"""
        logger.debug("\n=== Starting position restoration ===")
        screens_id = get_screens_hash()
        current_screen = QApplication.primaryScreen()  # Start with primary screen
        
        with get_session() as session:
            # Get position for current screen configuration
            position = session.query(ScreenPosition).filter_by(screens_id=screens_id).first()
            
            if position:                                
                logger.debug(f"Found saved position for {position.screen_name}: ({position.pos_x}, {position.pos_y})")
                self.move(int(position.pos_x), int(position.pos_y))
            else:
                logger.debug("No saved position found")
                self.set_default_position()


    def set_default_position(self, screen=None):
        """Set the default position on the specified screen or primary screen"""
        if not screen:
            screen = QApplication.primaryScreen()
        
        screen_geo = screen.geometry()
        # Position at middle right by default
        x = screen_geo.right() - self.total_size - 20  # 20px margin from right
        y = screen_geo.top() + (screen_geo.height() // 4)  # 1/4 down from top
        self.move(x, y)

    def mouseDoubleClickEvent(self, event):
        """Handle double click to open chat window"""
        if event.button() == Qt.MouseButton.LeftButton:
            # Only open chat window if EULA is accepted
            if self._check_eula_accepted():
                self.show_chat_window()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    # Add new method to show snippet window
    def show_snippet_window(self):
        """Open the snippet window if EULA is accepted"""
        # Check if EULA is accepted
        if not self._check_eula_accepted():
            return
            
        if not self.snippet_window:
            self.snippet_window = SnippetWindow()
        self.snippet_window.show()
        self.snippet_window.raise_()
        self.snippet_window.activateWindow()

    def update_chat_id_menu(self, chat_id):
        """Update Oracle menu with chat info"""
        if self._updating_menu:
            return
        
        try:
            self._updating_menu = True
            print(f"Oracle: Updating menu with chat ID: {chat_id}")
            if chat_id:
                # Convert chat_id to string and create MD5 hash
                chat_id_str = str(chat_id)
                md5_hash = hashlib.md5(chat_id_str.encode()).hexdigest()
                # Take first 6 characters of the hash
                short_hash = md5_hash[:6]
                # Update the shared menu item
                text = f"Chat: #{short_hash}"
                if self.chat_id_menu_item:
                    self.chat_id_menu_item.setText(text)
                    # Force menu update
                    self.menu.update()
                    if self.tray_icon and self.tray_icon.contextMenu():
                        self.tray_icon.contextMenu().update()
            else:
                if self.chat_id_menu_item:
                    self.chat_id_menu_item.setText("No active chat")
        finally:
            self._updating_menu = False

    def handle_position_change(self, position):
        """Handle position changes from settings window"""
        if position == "Custom":
            return  # Don't change position for custom

        screen = QApplication.primaryScreen()
        screen_geo = screen.geometry()
        window_size = self.size()

        positions = {
            "Top Left": (screen_geo.left(), screen_geo.top()),
            "Top Right": (screen_geo.right() - window_size.width(), screen_geo.top()),
            "Middle Left": (screen_geo.left(), screen_geo.center().y() - window_size.height() // 2),
            "Middle Right": (screen_geo.right() - window_size.width(), screen_geo.center().y() - window_size.height() // 2),
            "Bottom Left": (screen_geo.left(), screen_geo.bottom() - window_size.height()),
            "Bottom Right": (screen_geo.right() - window_size.width(), screen_geo.bottom() - window_size.height())
        }

        if position in positions:
            x, y = positions[position]
            self.move(x, y)
            if self.settings.get('restore_position'):
                self.save_current_position()

    def update_size(self, new_size):
        """Update the oracle size while maintaining proportions"""
        self.content_size = new_size
        self.shadow_size = int(new_size * 0.022)
        self.stroke_width = int(new_size * 0.033)
        
        self.total_size = self.content_size + 2 * (self.shadow_size + self.stroke_width)
        
        # Update window size
        self.setFixedSize(self.total_size, self.total_size)
        
        # Update container and label geometries
        self.round_container.setGeometry(
            self.shadow_size + self.stroke_width,
            self.shadow_size + self.stroke_width,
            self.content_size,
            self.content_size
        )
        
        self.gif_label.setGeometry(0, 0, self.content_size, self.content_size)
        
        # Reload the current image to ensure proper scaling
        if hasattr(self, 'current_movie'):
            self.current_movie.setScaledSize(QtCore.QSize(self.content_size, self.content_size))
        
        # Force a repaint
        self.update()
        
        # Save the new size to settings (only place where we save)
        settings = load_settings_from_db()
        settings['sphere_size'] = new_size
        save_settings_to_db(settings)
        logging.debug(f"Updated oracle size to: {new_size}px")

    def _check_eula_accepted(self):
        """Check if EULA is accepted and show settings window if not"""
        settings = load_settings_from_db()
        eula_accepted = settings.get('accepted_eula', False)
        
        if not eula_accepted:
            # Show a message to the user about accepting EULA
            QtWidgets.QMessageBox.information(
                self,
                "EULA Acceptance Required",
                "You need to accept the End User License Agreement to use this feature.\n\nOpening Preferences to accept EULA.",
                QtWidgets.QMessageBox.StandardButton.Ok
            )
            
            # Open settings window focused on EULA tab
            self.show_settings_window()
            self.settings_window.tab_widget.setCurrentIndex(0)  # EULA is the first tab
            return False
            
        return True
