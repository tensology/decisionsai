"""Non-activating Development drawer revealed by a top-left pointer dwell."""

from __future__ import annotations

import json
import logging
import platform
import time
import threading
from concurrent.futures import Future
from datetime import datetime, timedelta
from typing import Any, Callable

from PyQt6 import QtCore, QtGui, QtWidgets

from distr.core.db import Chat, get_session
from distr.core.db.projects import Project
from distr.core.kanban.lifecycle import ensure_project_boards
from distr.core.db.time import utc_now_naive
from distr.core.db.workflow import AutoWorkflowRun, DevelopmentTimeEntry, DevelopmentWorkItem


logger = logging.getLogger(__name__)
PINNED_PROJECTS_SETTING = "development_hot_corner_pinned_project_ids"


def load_desktop_proposals():
    from distr.core.incoming.proposals import list_incoming_proposals

    # Opening desktop navigation must never launch a local triage model.
    return list_incoming_proposals(limit=100, refresh_local=False)


def start_desktop_proposal(proposal):
    from distr.core.tensology_client import _local_api_post

    # One application-owned endpoint handles both native and browser actions.
    result = _local_api_post('workflows/studio/incoming/proposals/thread', {'proposal': proposal})
    if not isinstance(result, dict) or not result.get('chat_id'):
        raise ValueError('The proposal thread could not be started. Retry from Proposals.')
    return int(result['chat_id'])


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


def project_folder_icon(has_threads: bool) -> QtGui.QIcon:
    icon = QtGui.QIcon()
    for opened in (False, True):
        pixmap = QtGui.QPixmap(24, 24)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        color = QtGui.QColor("#dce0e5" if has_threads else "#626770")
        painter.setPen(QtGui.QPen(color, 1.5))
        painter.setBrush(QtGui.QColor("#292e35" if has_threads else "#17191d"))
        painter.drawPolygon(QtGui.QPolygonF([
            QtCore.QPointF(x, y) for x, y in
            [(3, 6), (10, 6), (12, 9), (21, 9), (21, 20), (3, 20)]
        ]))
        if opened and has_threads:
            painter.setBrush(color)
            painter.drawPolygon(QtGui.QPolygonF([
                QtCore.QPointF(x, y) for x, y in [(2, 13), (23, 13), (19, 21), (4, 21)]
            ]))
        else:
            painter.drawLine(3, 11, 21, 11)
        painter.end()
        icon.addPixmap(pixmap, QtGui.QIcon.Mode.Normal,
                       QtGui.QIcon.State.On if opened else QtGui.QIcon.State.Off)
    return icon


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
        # A chat keeps a cached execution status. It goes stale when a run ends
        # without updating it, which left a spinner running forever. Only a chat
        # with a genuinely live run may show activity.
        live_chat_ids = {
            int(row[0])
            for row in db.query(AutoWorkflowRun.chat_id)
            .filter(
                AutoWorkflowRun.chat_id.is_not(None),
                AutoWorkflowRun.status.in_(
                    ("initializing", "queued", "running", "waiting")
                ),
            )
            .all()
            if row[0] is not None
        }

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
                "active": (
                    status in {"initializing", "queued", "running", "waiting"}
                    and int(chat.id) in live_chat_ids
                ),
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
        activity_only: bool = False,
    ) -> None:
        super().__init__(tree.viewport())
        self._title = title
        self._active = bool(active)
        self._tree = tree
        self._item = item
        self._activity_only = activity_only
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
        text_left = self._tree.iconSize().width() + 9
        parent = self._item.parent()
        value = parent.data(0, QtCore.Qt.ItemDataRole.UserRole) if parent else None
        if isinstance(value, dict) and value.get("kind") == "project":
            index = self._tree.indexFromItem(parent)
            option = QtWidgets.QStyleOptionViewItem()
            option.initFrom(self._tree)
            self._tree.itemDelegate().initStyleOption(option, index)
            option.decorationSize = self._tree.iconSize()
            option.rect = self._tree.visualRect(index)
            text_bounds = self._tree.style().subElementRect(
                QtWidgets.QStyle.SubElement.SE_ItemViewItemText, option, self._tree)
            text_margin = self._tree.style().pixelMetric(
                QtWidgets.QStyle.PixelMetric.PM_FocusFrameHMargin, option, self._tree) + 1
            text_left = self.mapFrom(self._tree.viewport(), text_bounds.topLeft()).x() + text_margin
        if self._activity_only and self._active:
            spinner_top = (self.height() - self.SPINNER_SIZE) // 2
            spinner_rect = QtCore.QRectF(
                (self.width() - self.SPINNER_SIZE) / 2 + 0.5,
                spinner_top + 0.5,
                self.SPINNER_SIZE - 1,
                self.SPINNER_SIZE - 1,
            )
            pen = QtGui.QPen(foreground, 1.5)
            pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            start_angle = 90 if self._motion_reduced else int(self._animation_now * 300) % 360
            painter.drawArc(spinner_rect, start_angle * 16, 250 * 16)
        if self._activity_only:
            return

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


class DevelopmentProjectTree(QtWidgets.QTreeWidget):
    toggleRequested = QtCore.pyqtSignal(QtCore.QModelIndex)
    threadOpenRequested = QtCore.pyqtSignal(QtCore.QModelIndex)

    def _project_at(self, event):
        item = self.itemAt(event.position().toPoint())
        value = item.data(0, QtCore.Qt.ItemDataRole.UserRole) if item else None
        return item if isinstance(value, dict) and value.get("kind") == "project" else None

    def _on_folder(self, item, event):
        rect = self.visualItemRect(item)
        return rect.left() + 8 <= event.position().x() < rect.left() + 8 + self.iconSize().width()

    def mouseReleaseEvent(self, event):
        if getattr(self, "_ignore_folder_release", False):
            self._ignore_folder_release = False
            return
        item = self._project_at(event)
        if event.button() == QtCore.Qt.MouseButton.LeftButton and item and self._on_folder(item, event):
            self.toggleRequested.emit(self.indexFromItem(item))
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        item = self._project_at(event)
        if item and event.button() == QtCore.Qt.MouseButton.LeftButton:
            if item.childCount() == 0:
                return
            if self._on_folder(item, event):
                self._ignore_folder_release = True
            elif (self.columnAt(int(event.position().x())) == 0
                  and event.position().x() >= self.visualItemRect(item).left() + self.iconSize().width() + 12):
                self.toggleRequested.emit(self.indexFromItem(item))
            return
        selected = self.itemAt(event.position().toPoint())
        value = selected.data(0, QtCore.Qt.ItemDataRole.UserRole) if selected else None
        if (event.button() == QtCore.Qt.MouseButton.LeftButton and isinstance(value, dict)
                and value.get("kind") == "thread" and self.columnAt(int(event.position().x())) == 0):
            self.threadOpenRequested.emit(self.indexFromItem(selected))
            return
        super().mouseDoubleClickEvent(event)


class DevelopmentHotCornerPanel(QtWidgets.QWidget):
    """Slide in a compact Development board selector after a short dwell."""

    DWELL_SECONDS = 2.5
    LEAVE_SECONDS = 0.45
    WIDTH = 400
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
        self._uses_shared_pins = pinned_project_ids is None and pin_saver is save_pinned_projects
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
        self._view_settings = QtCore.QSettings("Tensology", "DecisionsAI")
        self._section_expanded = {
            "recent": False,
            "projects": self._view_settings.value("sidebar/projectsExpanded", True, type=bool),
            "proposals": self._view_settings.value("sidebar/proposalsExpanded", False, type=bool),
        }
        self._proposals = []
        self._proposal_error = ''
        self._proposal_loading = False
        self._proposal_busy = set()
        self._proposal_last_refresh = 0.0
        self._proposal_change_version = 0
        self._proposal_tasks = {}
        self._project_expanded: dict[int | None, bool] = {}
        self._context_menu: QtWidgets.QMenu | None = None
        self._rendered_at = time.monotonic()
        self._last_live_sync = self._rendered_at

        self._build_ui()
        self._proposal_task_timer = QtCore.QTimer(self)
        self._proposal_task_timer.setInterval(50)
        self._proposal_task_timer.timeout.connect(self._poll_proposal_tasks)

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
            QToolButton#developmentDrawerNewThread {
                min-width: 34px; max-width: 34px;
                min-height: 34px; max-height: 34px;
                border: 0; border-radius: 17px;
                background: #ffffff; color: #08090b;
                font-size: 22px; font-weight: 500;
            }
            QToolButton#developmentDrawerPanelPin {
                min-width: 20px; max-width: 20px; min-height: 34px;
                border: 0; background: transparent;
            }
            QToolButton#developmentDrawerFooterAction {
                min-width: 30px;
                max-width: 30px;
                min-height: 30px;
                max-height: 30px;
                border: 0;
                border-radius: 5px;
                background: transparent;
                color: #8f949e;
                font-size: 16px;
            }
            QToolButton#developmentDrawerFooterAction:hover {
                background: #1b1e23;
                color: #ffffff;
            }
            QFrame#developmentDrawerFooter {
                border: 0;
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
                border-radius: 0;
                padding: 0 8px;
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
        layout.setContentsMargins(8, 22, 12, 18)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(6)
        title = QtWidgets.QLabel("Boards")
        title.setObjectName("developmentDrawerTitle")
        title.setContentsMargins(12, 0, 0, 0)
        header.addWidget(title)
        header.addStretch(1)
        self.quick_actions: dict[str, QtWidgets.QToolButton] = {}
        new_thread = QtWidgets.QToolButton()
        new_thread.setObjectName("developmentDrawerNewThread")
        new_thread.setText("+")
        new_thread.setToolTip("New thread")
        new_thread.setAccessibleName("New thread")
        new_thread.clicked.connect(lambda: self._open_path("/development/new/"))
        header.addWidget(new_thread)
        self.quick_actions["New thread"] = new_thread
        header.addSpacing(10)
        self.panel_pin = QtWidgets.QToolButton()
        self.panel_pin.setObjectName("developmentDrawerPanelPin")
        pin_icon = QtGui.QIcon()
        for pinned in (False, True):
            pin_pixmap = QtGui.QPixmap(24, 24)
            pin_pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            painter = QtGui.QPainter(pin_pixmap)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            painter.translate(12, 12)
            if not pinned:
                painter.rotate(45)
            painter.translate(-12, -12)
            painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff" if pinned else "#a8adb6"), 1.6))
            if pinned:
                painter.setBrush(QtGui.QColor("#ffffff"))
            painter.drawPolygon(QtGui.QPolygonF([
                QtCore.QPointF(x, y) for x, y in
                [(8, 3), (16, 3), (15, 11), (18, 15), (6, 15), (9, 11)]
            ]))
            if not pinned:
                painter.drawLine(12, 15, 12, 22)
            painter.end()
            pin_icon.addPixmap(pin_pixmap, QtGui.QIcon.Mode.Normal,
                               QtGui.QIcon.State.On if pinned else QtGui.QIcon.State.Off)
        self.panel_pin.setIcon(pin_icon)
        self.panel_pin.setIconSize(QtCore.QSize(18, 18))
        self.panel_pin.setCheckable(True)
        self.panel_pin.setAccessibleName("Keep sidebar open")
        self.panel_pin.setToolTip("Keep sidebar open")
        self.panel_pin.toggled.connect(self._set_panel_pinned)
        header.addWidget(self.panel_pin)
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

        self.tree = DevelopmentProjectTree()
        self.tree.setItemDelegate(QtWidgets.QStyledItemDelegate(self.tree))
        self.tree.setIconSize(QtCore.QSize(22, 22))
        self.tree.toggleRequested.connect(self._queue_project_toggle)
        self.tree.threadOpenRequested.connect(self._open_thread_index)
        self.tree.setColumnCount(4)
        self.tree.setColumnHidden(2, True)
        self.tree.setHeaderLabels(["", "", "", ""])
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            self.tree.header().setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().hide()
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setWordWrap(False)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setTextElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self.tree.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.itemSelectionChanged.connect(self._remember_selection)
        self.tree.itemClicked.connect(self._handle_item_click)
        self.tree.itemExpanded.connect(self._save_section_state)
        self.tree.itemCollapsed.connect(self._save_section_state)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.tree, 1)

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
            from distr.core.incoming.events import proposal_change_version
            version = proposal_change_version()
            if version != self._proposal_change_version:
                self._proposal_change_version = version
                self._proposal_last_refresh = 0.0
            if now - self._proposal_last_refresh >= 60:
                self._refresh_proposals()
            self._refresh_thread_title_widgets(now)
            if now - self._last_live_sync >= 1:
                self._sync_live_state(now)
            else:
                self._refresh_elapsed_labels(now)
            if self.panel_pin.isChecked() or self._pointer_inside(pointer) or self._screen_at_hot_corner(pointer) is self._open_screen:
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
        self._refresh_proposals()
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

    def _set_panel_pinned(self, pinned: bool) -> None:
        self.panel_pin.setToolTip("Allow sidebar to hide" if pinned else "Keep sidebar open")
        self.panel_pin.setAccessibleName(self.panel_pin.toolTip())
        self._last_inside_at = time.monotonic()

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
        self._sync_pinned_projects(groups)
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
            section_font.setUnderline(True)
            section_font.setPixelSize(14)
            section.setFont(0, section_font)
            section.setForeground(0, QtGui.QColor("#c6cad1"))
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
        self._render_proposals()

    def _run_proposal_task(self, operation, callback):
        future = Future()
        self._proposal_tasks[future] = callback
        def run():
            try:
                future.set_result(operation())
            except Exception as exc:
                future.set_exception(exc)
        threading.Thread(target=run, name='desktop-proposal', daemon=True).start()
        self._proposal_task_timer.start()

    def _poll_proposal_tasks(self):
        # Workers never touch Qt objects. The widget-owned timer disappears with
        # the panel, even if a source request is still finishing in the background.
        for future, callback in list(self._proposal_tasks.items()):
            if not future.done():
                continue
            self._proposal_tasks.pop(future)
            try:
                result, error = future.result(), None
            except Exception as exc:
                result, error = None, str(exc)
            callback(result, error)
        if not self._proposal_tasks:
            self._proposal_task_timer.stop()

    def _refresh_proposals(self):
        if self._proposal_loading:
            return
        self._proposal_loading = True
        self._proposal_last_refresh = time.monotonic()
        self._render_proposals()
        self._run_proposal_task(load_desktop_proposals, self._proposals_loaded)

    def _proposals_loaded(self, result, error):
        self._proposal_loading = False
        self._proposal_error = error or ''
        if not error:
            self._proposal_error = str((result or {}).get('tensology', {}).get('error') or '')
            incoming = list((result or {}).get('proposals') or [])
            self._proposals = list({str(row.get('id')): row for row in (self._proposals if self._proposal_error else []) + incoming}.values())
        self._render_proposals()

    def _render_proposals(self):
        section = self._section_rows.get('proposals')
        if section is None:
            section = QtWidgets.QTreeWidgetItem(['Proposals'])
            section.setData(0, QtCore.Qt.ItemDataRole.UserRole, {'kind': 'section', 'key': 'proposals'})
            section.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            font = section.font(0)
            font.setBold(True)
            font.setUnderline(True)
            font.setPixelSize(14)
            section.setFont(0, font)
            section.setForeground(0, QtGui.QColor('#c6cad1'))
            self.tree.addTopLevelItem(section)
            self._section_rows['proposals'] = section
            section.setExpanded(self._section_expanded['proposals'])
        section.setText(0, f"{'▾' if section.isExpanded() else '▸'} Proposals ({len(self._proposals)})")
        for index in range(section.childCount()):
            widget = self.tree.itemWidget(section.child(index), 0)
            if widget:
                widget.hide()
                widget.deleteLater()
        section.takeChildren()
        if self._proposal_error or not self._proposals:
            text = self._proposal_error or ('Loading proposals...' if self._proposal_loading else 'No proposals right now.')
            item = QtWidgets.QTreeWidgetItem([text])
            item.setToolTip(0, text)
            section.addChild(item)
            item.setFirstColumnSpanned(True)
            if self._proposal_error:
                item.setText(0, '')
                error_row = QtWidgets.QWidget()
                error_layout = QtWidgets.QVBoxLayout(error_row)
                error_layout.setContentsMargins(8, 8, 8, 8)
                error_label = QtWidgets.QLabel(text)
                error_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
                error_label.setWordWrap(True)
                error_label.setStyleSheet('color: #ff8585; font-size: 12px;')
                error_layout.addWidget(error_label)
                retry = QtWidgets.QPushButton('Retry')
                retry.setStyleSheet('QPushButton { background: #e7e9ed; color: #08090b; border: 0; border-radius: 4px; padding: 6px 12px; font-size: 12px; }')
                retry.setAccessibleName('Retry loading proposals')
                retry.clicked.connect(self._refresh_proposals)
                error_layout.addWidget(retry, 0, QtCore.Qt.AlignmentFlag.AlignRight)
                error_label.ensurePolished()
                width = max(200, min(self.WIDTH, self.width()) - 40)
                height = QtGui.QFontMetrics(error_label.font()).boundingRect(QtCore.QRect(0, 0, width, 2000), QtCore.Qt.TextFlag.TextWordWrap, text).height()
                item.setSizeHint(0, QtCore.QSize(200, max(72, height + 58)))
                self.tree.setItemWidget(item, 0, error_row)
        for proposal in self._proposals:
            item = QtWidgets.QTreeWidgetItem()
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, {'kind': 'proposal', 'proposal': proposal})
            section.addChild(item)
            item.setFirstColumnSpanned(True)
            row = QtWidgets.QWidget()
            box = QtWidgets.QVBoxLayout(row)
            box.setContentsMargins(8, 8, 8, 8)
            box.setSpacing(4)
            title = QtWidgets.QLabel(str(proposal.get('title') or 'Proposal'))
            title.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            title.setWordWrap(True)
            title.setStyleSheet('color: #e7e9ed; font-size: 13px;')
            box.addWidget(title)
            controls = QtWidgets.QHBoxLayout()
            linked_project = next((group for _, group in self._project_rows if group['project_id'] == proposal.get('project_id') and group['project_id'] is not None), None)
            project = QtWidgets.QLabel(str(linked_project['name'] if linked_project else proposal.get('project_name') or 'Link a project'))
            project.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            project.setMaximumWidth(200)
            project.setObjectName('proposalProjectBadge')
            project.setWordWrap(False)
            project.setStyleSheet('background: #253958; color: #dce0e5; border-radius: 4px; padding: 4px 8px; font-size: 12px;')
            project.ensurePolished()
            project.setToolTip(project.text())
            project.setText(QtGui.QFontMetrics(project.font()).elidedText(project.text(), QtCore.Qt.TextElideMode.ElideRight, 180))
            project.setMinimumWidth(min(200, QtGui.QFontMetrics(project.font()).horizontalAdvance(project.text()) + 16))
            controls.addWidget(project)
            controls.addStretch()
            busy = str(proposal.get('id')) in self._proposal_busy
            button = QtWidgets.QPushButton('Starting...' if busy else 'Create thread')
            button.setAccessibleName(f"Create thread for {proposal.get('title') or 'proposal'}")
            button.setEnabled(bool(linked_project) and not self._proposal_busy)
            if not linked_project:
                button.setToolTip('Assign this proposal to a project in Incoming before starting work.')
            button.setStyleSheet('QPushButton { background: #e7e9ed; color: #08090b; border: 0; border-radius: 4px; padding: 6px 8px; font-size: 12px; } QPushButton:disabled { color: #747b87; background: #292e35; }')
            button.clicked.connect(lambda _checked=False, selected=dict(proposal): self._start_proposal(selected))
            controls.addWidget(button)
            box.addLayout(controls)
            title_width = max(200, min(self.WIDTH, self.width()) - 40)
            title.ensurePolished()
            height = QtGui.QFontMetrics(title.font()).boundingRect(QtCore.QRect(0, 0, title_width, 2000), QtCore.Qt.TextFlag.TextWordWrap, title.text()).height()
            item.setSizeHint(0, QtCore.QSize(200, max(72, height + 52)))
            self.tree.setItemWidget(item, 0, row)

    def _start_proposal(self, proposal):
        key = str(proposal.get('id'))
        if self._proposal_busy:
            return
        self._proposal_busy.add(key)
        self._proposal_error = ''
        self._render_proposals()
        def finished(chat_id, error):
            self._proposal_busy.discard(key)
            self._proposal_error = error or ''
            if error:
                self._render_proposals()
                return
            self._proposals = [row for row in self._proposals if str(row.get('id')) != key]
            self._selected_chat_id = int(chat_id)
            self._selected_item_key = ('thread', int(chat_id))
            self._project_expanded[int(proposal['project_id'])] = True
            self._refresh_threads()
            for item, group in self._project_rows:
                if group['project_id'] == int(proposal['project_id']):
                    item.setExpanded(True)
                    if item.parent():
                        item.parent().setExpanded(True)
            for item, thread in self._thread_rows:
                if int(thread['chat_id']) == int(chat_id):
                    self.tree.setCurrentItem(item)
                    self.tree.scrollToItem(item)
                    break
            self._open_path(f'/development/threads/{int(chat_id)}/')
        self._run_proposal_task(lambda: start_desktop_proposal(proposal), finished)

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
            "",
        ])
        item.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"kind": "thread", **thread})
        item.setData(0, QtCore.Qt.ItemDataRole.AccessibleTextRole, str(thread["title"]))
        item.setToolTip(0, str(thread["title"]))
        item.setToolTip(1, "Thread time")
        item.setTextAlignment(1, QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
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
            "",
            "+" if group["project_id"] is not None else "",
        ])
        project_item.setFlags(
            QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
        )
        thread_count = len(group["threads"])
        project_item.setIcon(0, project_folder_icon(bool(thread_count)))
        project_item.setToolTip(0, f"{group['name']}: {thread_count} thread{'s' if thread_count != 1 else ''}")
        project_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"kind": "project", **group})
        project_font = project_item.font(0)
        project_font.setBold(True)
        project_item.setFont(0, project_font)
        project_item.setSizeHint(0, self._project_title_size(str(group["name"]), project_font))
        project_item.setForeground(0, QtGui.QColor("#aeb3bd"))
        project_item.setToolTip(1, "Total development time this week")
        project_item.setTextAlignment(1, QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        project_item.setToolTip(3, f"Create a new thread in {group['name']}")
        project_item.setTextAlignment(3, QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
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
                "",
            ])
            child.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"kind": "thread", **thread})
            child.setData(0, QtCore.Qt.ItemDataRole.AccessibleTextRole, str(thread["title"]))
            child.setToolTip(0, str(thread["title"]))
            child.setToolTip(1, "Thread time")
            child.setTextAlignment(1, QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
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
        activity = ThreadTitleWidget(title=str(thread["title"]), active=bool(thread["active"]),
                                     tree=self.tree, item=item, activity_only=True)
        activity.setToolTip("Working on this thread" if thread["active"] else "")
        self.tree.setItemWidget(item, 3, activity)
        self._thread_title_widgets.append((item, thread, activity))

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

    def _sync_live_state(self, now: float) -> None:
        """Update shared timer state without rebuilding or expanding the tree."""
        self._last_live_sync = now
        try:
            fresh_groups = load_development_groups(self._session_provider)
        except Exception as exc:
            logger.warning("Could not sync Development hot-corner timers: %s", exc)
            return
        if self._sync_pinned_projects(fresh_groups):
            self._refresh_threads()
            return
        fresh_by_project = {group["project_id"]: group for group in fresh_groups}
        fresh_threads = {
            int(thread["chat_id"]): thread
            for group in fresh_groups
            for thread in group["threads"]
        }
        if set(fresh_threads) != {int(thread["chat_id"]) for _, thread in self._thread_rows}:
            self._refresh_threads()
            return
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
        recent_threads = recent_development_threads(groups)
        expected = [int(thread['chat_id']) for thread in recent_threads[:5]]
        current = [
            int(value['chat_id'])
            for index in range(section.childCount())
            if isinstance(value := section.child(index).data(0, QtCore.Qt.ItemDataRole.UserRole), dict)
            and value.get('kind') == 'thread'
        ]
        if current == expected and section.childCount() == len(expected) + int(len(recent_threads) > 5):
            return
        expanded = section.isExpanded()
        scroll = self.tree.verticalScrollBar().value()
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
        for thread in recent_threads[:5]:
            self._add_recent_row(section, thread, self._selected_item_key)
        if len(recent_threads) > 5:
            more = QtWidgets.QTreeWidgetItem(["Show more"])
            more.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"kind": "recent_more"})
            more.setForeground(0, QtGui.QColor("#8f949e"))
            section.addChild(more)
        section.setExpanded(expanded)
        self.tree.verticalScrollBar().setValue(scroll)

    def _sync_pinned_projects(self, groups: list[dict[str, Any]]) -> bool:
        if not self._uses_shared_pins:
            return False
        from distr.core.workflow.development_sidebar import load_pinned_boards
        keys = load_pinned_boards()
        if keys is None:
            return False
        next_ids = {group['project_id'] for group in groups if group.get('board_key') in keys}
        changed = next_ids != self._pinned_project_ids
        self._pinned_project_ids = next_ids
        return changed

    def _handle_item_click(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        value = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(value, dict):
            return
        if value.get("kind") == "section":
            section_key = str(value.get("key") or "")
            if section_key in self._section_expanded:
                index = QtCore.QPersistentModelIndex(self.tree.indexFromItem(item))
                # Let Qt finish click/accessibility dispatch before changing its tree.
                QtCore.QTimer.singleShot(0, lambda: self._toggle_section(index, section_key))
            return
        if value.get("kind") == "recent_more":
            self._open_path("/development/?recent=1")
            return
        if value.get("kind") == "project":
            if column == 3 and value.get("project_id") is not None:
                self._create_project_thread(value)
        elif value.get("kind") == "thread":
            self.tree.setCurrentItem(item)

    def _open_thread_index(self, index: QtCore.QModelIndex) -> None:
        item = self.tree.itemFromIndex(index)
        value = item.data(0, QtCore.Qt.ItemDataRole.UserRole) if item else None
        if isinstance(value, dict) and value.get("kind") == "thread":
            # Open the Development thread itself, not the plain chat view.
            # /chat/?id= lands on a different page, which is why the panel and
            # the opened view looked out of sync.
            self._open_path(f"/development/threads/{int(value['chat_id'])}/")

    def _queue_project_toggle(self, index: QtCore.QModelIndex) -> None:
        item = self.tree.itemFromIndex(index)
        if item is None or item.childCount() == 0:
            return
        persistent = QtCore.QPersistentModelIndex(index)
        QtCore.QTimer.singleShot(0, lambda: self._toggle_section(persistent, None))

    def _save_section_state(self, item: QtWidgets.QTreeWidgetItem) -> None:
        value = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if isinstance(value, dict) and value.get("kind") == "section":
            key = value.get("key")
            if key in self._section_expanded:
                self._section_expanded[key] = item.isExpanded()
                if key in {"projects", "proposals"}:
                    self._view_settings.setValue(f"sidebar/{key}Expanded", item.isExpanded())
                if key == 'proposals':
                    item.setText(0, f"{'▾' if item.isExpanded() else '▸'} Proposals ({len(self._proposals)})")

    def _toggle_section(self, index: QtCore.QPersistentModelIndex, key: str | None) -> None:
        if not index.isValid():
            return
        item = self.tree.itemFromIndex(QtCore.QModelIndex(index))
        if item is None:
            return
        if key is None and item.childCount() == 0:
            return
        expanded = not item.isExpanded()
        item.setExpanded(expanded)
        if key is not None:
            self._section_expanded[key] = expanded
        else:
            value = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            self._project_expanded[value["project_id"]] = expanded

    def _show_context_menu(self, position: QtCore.QPoint) -> None:
        item = self.tree.itemAt(position)
        value = item.data(0, QtCore.Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(value, dict) or value.get("kind") not in {"project", "thread"}:
            return
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #111317; color: #e7e9ed; border: 1px solid #4b5059; padding: 5px; }
            QMenu::item { padding: 7px 22px 7px 12px; }
            QMenu::item:selected { background: #253958; color: #ffffff; }
            QMenu::item:disabled { color: #747b87; }
            QMenu::separator { height: 1px; background: #4b5059; margin: 5px 7px; }
            QMenu::section { color: #aeb5c0; padding: 6px 12px 3px; font-weight: bold; }
        """)
        if value["kind"] == "project" and value.get("project_id") is not None:
            project_id = int(value["project_id"])
            board_key = value.get("board_key")
            if board_key:
                provider, identity = str(board_key).split(":", 1)
                base = f"/development/boards/{provider}/{identity}/"
                menu.addAction("Go to project", lambda: self._open_path(base + "kanban/"))
                for label, action in (
                    ("New Ticket", "new-ticket"), ("New Thread", "new-chat"),
                    ("Plan", "plan"), ("Board View", "kanban"), ("List View", "list"),
                    ("Refresh Board" if provider == "decisions" else f"Re-sync {provider.title()} Board", "refresh"),
                    ("Edit Board", "edit"), ("Set as Active", "activate"),
                    ("Show in Finder" if platform.system() == "Darwin" else "Show in File Manager", "open-folder"),
                    ("Unpin Board" if project_id in self._pinned_project_ids else "Pin Board", "pin"),
                    ("Open Terminals", "terminals"), ("Start / Stop Terminals", "terminal-toggle"),
                    ("Archive Board", "archive"), ("Delete project", "delete"),
                ):
                    if provider != "decisions" and action in {"activate", "archive", "delete"}:
                        continue
                    heading = {"new-ticket": "Create", "kanban": "View", "edit": "Board",
                               "terminals": "Terminals", "archive": "Manage"}.get(action)
                    if heading:
                        menu.addSeparator()
                        section = menu.addAction(heading)
                        section.setEnabled(False)
                        font = section.font()
                        font.setBold(True)
                        font.setPointSize(10)
                        section.setFont(font)
                    if action == "pin":
                        menu.addAction(label, lambda: self._toggle_project_pin(project_id))
                        continue
                    entry = menu.addAction(label, lambda _checked=False, selected=action:
                                           self._open_board_action(str(board_key), selected))
                    if action in {"archive", "delete"}:
                        marker = QtGui.QPixmap(12, 12)
                        marker.fill(QtCore.Qt.GlobalColor.transparent)
                        painter = QtGui.QPainter(marker)
                        painter.setPen(QtGui.QPen(QtGui.QColor("#ff8585"), 1.5))
                        painter.drawRect(3, 4, 6, 6)
                        painter.drawLine(2, 2, 10, 2)
                        painter.end()
                        entry.setIcon(QtGui.QIcon(marker))
            else:
                menu.addAction("Unpin project" if project_id in self._pinned_project_ids else "Pin project",
                               lambda: self._toggle_project_pin(project_id))
        elif value["kind"] == "thread":
            from distr.core.workflow.development_control import (
                DEVELOPMENT_TIMER_MENU,
                development_timer_menu_items,
            )

            chat_id = int(value["chat_id"])
            menu.addSection(DEVELOPMENT_TIMER_MENU["heading"])
            for item in development_timer_menu_items(bool(value["time_running"])):
                action = menu.addAction(item["label"])
                action.triggered.connect(
                    lambda _checked=False, timer_action=item["action"]: self._update_thread_time(
                        chat_id, timer_action
                    )
                )
        self._context_menu = menu
        menu.popup(self.tree.viewport().mapToGlobal(position))

    def _open_board_action(self, board_key: str, action: str) -> None:
        from distr.core.workflow.development_sidebar import queue_desktop_board_action
        token = queue_desktop_board_action(board_key, action)
        provider, identity = board_key.split(":", 1)
        self._open_path(f"/development/boards/{provider}/{identity}/#desktop-action={token}")

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
        if self._uses_shared_pins:
            from distr.core.workflow.development_sidebar import load_pinned_boards, save_pinned_boards
            groups = [group for _item, group in self._project_rows]
            keys = load_pinned_boards()
            if keys is None:
                keys = [group['board_key'] for group in groups
                        if group['project_id'] in self._pinned_project_ids and group.get('board_key')]
            key = next((group.get('board_key') for group in groups if group['project_id'] == project_id), None)
            if key:
                keys = [existing for existing in keys if existing != key]
                if project_id in self._pinned_project_ids:
                    keys.append(key)
                save_pinned_boards(keys)
            else:
                self._pin_saver(sorted(self._pinned_project_ids))
        else:
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
            self._open_path(f"/development/threads/{int(chat_id)}/")
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
