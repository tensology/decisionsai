"""
ChatManagerQt - Thin Qt bridge around ChatManagerCore.

Exposes pyqtSignals for desktop GUI consumption while delegating all
logic to the pure-Python ChatManagerCore.
"""

from PyQt6.QtCore import QObject, pyqtSignal
from distr.core.chat_manager import ChatManagerCore

import logging

logger = logging.getLogger(__name__)


class ChatManagerQt(QObject):
    """Qt signal bridge for ChatManagerCore.

    Emits Qt signals whenever the core emits events, allowing Qt slots
    to react.  All actual logic lives in ChatManagerCore.
    """

    chat_created = pyqtSignal(int)
    chat_updated = pyqtSignal(int)
    chat_cleared = pyqtSignal(int)
    chat_deleted = pyqtSignal(int)
    current_chat_changed = pyqtSignal(int)

    def __init__(self, core: ChatManagerCore, parent=None):
        super().__init__(parent)
        self._core = core
        self._bridge_events()

    def _bridge_events(self) -> None:
        """Wire core events to Qt signals."""
        self._core.on("chat_created", self._emit_chat_created)
        self._core.on("chat_updated", self._emit_chat_updated)
        self._core.on("chat_cleared", self._emit_chat_cleared)
        self._core.on("chat_deleted", self._emit_chat_deleted)
        self._core.on("current_chat_changed", self._emit_current_chat_changed)

    # Wrapped emitters to swallow RuntimeError during shutdown
    def _emit_chat_created(self, chat_id: int) -> None:
        try:
            self.chat_created.emit(chat_id)
        except RuntimeError:
            pass

    def _emit_chat_updated(self, chat_id: int) -> None:
        try:
            self.chat_updated.emit(chat_id)
        except RuntimeError:
            pass

    def _emit_chat_cleared(self, chat_id: int) -> None:
        try:
            self.chat_cleared.emit(chat_id)
        except RuntimeError:
            pass

    def _emit_chat_deleted(self, chat_id: int) -> None:
        try:
            self.chat_deleted.emit(chat_id)
        except RuntimeError:
            pass

    def _emit_current_chat_changed(self, chat_id: int) -> None:
        try:
            self.current_chat_changed.emit(chat_id)
        except RuntimeError:
            pass

    # Delegate all attribute access to core
    def __getattr__(self, name: str):
        # __getattr__ is only called when normal attribute lookup fails
        return getattr(self._core, name)
