"""Non-activating Development drawer revealed by a top-left pointer dwell."""

from __future__ import annotations

import json
import logging
import platform
import time
from typing import Any, Callable

from PyQt6 import QtCore, QtGui, QtWidgets

from distr.core.db import Chat, get_session
from distr.core.db.projects import Project
from distr.core.db.workflow import DevelopmentWorkItem


logger = logging.getLogger(__name__)


def is_top_left_corner(
    pointer: QtCore.QPoint,
    geometry: QtCore.QRect,
    tolerance: int = 8,
) -> bool:
    """Return whether *pointer* is inside a display's top-left hot corner."""
    return (
        geometry.left() <= pointer.x() <= geometry.left() + tolerance
        and geometry.top() <= pointer.y() <= geometry.top() + tolerance
    )


def load_development_groups(
    session_provider: Callable[[], Any] = get_session,
) -> list[dict[str, Any]]:
    """Return projects and their visible Development root threads."""
    with session_provider() as db:
        projects = (
            db.query(Project)
            .order_by(Project.position.asc(), Project.name.asc(), Project.id.asc())
            .all()
        )
        work_items = {
            int(item.chat_id): item
            for item in db.query(DevelopmentWorkItem).all()
        }
        chats = (
            db.query(Chat)
            .filter(
                Chat.parent_id.is_(None),
                Chat.is_archived.is_(False),
                Chat.is_hidden.is_(False),
            )
            .order_by(Chat.modified_date.desc(), Chat.id.desc())
            .all()
        )

        groups = [
            {"project_id": int(project.id), "name": project.name or "Untitled", "threads": []}
            for project in projects
        ]
        by_project = {group["project_id"]: group for group in groups}
        unassigned: list[dict[str, Any]] = []

        for chat in chats:
            try:
                params = json.loads(chat.params or "{}")
            except (TypeError, ValueError):
                params = {}
            development = params.get("development")
            item = work_items.get(int(chat.id))
            if item is None and not isinstance(development, dict):
                continue
            development = development if isinstance(development, dict) else {}
            execution = development.get("execution")
            execution = execution if isinstance(execution, dict) else {}
            status = str(execution.get("status") or "idle").strip().lower()
            project_id = item.project_id if item is not None else chat.project_id
            thread = {
                "chat_id": int(chat.id),
                "title": (chat.title or "Development thread").strip() or "Development thread",
                "status": status,
            }
            group = by_project.get(int(project_id)) if project_id is not None else None
            (group["threads"] if group is not None else unassigned).append(thread)

        if unassigned:
            groups.append({"project_id": None, "name": "No Project", "threads": unassigned})
        return groups


def _prefers_reduced_motion() -> bool:
    if platform.system() != "Darwin":
        return False
    try:
        from AppKit import NSWorkspace

        return bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
    except Exception:
        return False


class DevelopmentHotCornerPanel(QtWidgets.QWidget):
    """Slide in a read-only Development selector after a three-second dwell."""

    DWELL_SECONDS = 3.0
    LEAVE_SECONDS = 0.45
    WIDTH = 360
    CORNER_TOLERANCE = 8

    def __init__(
        self,
        *,
        enabled: bool = True,
        session_provider: Callable[[], Any] = get_session,
    ) -> None:
        flags = (
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.WindowDoesNotAcceptFocus
        )
        super().__init__(None, flags)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setObjectName("developmentHotCornerPanel")
        self.setAccessibleName("Development threads")

        self._enabled = bool(enabled)
        self._session_provider = session_provider
        self._corner_screen: QtGui.QScreen | None = None
        self._corner_started_at: float | None = None
        self._open_screen: QtGui.QScreen | None = None
        self._last_inside_at = 0.0
        self._selected_chat_id: int | None = None

        self._build_ui()

        self._slide = QtCore.QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(240)
        self._slide.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self._slide.finished.connect(self._finish_slide)
        self._hiding = False

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)
        if self._enabled:
            self._timer.start()

    @property
    def selected_chat_id(self) -> int | None:
        return self._selected_chat_id

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            #developmentHotCornerPanel {
                background: #08090b;
                border-right: 1px solid #2a2c31;
            }
            QLabel#developmentDrawerTitle {
                color: #ffffff;
                font-size: 20px;
                font-weight: 700;
            }
            QLabel#developmentDrawerHint {
                color: #8f949e;
                font-size: 11px;
            }
            QTreeWidget {
                background: transparent;
                border: 0;
                color: #e7e9ed;
                font-size: 13px;
                outline: 0;
                show-decoration-selected: 1;
            }
            QTreeWidget::item {
                min-height: 30px;
                border-radius: 6px;
                padding: 2px 6px;
            }
            QTreeWidget::item:hover {
                background: #17191d;
            }
            QTreeWidget::item:selected {
                background: #253958;
                color: #ffffff;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #34373d;
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
            """
        )
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 22, 14, 18)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("Development")
        title.setObjectName("developmentDrawerTitle")
        layout.addWidget(title)

        hint = QtWidgets.QLabel("Select a thread")
        hint.setObjectName("developmentDrawerHint")
        layout.addWidget(hint)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(16)
        self.tree.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.itemSelectionChanged.connect(self._remember_selection)
        layout.addWidget(self.tree, 1)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._reset_dwell()
        if self._enabled:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self.hide()

    def _tick(self) -> None:
        if not self._enabled:
            return
        pointer = QtGui.QCursor.pos()
        now = time.monotonic()

        if self.isVisible():
            if self._pointer_inside(pointer) or self._screen_at_hot_corner(pointer) is self._open_screen:
                self._last_inside_at = now
            elif now - self._last_inside_at >= self.LEAVE_SECONDS:
                self._hide_panel()
            return

        screen = self._screen_at_hot_corner(pointer)
        if screen is None:
            self._reset_dwell()
            return
        if screen is not self._corner_screen:
            self._corner_screen = screen
            self._corner_started_at = now
            return
        if self._corner_started_at is not None and now - self._corner_started_at >= self.DWELL_SECONDS:
            self._show_for_screen(screen)
            self._reset_dwell()

    def _screen_at_hot_corner(self, pointer: QtCore.QPoint) -> QtGui.QScreen | None:
        screen = QtGui.QGuiApplication.screenAt(pointer)
        if screen is not None and is_top_left_corner(
            pointer,
            screen.geometry(),
            self.CORNER_TOLERANCE,
        ):
            return screen
        return None

    def _pointer_inside(self, pointer: QtCore.QPoint) -> bool:
        return self.geometry().contains(pointer)

    def _reset_dwell(self) -> None:
        self._corner_screen = None
        self._corner_started_at = None

    def _show_for_screen(self, screen: QtGui.QScreen) -> None:
        self._refresh_threads()
        available = screen.availableGeometry()
        width = min(self.WIDTH, max(280, int(available.width() * 0.34)))
        self.setFixedSize(width, available.height())
        end = QtCore.QPoint(available.left(), available.top())
        start = QtCore.QPoint(available.left() - width, available.top())
        self._open_screen = screen
        self._last_inside_at = time.monotonic()
        self._hiding = False
        self.move(start)
        self.show()
        self._ensure_macos_overlay_level()
        self._animate_position(start, end)

    def _hide_panel(self) -> None:
        if not self.isVisible() or self._hiding:
            return
        self._hiding = True
        end = QtCore.QPoint(self.x() - self.width(), self.y())
        self._animate_position(self.pos(), end)

    def _animate_position(self, start: QtCore.QPoint, end: QtCore.QPoint) -> None:
        self._slide.stop()
        if _prefers_reduced_motion():
            self.move(end)
            self._finish_slide()
            return
        self._slide.setStartValue(start)
        self._slide.setEndValue(end)
        self._slide.start()

    def _finish_slide(self) -> None:
        if self._hiding:
            self._hiding = False
            self._open_screen = None
            self.hide()

    def _refresh_threads(self) -> None:
        selected = self._selected_chat_id
        self.tree.clear()
        try:
            groups = load_development_groups(self._session_provider)
        except Exception as exc:
            logger.warning("Could not load Development hot-corner list: %s", exc)
            groups = []

        for group in groups:
            project_item = QtWidgets.QTreeWidgetItem([str(group["name"])])
            project_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            project_font = project_item.font(0)
            project_font.setBold(True)
            project_item.setFont(0, project_font)
            project_item.setForeground(0, QtGui.QColor("#aeb3bd"))
            self.tree.addTopLevelItem(project_item)
            for thread in group["threads"]:
                active = thread["status"] in {"initializing", "queued", "running", "waiting"}
                prefix = "●" if active else "○"
                child = QtWidgets.QTreeWidgetItem([f"{prefix}  {thread['title']}"])
                child.setData(0, QtCore.Qt.ItemDataRole.UserRole, int(thread["chat_id"]))
                child.setToolTip(0, str(thread["title"]))
                child.setForeground(0, QtGui.QColor("#72a7ff" if active else "#d6d9df"))
                project_item.addChild(child)
                if int(thread["chat_id"]) == selected:
                    self.tree.setCurrentItem(child)
            project_item.setExpanded(True)

        if not groups:
            empty = QtWidgets.QTreeWidgetItem(["No Development projects"])
            empty.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            empty.setForeground(0, QtGui.QColor("#8f949e"))
            self.tree.addTopLevelItem(empty)

    def _remember_selection(self) -> None:
        item = self.tree.currentItem()
        chat_id = item.data(0, QtCore.Qt.ItemDataRole.UserRole) if item else None
        if chat_id is not None:
            self._selected_chat_id = int(chat_id)

    def _ensure_macos_overlay_level(self) -> None:
        if platform.system() != "Darwin" or QtGui.QGuiApplication.platformName() != "cocoa":
            return
        try:
            import objc
            from AppKit import (
                NSStatusWindowLevel,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
                NSWindowCollectionBehaviorStationary,
            )

            native_view = objc.objc_object(c_void_p=int(self.winId()))
            native_window = native_view.window()
            native_window.setLevel_(NSStatusWindowLevel)
            native_window.setHidesOnDeactivate_(False)
            native_window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary
                | NSWindowCollectionBehaviorStationary
            )
        except Exception:
            pass

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._timer.stop()
        self._slide.stop()
        super().closeEvent(event)
