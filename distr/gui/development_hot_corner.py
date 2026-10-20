"""Non-activating Development drawer revealed by a top-left pointer dwell."""

from __future__ import annotations

import json
import logging
import platform
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from PyQt6 import QtCore, QtGui, QtWidgets

from distr.core.db import Chat, get_session
from distr.core.db.projects import Project
from distr.core.kanban.lifecycle import ensure_project_boards
from distr.core.db.time import utc_now_naive
from distr.core.db.workflow import DevelopmentTimeEntry, DevelopmentWorkItem


logger = logging.getLogger(__name__)
PINNED_PROJECTS_SETTING = "development_hot_corner_pinned_project_ids"


def load_pinned_projects() -> list[int]:
    from distr.core.settings import load_settings_from_db

    value = load_settings_from_db().get(PINNED_PROJECTS_SETTING, [])
    if not isinstance(value, list):
        return []
    return sorted({int(project_id) for project_id in value})


def save_pinned_projects(project_ids: list[int]) -> None:
    from distr.core.utils import save_settings_to_db

    save_settings_to_db(
        {PINNED_PROJECTS_SETTING: sorted({int(project_id) for project_id in project_ids})}
    )


def load_development_default_route() -> tuple[str, str]:
    from distr.core.settings import load_settings_from_db

    settings = load_settings_from_db()
    backend = str(settings.get("project_cli_medium_backend") or "codex").strip()
    return (
        "Kilo" if backend.lower() in {"kilo", "kilocode", "ki" + "ro"} else backend,
        str(settings.get("project_cli_medium_model") or "auto").strip(),
    )


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
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return projects and their visible Development root threads."""
    now = now or utc_now_naive()
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
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
        entries_by_chat: dict[int, list[DevelopmentTimeEntry]] = {}
        entries = (
            db.query(DevelopmentTimeEntry)
            .filter(DevelopmentTimeEntry.ended_at >= week_start)
            .all()
        )
        for entry in entries:
            entries_by_chat.setdefault(int(entry.chat_id), []).append(entry)
        boards_by_project = ensure_project_boards(db)
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

        groups = []
        for project in projects:
            board = boards_by_project.get(int(project.id))
            provider = (
                "decisions"
                if board is not None and board.source in {None, "database", "decisions", "local"}
                else str(getattr(board, "source", "") or "")
            )
            board_identity = (
                int(board.id)
                if provider == "decisions" and board is not None
                else getattr(board, "external_board_id", None)
            )
            groups.append(
                {
                    "project_id": int(project.id),
                    "board_key": f"{provider}:{board_identity}" if provider and board_identity else None,
                    "name": project.name or "Untitled",
                    "weekly_seconds": 0,
                    "threads": [],
                }
            )
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
            elapsed_seconds = (
                max(0, int(item.time_accumulated_seconds or 0)) if item is not None else 0
            )
            time_running = bool(item is not None and not item.time_paused and item.time_started_at)
            if time_running:
                elapsed_seconds += max(0, int((now - item.time_started_at).total_seconds()))
            weekly_seconds = weekly_entry_seconds(entries_by_chat.get(int(chat.id), []))
            if time_running:
                weekly_seconds += max(
                    0,
                    int((now - max(item.time_started_at, week_start)).total_seconds()),
                )
            thread = {
                "chat_id": int(chat.id),
                "title": (chat.title or "Development thread").strip() or "Development thread",
                "modified_at": chat.modified_date.timestamp() if chat.modified_date else 0,
                "source_type": str(development.get("source_type") or ""),
                "status": status,
                "active": status in {"initializing", "queued", "running", "waiting"},
                "elapsed_seconds": elapsed_seconds,
                "weekly_seconds": weekly_seconds,
                "time_running": time_running,
            }
            group = by_project.get(int(project_id)) if project_id is not None else None
            if group is not None:
                group["threads"].append(thread)
                group["weekly_seconds"] += weekly_seconds
            else:
                unassigned.append(thread)

        if unassigned:
            groups.append(
                {
                    "project_id": None,
                    "board_key": None,
                    "name": "No Project",
                    "weekly_seconds": sum(int(thread["weekly_seconds"]) for thread in unassigned),
                    "threads": unassigned,
                }
            )
        return groups


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def recent_development_threads(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    threads = [
        thread
        for group in groups
        for thread in group["threads"]
        if thread.get("source_type") != "automation"
    ]
    return sorted(
        threads,
        key=lambda thread: (
            bool(thread.get("active")),
            float(thread.get("modified_at") or 0),
            int(thread["chat_id"]),
        ),
        reverse=True,
    )


def weekly_entry_seconds(entries: list[DevelopmentTimeEntry]) -> int:
    reset_id = max(
        (int(entry.id) for entry in entries if entry.source == "reset"),
        default=0,
    )
    return sum(
        max(0, int(entry.seconds or 0))
        for entry in entries
        if int(entry.id) > reset_id
    )


def _prefers_reduced_motion() -> bool:
    if platform.system() != "Darwin":
        return False
    try:
        from AppKit import NSWorkspace

        return bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
    except Exception:
        return False


class ThreadTitleWidget(QtWidgets.QWidget):
    """Compact thread title with overflow marquee and active-work spinner."""

    SPINNER_SIZE = 9
    SPINNER_GAP = 5
    MARQUEE_GAP = 28
    MARQUEE_PIXELS_PER_SECOND = 28

    def __init__(
        self,
        *,
        title: str,
        active: bool,
        tree: QtWidgets.QTreeWidget,
        item: QtWidgets.QTreeWidgetItem,
    ) -> None:
        super().__init__(tree.viewport())
        self._title = title
        self._active = bool(active)
        self._tree = tree
        self._item = item
        self._motion_reduced = _prefers_reduced_motion()
        self._animation_started_at = time.monotonic()
        self._animation_now = self._animation_started_at
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.setFixedHeight(24)
        self.setAccessibleName(title)

    @property
    def title(self) -> str:
        return self._title

    @property
    def active(self) -> bool:
        return self._active

    def set_thread_state(self, *, title: str, active: bool) -> None:
        if title != self._title:
            self._title = title
            self._animation_started_at = time.monotonic()
            self.setAccessibleName(title)
        self._active = bool(active)
        self.update()

    def advance(self, now: float) -> None:
        self._animation_now = now
        self.update()

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        selected = self._tree.currentItem() is self._item
        foreground = QtGui.QColor(
            "#ffffff" if selected else "#72a7ff" if self._active else "#d6d9df"
        )
        text_left = 0
        if self._active:
            spinner_top = (self.height() - self.SPINNER_SIZE) // 2
            spinner_rect = QtCore.QRectF(
                0.5,
                spinner_top + 0.5,
                self.SPINNER_SIZE - 1,
                self.SPINNER_SIZE - 1,
            )
            pen = QtGui.QPen(foreground, 1.5)
            pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            start_angle = 90 if self._motion_reduced else int(self._animation_now * 300) % 360
            painter.drawArc(spinner_rect, start_angle * 16, 250 * 16)
            text_left = self.SPINNER_SIZE + self.SPINNER_GAP

        text_rect = QtCore.QRectF(
            text_left,
            0,
            max(0, self.width() - text_left),
            self.height(),
        )
        painter.setPen(foreground)
        metrics = painter.fontMetrics()
        title_width = metrics.horizontalAdvance(self._title)
        if title_width <= text_rect.width():
            painter.drawText(
                text_rect,
                QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
                self._title,
            )
            return
        if self._motion_reduced:
            painter.drawText(
                text_rect,
                QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
                metrics.elidedText(
                    self._title,
                    QtCore.Qt.TextElideMode.ElideRight,
                    int(text_rect.width()),
                ),
            )
            return

        painter.save()
        painter.setClipRect(text_rect)
        loop_width = title_width + self.MARQUEE_GAP
        offset = int(
            max(0.0, self._animation_now - self._animation_started_at)
            * self.MARQUEE_PIXELS_PER_SECOND
        ) % loop_width
        baseline = (self.height() + metrics.ascent() - metrics.descent()) / 2
        first_x = text_rect.left() - offset
        painter.drawText(QtCore.QPointF(first_x, baseline), self._title)
        painter.drawText(QtCore.QPointF(first_x + loop_width, baseline), self._title)
        painter.restore()


class DevelopmentHotCornerPanel(QtWidgets.QWidget):
    """Slide in a compact Development board selector after a short dwell."""

    DWELL_SECONDS = 2.5
    LEAVE_SECONDS = 0.45
    WIDTH = 360
    CORNER_TOLERANCE = 8

    def __init__(
        self,
        *,
        enabled: bool = True,
        session_provider: Callable[[], Any] = get_session,
        open_path: Callable[[str], None] | None = None,
        thread_factory: Callable[..., int] | None = None,
        time_updater: Callable[[int, str], Any] | None = None,
        pinned_project_ids: list[int] | None = None,
        pin_saver: Callable[[list[int]], None] = save_pinned_projects,
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
        self.setAccessibleName("Development boards")

        self._enabled = bool(enabled)
        self._session_provider = session_provider
        self._open_path = open_path or (lambda _path: None)
        self._thread_factory = thread_factory
        self._time_updater = time_updater
        resolved_pins = load_pinned_projects() if pinned_project_ids is None else pinned_project_ids
        self._pinned_project_ids = {int(project_id) for project_id in resolved_pins}
        self._pin_saver = pin_saver
        self._corner_screen: QtGui.QScreen | None = None
        self._corner_started_at: float | None = None
        self._open_screen: QtGui.QScreen | None = None
        self._last_inside_at = 0.0
        self._selected_chat_id: int | None = None
        self._selected_item_key: tuple[str, int | None] | None = None
        self._thread_rows: list[tuple[QtWidgets.QTreeWidgetItem, dict[str, Any]]] = []
        self._thread_title_widgets: list[
            tuple[QtWidgets.QTreeWidgetItem, dict[str, Any], ThreadTitleWidget]
        ] = []
        self._project_rows: list[tuple[QtWidgets.QTreeWidgetItem, dict[str, Any]]] = []
        self._section_rows: dict[str, QtWidgets.QTreeWidgetItem] = {}
        self._section_expanded = {"recent": False, "projects": True}
        self._project_expanded: dict[int | None, bool] = {}
        self._context_menu: QtWidgets.QMenu | None = None
        self._rendered_at = time.monotonic()
        self._last_live_sync = self._rendered_at

        self._build_ui()

        self._slide = QtCore.QPropertyAnimation(self, b"geometry", self)
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
            QToolButton#developmentDrawerAction {
                min-width: 34px;
                min-height: 30px;
                border: 1px solid #2a2c31;
                border-radius: 6px;
                background: #111317;
                color: #d6d9df;
                font-size: 14px;
                font-weight: 600;
            }
            QToolButton#developmentDrawerAction:hover {
                background: #1b1e23;
                border-color: #4b5563;
                color: #ffffff;
            }
            QToolButton#developmentDrawerFooterAction {
                min-width: 26px;
                max-width: 26px;
                min-height: 26px;
                max-height: 26px;
                border: 0;
                border-radius: 5px;
                background: transparent;
                color: #8f949e;
                font-size: 12px;
            }
            QToolButton#developmentDrawerFooterAction:hover {
                background: #1b1e23;
                color: #ffffff;
            }
            QFrame#developmentDrawerFooter {
                border: 0;
            }
            QFrame#developmentDrawerDefaultRoute {
                border-top: 1px solid #2a2c31;
            }
            QLabel#developmentDrawerDefaultCaption {
                color: #8f949e;
                font-size: 10px;
            }
            QLabel#developmentDrawerDefaultRouteText {
                color: #d6d9df;
                font-size: 11px;
                font-weight: 600;
            }
            QToolButton#developmentDrawerDefaultRouteEdit {
                min-width: 26px;
                max-width: 26px;
                min-height: 26px;
                max-height: 26px;
                border: 0;
                border-radius: 5px;
                background: transparent;
                color: #8f949e;
                font-size: 14px;
            }
            QToolButton#developmentDrawerDefaultRouteEdit:hover {
                background: #1b1e23;
                color: #ffffff;
            }
            QLabel#developmentDrawerFooterTime {
                color: #d6d9df;
                font-size: 11px;
                font-weight: 600;
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
                min-height: 24px;
                border-radius: 6px;
                padding: 0 4px;
            }
            QTreeWidget::item:hover {
                background: #17191d;
            }
            QTreeWidget::item:selected {
                background: #253958;
                color: #ffffff;
                border-radius: 0;
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
        layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetNoConstraint)
        layout.setContentsMargins(20, 22, 8, 18)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(6)
        title = QtWidgets.QLabel("Boards")
        title.setObjectName("developmentDrawerTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.quick_actions: dict[str, QtWidgets.QToolButton] = {}
        new_thread = QtWidgets.QToolButton()
        new_thread.setObjectName("developmentDrawerAction")
        new_thread.setText("+")
        new_thread.setToolTip("New thread")
        new_thread.setAccessibleName("New thread")
        new_thread.clicked.connect(lambda: self._open_path("/development/new/"))
        header.addWidget(new_thread)
        self.quick_actions["New thread"] = new_thread
        layout.addLayout(header)

        footer_actions = []
        for label, symbol, path in (
            ("Planning", "≡", "/development/plan/"),
            ("Incoming", "↓", "/development/incoming/"),
            ("Scheduled", "◷", "/development/automations/"),
            ("Workflows", "⎇", "/development/workflows/"),
            ("Terminals", ">_", "/development/terminals/"),
            ("Reports", "▥", "/development/reports/"),
        ):
            button = QtWidgets.QToolButton()
            button.setObjectName("developmentDrawerFooterAction")
            button.setText(symbol)
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.clicked.connect(lambda _checked=False, target=path: self._open_path(target))
            footer_actions.append(button)
            self.quick_actions[label] = button

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["", "", "", ""])
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            self.tree.header().setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().hide()
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(8)
        self.tree.setWordWrap(False)
        self.tree.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setTextElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self.tree.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.itemSelectionChanged.connect(self._remember_selection)
        self.tree.itemClicked.connect(self._handle_item_click)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.tree, 1)

        default_route = QtWidgets.QFrame()
        default_route.setObjectName("developmentDrawerDefaultRoute")
        default_route_layout = QtWidgets.QHBoxLayout(default_route)
        default_route_layout.setContentsMargins(2, 9, 0, 0)
        default_route_layout.setSpacing(6)
        default_caption = QtWidgets.QLabel("Auto route")
        default_caption.setObjectName("developmentDrawerDefaultCaption")
        default_route_layout.addWidget(default_caption)
        self.default_route = QtWidgets.QLabel("codex / auto")
        self.default_route.setObjectName("developmentDrawerDefaultRouteText")
        self.default_route.setToolTip("Default medium-complexity Development harness and model")
        default_route_layout.addWidget(self.default_route)
        default_route_layout.addStretch(1)
        edit_default = QtWidgets.QToolButton()
        edit_default.setObjectName("developmentDrawerDefaultRouteEdit")
        edit_default.setText("⚙")
        edit_default.setToolTip("Edit automatic Development routing")
        edit_default.setAccessibleName("Edit automatic Development routing")
        edit_default.clicked.connect(lambda: self._open_path("/settings?llm=workflow#llms"))
        default_route_layout.addWidget(edit_default)
        layout.addWidget(default_route)
        self._refresh_default_route()

        footer = QtWidgets.QFrame()
        footer.setObjectName("developmentDrawerFooter")
        footer_layout = QtWidgets.QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 10, 0, 0)
        footer_layout.setSpacing(3)
        for button in footer_actions:
            footer_layout.addWidget(button)
        footer_layout.addStretch(1)
        self.footer = QtWidgets.QLabel("Total  00:00:00")
        self.footer.setObjectName("developmentDrawerFooterTime")
        self.footer.setToolTip("This week")
        self.footer.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        footer_layout.addWidget(self.footer, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(footer)

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
            self._refresh_thread_title_widgets(now)
            if now - self._last_live_sync >= 1:
                self._sync_live_state(now)
            else:
                self._refresh_elapsed_labels(now)
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
        width = min(self.WIDTH, available.width())
        end = QtCore.QRect(available.left(), available.top(), width, available.height())
        start = QtCore.QRect(available.left(), available.top(), 1, available.height())
        self._open_screen = screen
        self._last_inside_at = time.monotonic()
        self._hiding = False
        self.setGeometry(start)
        self.show()
        self._ensure_macos_overlay_level()
        self._animate_geometry(start, end)

    def _hide_panel(self) -> None:
        if not self.isVisible() or self._hiding:
            return
        self._hiding = True
        end = QtCore.QRect(self.x(), self.y(), 1, self.height())
        self._animate_geometry(self.geometry(), end)

    def _animate_geometry(self, start: QtCore.QRect, end: QtCore.QRect) -> None:
        self._slide.stop()
        if _prefers_reduced_motion():
            self.setGeometry(end)
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
        selected_key = self._selected_item_key
        for key, item in self._section_rows.items():
            if key in self._section_expanded:
                self._section_expanded[key] = item.isExpanded()
        for item, group in self._project_rows:
            self._project_expanded[group["project_id"]] = item.isExpanded()
        self.tree.clear()
        self._thread_rows.clear()
        self._thread_title_widgets.clear()
        self._project_rows.clear()
        self._section_rows.clear()
        try:
            groups = load_development_groups(self._session_provider)
        except Exception as exc:
            logger.warning("Could not load Development hot-corner list: %s", exc)
            groups = []
        pinned_groups = [group for group in groups if group["project_id"] in self._pinned_project_ids]
        regular_groups = [group for group in groups if group not in pinned_groups]
        recent_threads = recent_development_threads(groups)

        for section_key, section_title, section_groups in (
            ("pinned", "Pinned", pinned_groups),
            ("projects", "Projects", regular_groups),
            ("recent", "Recent", recent_threads),
        ):
            if not section_groups and section_key != "pinned":
                continue
            section = QtWidgets.QTreeWidgetItem([section_title])
            section.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            section.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"kind": "section", "key": section_key})
            section_font = section.font(0)
            section_font.setBold(True)
            section.setFont(0, section_font)
            section.setForeground(0, QtGui.QColor("#8f949e"))
            self.tree.addTopLevelItem(section)
            if section_key == "pinned":
                section.setChildIndicatorPolicy(QtWidgets.QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicator)
            else:
                section.setExpanded(self._section_expanded[section_key])
            self._section_rows[section_key] = section

            if section_key == "recent":
                for thread in recent_threads[:5]:
                    self._add_recent_row(section, thread, selected_key)
                if len(recent_threads) > 5:
                    more = QtWidgets.QTreeWidgetItem(["Show more"])
                    more.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"kind": "recent_more"})
                    more.setForeground(0, QtGui.QColor("#8f949e"))
                    section.addChild(more)
            else:
                for group in section_groups:
                    self._add_project_row(None if section_key == "pinned" else section, group, selected_key)

        self._rendered_at = time.monotonic()
        self._refresh_elapsed_labels(self._rendered_at)

        if not groups:
            empty = QtWidgets.QTreeWidgetItem(["No Development projects"])
            empty.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            empty.setForeground(0, QtGui.QColor("#8f949e"))
            self.tree.addTopLevelItem(empty)

    def _add_recent_row(
        self,
        section: QtWidgets.QTreeWidgetItem,
        thread: dict[str, Any],
        selected_key: tuple[str, int | None] | None,
    ) -> None:
        item = QtWidgets.QTreeWidgetItem([
            "",
            format_duration(thread["elapsed_seconds"]),
            "",
            "↗",
        ])
        item.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"kind": "thread", **thread})
        item.setData(0, QtCore.Qt.ItemDataRole.AccessibleTextRole, str(thread["title"]))
        item.setToolTip(0, str(thread["title"]))
        item.setToolTip(1, "Thread time")
        item.setToolTip(3, "Open chat in the web UI")
        item.setSizeHint(0, QtCore.QSize(125, 24))
        section.addChild(item)
        self._thread_rows.append((item, thread))
        self._install_thread_title(item, thread)
        if selected_key == ("thread", int(thread["chat_id"])):
            self.tree.setCurrentItem(item)

    def _add_project_row(
        self,
        section: QtWidgets.QTreeWidgetItem | None,
        group: dict[str, Any],
        selected_key: tuple[str, int | None] | None,
    ) -> None:
        project_item = QtWidgets.QTreeWidgetItem([
            str(group["name"]),
            format_duration(group["weekly_seconds"]),
            "+" if group["project_id"] is not None else "",
            "↗" if group["board_key"] else "",
        ])
        project_item.setFlags(
            QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
        )
        project_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"kind": "project", **group})
        project_font = project_item.font(0)
        project_font.setBold(True)
        project_item.setFont(0, project_font)
        project_item.setSizeHint(0, self._project_title_size(str(group["name"]), project_font))
        project_item.setForeground(0, QtGui.QColor("#aeb3bd"))
        project_item.setToolTip(1, "Total development time this week")
        project_item.setToolTip(2, f"Create a new thread in {group['name']}")
        project_item.setToolTip(3, f"Open {group['name']} in the web UI")
        if section is None:
            self.tree.addTopLevelItem(project_item)
        else:
            section.addChild(project_item)
        self._project_rows.append((project_item, group))
        if selected_key == ("project", group["project_id"]):
            self.tree.setCurrentItem(project_item)
        for thread in group["threads"]:
            child = QtWidgets.QTreeWidgetItem([
                "",
                format_duration(thread["elapsed_seconds"]),
                "",
                "↗",
            ])
            child.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"kind": "thread", **thread})
            child.setData(0, QtCore.Qt.ItemDataRole.AccessibleTextRole, str(thread["title"]))
            child.setToolTip(0, str(thread["title"]))
            child.setToolTip(1, "Thread time")
            child.setToolTip(3, "Open chat in the web UI")
            child.setSizeHint(0, QtCore.QSize(125, 24))
            project_item.addChild(child)
            self._thread_rows.append((child, thread))
            self._install_thread_title(child, thread)
            if selected_key == ("thread", int(thread["chat_id"])):
                self.tree.setCurrentItem(child)
        project_item.setExpanded(
            self._project_expanded.get(
                group["project_id"],
                any(thread["active"] for thread in group["threads"]),
            )
        )

    def _install_thread_title(
        self,
        item: QtWidgets.QTreeWidgetItem,
        thread: dict[str, Any],
    ) -> None:
        widget = ThreadTitleWidget(
            title=str(thread["title"]),
            active=bool(thread["active"]),
            tree=self.tree,
            item=item,
        )
        self.tree.setItemWidget(item, 0, widget)
        self._thread_title_widgets.append((item, thread, widget))

    def _refresh_thread_title_widgets(self, now: float) -> None:
        for _item, thread, widget in self._thread_title_widgets:
            widget.set_thread_state(
                title=str(thread["title"]),
                active=bool(thread["active"]),
            )
            widget.advance(now)

    def _project_title_size(
        self,
        title: str,
        font: QtGui.QFont,
        title_width: int = 150,
    ) -> QtCore.QSize:
        bounds = QtGui.QFontMetrics(font).boundingRect(
            QtCore.QRect(0, 0, title_width, 1000),
            QtCore.Qt.TextFlag.TextWordWrap,
            title,
        )
        return QtCore.QSize(title_width, max(30, bounds.height() + 8))

    def _refresh_elapsed_labels(self, now: float) -> None:
        delta = max(0, int(now - self._rendered_at))
        for item, thread in self._thread_rows:
            seconds = int(thread["elapsed_seconds"]) + (
                delta if thread["time_running"] else 0
            )
            item.setText(1, format_duration(seconds))
        total = 0
        for item, group in self._project_rows:
            running = sum(1 for thread in group["threads"] if thread["time_running"])
            weekly = int(group["weekly_seconds"]) + delta * running
            item.setText(1, format_duration(weekly))
            total += weekly
        self.footer.setText(f"Total  {format_duration(total)}")

    def _refresh_default_route(self) -> None:
        try:
            provider, model = load_development_default_route()
        except Exception as exc:
            logger.warning("Could not load default Development route: %s", exc)
            return
        self.default_route.setText(f"{provider} / {model}")

    def _sync_live_state(self, now: float) -> None:
        """Update shared timer state without rebuilding or expanding the tree."""
        self._last_live_sync = now
        self._refresh_default_route()
        try:
            fresh_groups = load_development_groups(self._session_provider)
        except Exception as exc:
            logger.warning("Could not sync Development hot-corner timers: %s", exc)
            return
        fresh_by_project = {group["project_id"]: group for group in fresh_groups}
        fresh_threads = {
            int(thread["chat_id"]): thread
            for group in fresh_groups
            for thread in group["threads"]
        }
        for item, group in self._project_rows:
            fresh_group = fresh_by_project.get(group["project_id"])
            if fresh_group is None:
                continue
            group["weekly_seconds"] = fresh_group["weekly_seconds"]
            item_value = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if isinstance(item_value, dict):
                item_value["weekly_seconds"] = fresh_group["weekly_seconds"]
                item.setData(0, QtCore.Qt.ItemDataRole.UserRole, item_value)
        for item, thread in self._thread_rows:
            fresh_thread = fresh_threads.get(int(thread["chat_id"]))
            if fresh_thread is not None:
                thread.update(fresh_thread)
                value = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if isinstance(value, dict):
                    value.update(fresh_thread)
                    item.setData(0, QtCore.Qt.ItemDataRole.UserRole, value)
        self._refresh_recent_rows(fresh_groups)
        self._rendered_at = now
        self._refresh_elapsed_labels(now)
        self._refresh_thread_title_widgets(now)

    def _refresh_recent_rows(self, groups: list[dict[str, Any]]) -> None:
        section = self._section_rows.get("recent")
        if section is None:
            return
        self._thread_rows = [
            (item, thread)
            for item, thread in self._thread_rows
            if item.parent() is not section
        ]
        self._thread_title_widgets = [
            (item, thread, widget)
            for item, thread, widget in self._thread_title_widgets
            if item.parent() is not section
        ]
        section.takeChildren()
        recent_threads = recent_development_threads(groups)
        for thread in recent_threads[:5]:
            self._add_recent_row(section, thread, self._selected_item_key)
        if len(recent_threads) > 5:
            more = QtWidgets.QTreeWidgetItem(["Show more"])
            more.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"kind": "recent_more"})
            more.setForeground(0, QtGui.QColor("#8f949e"))
            section.addChild(more)

    def _handle_item_click(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        value = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(value, dict):
            return
        if value.get("kind") == "section":
            section_key = str(value.get("key") or "")
            if section_key in self._section_expanded:
                expanded = not item.isExpanded()
                item.setExpanded(expanded)
                self._section_expanded[section_key] = expanded
            return
        if value.get("kind") == "recent_more":
            self._open_path("/development/?recent=1")
            return
        if value.get("kind") == "project":
            if column == 2 and value.get("project_id") is not None:
                self._create_project_thread(value)
            elif column == 3 and value.get("board_key"):
                provider, identity = str(value["board_key"]).split(":", 1)
                self._open_path(f"/development/boards/{provider}/{identity}/")
        elif value.get("kind") == "thread":
            chat_id = int(value["chat_id"])
            if column in {0, 3}:
                self._open_path(f"/chat/?id={chat_id}")

    def _show_context_menu(self, position: QtCore.QPoint) -> None:
        item = self.tree.itemAt(position)
        value = item.data(0, QtCore.Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(value, dict) or value.get("kind") not in {"project", "thread"}:
            return
        menu = QtWidgets.QMenu(self)
        if value["kind"] == "project" and value.get("project_id") is not None:
            project_id = int(value["project_id"])
            pin = menu.addAction("Unpin project" if project_id in self._pinned_project_ids else "Pin project")
            pin.triggered.connect(lambda: self._toggle_project_pin(project_id))
        elif value["kind"] == "thread":
            chat_id = int(value["chat_id"])
            timer_action = "pause" if value["time_running"] else "play"
            timer = menu.addAction("Pause timer" if value["time_running"] else "Start timer")
            timer.triggered.connect(lambda: self._update_thread_time(chat_id, timer_action))
            reset = menu.addAction("Reset timer")
            reset.triggered.connect(lambda: self._update_thread_time(chat_id, "reset"))
        self._context_menu = menu
        menu.popup(self.tree.viewport().mapToGlobal(position))

    def _update_thread_time(self, chat_id: int, action: str) -> None:
        try:
            if self._time_updater is not None:
                self._time_updater(chat_id, action)
            else:
                from distr.core.workflow.development_control import (
                    pause_thread_time,
                    reset_thread_time,
                    resume_thread_time,
                )

                {"play": resume_thread_time, "pause": pause_thread_time, "reset": reset_thread_time}[action](chat_id)
            self._refresh_threads()
        except Exception as exc:
            logger.warning("Could not %s Development timer for chat %s: %s", action, chat_id, exc)

    def _toggle_project_pin(self, project_id: int) -> None:
        if project_id in self._pinned_project_ids:
            self._pinned_project_ids.remove(project_id)
        else:
            self._pinned_project_ids.add(project_id)
        self._pin_saver(sorted(self._pinned_project_ids))
        self._refresh_threads()

    def _create_project_thread(self, group: dict[str, Any]) -> None:
        try:
            if self._thread_factory is None:
                from distr.core.workflow.development_threads import ensure_development_thread

                self._thread_factory = ensure_development_thread
            board_key = str(group.get("board_key") or "") or None
            provider = board_key.split(":", 1)[0] if board_key else None
            chat_id = self._thread_factory(
                title="New Development thread",
                project_id=int(group["project_id"]),
                source_type="prompt",
                board_key=board_key,
                board_provider=provider,
            )
            self._selected_chat_id = int(chat_id)
            self._selected_item_key = ("thread", int(chat_id))
            self._refresh_threads()
            self._open_path(f"/chat/?id={int(chat_id)}")
        except Exception as exc:
            logger.warning("Could not create Development thread from hot corner: %s", exc)

    def _remember_selection(self) -> None:
        item = self.tree.currentItem()
        value = item.data(0, QtCore.Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(value, dict):
            return
        if value.get("kind") == "thread":
            self._selected_chat_id = int(value["chat_id"])
            self._selected_item_key = ("thread", self._selected_chat_id)
        elif value.get("kind") == "project":
            self._selected_chat_id = None
            self._selected_item_key = ("project", value.get("project_id"))
        for _item, _thread, widget in self._thread_title_widgets:
            widget.update()

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
