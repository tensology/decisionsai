"""Non-activating Development drawer revealed by a top-left pointer dwell."""

from __future__ import annotations

import json
import logging
import platform
import time
import threading
from concurrent.futures import Future
from ctypes import c_void_p
from datetime import datetime, timedelta
from typing import Any, Callable

from PyQt6 import QtCore, QtGui, QtWidgets

from distr.core.db import Chat, get_session
from distr.core.db.kanban import KanbanBoard
from distr.core.db.projects import Project
from distr.core.kanban.lifecycle import ensure_project_boards
from distr.core.project_startup_terminals import parse_startup_command_lines, project_startup_terminals_running
from distr.core.workflow.development_sidebar import board_key_for
from distr.core.db.time import utc_now_naive
from distr.core.db.workflow import AutoWorkflowRun, DevelopmentTimeEntry, DevelopmentWorkItem


logger = logging.getLogger(__name__)
# Archived legacy setting; migrated into development_pinned_board_keys.
PINNED_PROJECTS_SETTING = "development_hot_corner_pinned_project_ids"


class _ExpiredProposalRibbon(QtWidgets.QWidget):
    """Native equivalent of the Incoming card's diagonal top-right ribbon."""
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setClipRect(self.rect())
        painter.translate(self.width() - 28, 24)
        painter.rotate(45)
        painter.fillRect(QtCore.QRect(-66, -12, 132, 24), QtGui.QColor('#626770'))
        painter.setPen(QtGui.QColor('#ffffff'))
        font = painter.font()
        font.setPixelSize(12)
        painter.setFont(font)
        painter.drawText(QtCore.QRect(-66, -12, 132, 24), QtCore.Qt.AlignmentFlag.AlignCenter, 'Expired')


class _ExpiredProposalCard(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self._ribbon = _ExpiredProposalRibbon(self)
        self._ribbon.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._ribbon.setGeometry(max(0, self.width() - 94), 0, 94, 94)
        self._ribbon.raise_()

    def showEvent(self, event):
        super().showEvent(event)
        self._ribbon.raise_()


def load_desktop_proposals():
    from distr.core.incoming.proposals import list_incoming_proposals

    # Opening desktop navigation must never launch a local triage model.
    return list_incoming_proposals(limit=100, refresh_local=False)


def start_desktop_proposal(proposal):
    from distr.core.tensology_client import _local_api_post
    from distr.core.incoming.media import list_proposal_attachment_manifest
    from distr.core.incoming.transcription import thread_preparation_timeout

    # One application-owned endpoint handles both native and browser actions.
    result = _local_api_post('workflows/studio/incoming/proposals/thread', {'proposal': proposal},
                             timeout=thread_preparation_timeout(list_proposal_attachment_manifest(proposal)))
    if not isinstance(result, dict) or not result.get('chat_id'):
        raise ValueError('The proposal thread could not be started. Retry from Proposals.')
    return int(result['chat_id'])


def load_pinned_board_keys() -> list[str]:
    """The one pin list shared with the web sidebar (board keys)."""
    from distr.core.workflow.development_sidebar import load_pinned_boards

    return load_pinned_boards() or []


def project_folder_icon(has_threads: bool) -> QtGui.QIcon:
    """Filled folder when the board has threads underneath, outline when it has none."""
    pixmap = QtGui.QPixmap(24, 24)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    color = QtGui.QColor("#dce0e5" if has_threads else "#626770")
    painter.setPen(QtGui.QPen(color, 1.5))
    painter.setBrush(color if has_threads else QtCore.Qt.BrushStyle.NoBrush)
    painter.drawPolygon(QtGui.QPolygonF([
        QtCore.QPointF(x, y) for x, y in
        [(3, 6), (10, 6), (12, 9), (21, 9), (21, 20), (3, 20)]
    ]))
    painter.end()
    icon = QtGui.QIcon()
    icon.addPixmap(pixmap, QtGui.QIcon.Mode.Normal, QtGui.QIcon.State.Off)
    icon.addPixmap(pixmap, QtGui.QIcon.Mode.Normal, QtGui.QIcon.State.On)
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
    external_catalog: dict | None = None,
) -> list[dict[str, Any]]:
    """Return visible boards and the Development root threads assigned to them."""
    if external_catalog is None and session_provider is get_session:
        from distr.gui.web.routes.kanban import external_board_catalog

        external_catalog = external_board_catalog()
    external_catalog = external_catalog or {}
    now = now or utc_now_naive()
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    with session_provider() as db:
        projects = (
            db.query(Project).filter(Project.deleted_at.is_(None))
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

        projects_by_id = {int(project.id): project for project in projects}
        primary_keys = {pid: board_key_for(board) for pid, board in boards_by_project.items()}
        groups_by_key = {}
        for board in db.query(KanbanBoard).filter(KanbanBoard.archived.is_not(True)).order_by(
            KanbanBoard.position, KanbanBoard.name, KanbanBoard.id
        ):
            key = board_key_for(board)
            if key and key not in groups_by_key:
                groups_by_key[key] = {
                    "project_id": board.default_project_id,
                    "board_key": key,
                    "name": board.name or "Untitled",
                    "weekly_seconds": 0,
                    "threads": [],
                }
        external_keys = []
        for provider in ("jira", "trello"):
            for board in external_catalog.get(provider) or ():
                key = f"{provider}:{board['id']}"
                external_keys.append(key)
                group = groups_by_key.setdefault(key, {
                    "project_id": board.get("default_project_id"),
                    "board_key": key,
                    "weekly_seconds": 0,
                    "threads": [],
                })
                project = projects_by_id.get(group["project_id"])
                group["name"] = (
                    project.name if provider == "jira" and project and primary_keys.get(project.id) == key
                    else board.get("name") or group.get("name") or "Untitled"
                )
        groups = [groups_by_key[key] for key in dict.fromkeys([*external_keys, *groups_by_key])]
        for group in groups:
            project = projects_by_id.get(group["project_id"])
            group["project_id"] = project.id if project else None
            group["has_terminal_commands"] = bool(project and parse_startup_command_lines(project.startup_instructions))
            group["terminal_running"] = bool(project and project_startup_terminals_running(project.id))
        unassigned: list[dict[str, Any]] = []

        for chat in chats:
            try:
                params = json.loads(chat.params or "{}")
            except (TypeError, ValueError):
                params = {}
            development = params.get("development") if isinstance(params, dict) else None
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
                "created_at": chat.created_date.timestamp() if chat.created_date else 0,
                "source_type": str(development.get("source_type") or ""),
                "pinned": bool(development.get("pinned")),
                "status": status,
                "active": (
                    status in {"initializing", "queued", "running", "waiting"}
                    and int(chat.id) in live_chat_ids
                ),
                "elapsed_seconds": elapsed_seconds,
                "weekly_seconds": weekly_seconds,
                "time_running": time_running,
            }
            explicit_key = (item.board_key if item is not None else None) or development.get("board_key")
            group = groups_by_key.get(explicit_key) if isinstance(explicit_key, str) else None
            if group is None and project_id is not None:
                group = groups_by_key.get(primary_keys.get(int(project_id)))
            if group is not None:
                group["threads"].append(thread)
                group["weekly_seconds"] += weekly_seconds
            else:
                unassigned.append(thread)

        for group in groups:
            group["threads"].sort(key=lambda thread: (thread["created_at"], thread["chat_id"]), reverse=True)
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
        if not is_background_source(thread.get("source_type"))
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


# Threads nobody typed into: automations, Incoming-dispatched work (WhatsApp/Gmail proposals),
# scheduled/initiative/orchestrator runs and workflow runs. Ordinary user prompts stay out.
# Same rule as BACKGROUND_SOURCE_TYPES in static/development/sidebar/index.js.
BACKGROUND_SOURCE_TYPES = frozenset({
    "automation", "incoming_proposal", "scheduled", "initiative", "orchestrator", "proactive_orchestrator",
})


def is_background_source(source_type: Any) -> bool:
    source = str(source_type or "").strip().lower()
    return source in BACKGROUND_SOURCE_TYPES or "workflow" in source


def group_identity(group: dict[str, Any]) -> str | int | None:
    return group.get("board_key") or group["project_id"]


def thread_order(thread: dict[str, Any]) -> tuple[float, int]:
    return float(thread.get("modified_at") or 0), int(thread["chat_id"])


def running_automation_threads(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Background-started threads with a live run right now (same rule as the web "Running" group)."""
    seen: set[int] = set()
    threads = []
    for group in groups:
        for thread in group["threads"]:
            chat_id = int(thread["chat_id"])
            if is_background_source(thread.get("source_type")) and thread.get("active") and chat_id not in seen:
                seen.add(chat_id)
                threads.append(thread)
    return sorted(
        threads,
        key=thread_order,
        reverse=True,
    )


SECTION_SETTINGS = {
    "running": "sidebar/runningExpanded",
    "recent": "sidebar/recentExpanded",
    "automations": "sidebar/automationsExpanded",
    "no_project": "sidebar/noProjectExpanded",
    "pinned": "sidebar/pinnedExpanded",
    "projects": "sidebar/projectsExpanded",
    "proposals": "sidebar/proposalsExpanded",
}


PANEL_PINNED_SETTING = "sidebar/panelPinned"
PANEL_PINNED_SCREEN_SETTING = "sidebar/panelPinnedScreen"


def section_title_text(title: str, expanded: bool) -> str:
    return f"{'▾' if expanded else '▸'} {title}"


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


def wrapped_title(text: str, font: QtGui.QFont, width: int) -> tuple[QtGui.QTextLayout, int]:
    layout = QtGui.QTextLayout(text.replace("\n", "\u2028"), font)
    options = QtGui.QTextOption()
    options.setWrapMode(QtGui.QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(options)
    layout.beginLayout()
    height = 0
    while (line := layout.createLine()).isValid():
        line.setLineWidth(max(1, width))
        line.setPosition(QtCore.QPointF(0, height))
        height += round(line.height())
    layout.endLayout()
    return layout, height


class SidebarTitleDelegate(QtWidgets.QStyledItemDelegate):
    """Measure and paint the same wrapped title in each board or thread row."""

    @staticmethod
    def title_left(index: QtCore.QModelIndex) -> int:
        value = index.data(QtCore.Qt.ItemDataRole.UserRole)
        parent = index.parent().data(QtCore.Qt.ItemDataRole.UserRole)
        if any(isinstance(row, dict) and row.get("kind") == "project" for row in (value, parent)):
            return 40
        return 22

    def paint(self, painter, option, index):
        value = index.data(QtCore.Qt.ItemDataRole.UserRole)
        if index.column() != 0 or not isinstance(value, dict) or value.get("kind") not in {"project", "thread"}:
            super().paint(painter, option, index)
            return
        option = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        title = option.text
        option.text = ""
        style = option.widget.style()
        style.drawControl(QtWidgets.QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)
        left = option.rect.left() + self.title_left(index)
        text, height = wrapped_title(title, option.font, option.rect.right() - left - 5)
        selected = option.state & QtWidgets.QStyle.StateFlag.State_Selected
        color = option.palette.color(
            QtGui.QPalette.ColorRole.HighlightedText if selected else QtGui.QPalette.ColorRole.Text
        )
        if value["kind"] == "thread" and not selected:
            color = QtGui.QColor("#72a7ff" if value.get("active") else "#d6d9df")
        painter.save()
        painter.setPen(color)
        text.draw(painter, QtCore.QPointF(left, option.rect.top() + (option.rect.height() - height) / 2))
        painter.restore()


class ThreadActivityWidget(QtWidgets.QWidget):
    """Paint the active-work indicator without taking the row's pointer events."""

    SPINNER_SIZE = 9

    def __init__(self, tree: QtWidgets.QTreeWidget, item: QtWidgets.QTreeWidgetItem, active: bool):
        super().__init__(tree.viewport())
        self._tree = tree
        self._item = item
        self._active = bool(active)
        self._motion_reduced = _prefers_reduced_motion()
        self._animation_now = time.monotonic()
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setToolTip("Working on this thread" if self._active else "")

    def advance(self, now: float, active: bool) -> None:
        if bool(active) != self._active:
            self._active = bool(active)
            self.setToolTip("Working on this thread" if self._active else "")
            self.update()
        self._animation_now = now
        if self._active:
            self.update()

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        if not self._active:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        spinner_rect = QtCore.QRectF(
            (self.width() - self.SPINNER_SIZE) / 2 + 0.5,
            (self.height() - self.SPINNER_SIZE) / 2 + 0.5,
            self.SPINNER_SIZE - 1,
            self.SPINNER_SIZE - 1,
        )
        color = "#ffffff" if self._tree.currentItem() is self._item else "#72a7ff"
        pen = QtGui.QPen(QtGui.QColor(color), 1.5)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        start_angle = 90 if self._motion_reduced else int(self._animation_now * 300) % 360
        painter.drawArc(spinner_rect, start_angle * 16, 250 * 16)


class DevelopmentProjectTree(QtWidgets.QTreeWidget):
    toggleRequested = QtCore.pyqtSignal(QtCore.QModelIndex)
    threadOpenRequested = QtCore.pyqtSignal(QtCore.QModelIndex)
    rowActivateRequested = QtCore.pyqtSignal(object, int)

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
            return
        selected = self.itemAt(event.position().toPoint())
        value = selected.data(0, QtCore.Qt.ItemDataRole.UserRole) if selected else None
        if (event.button() == QtCore.Qt.MouseButton.LeftButton and isinstance(value, dict)
                and value.get("kind") == "thread" and self.columnAt(int(event.position().x())) == 0):
            self.threadOpenRequested.emit(self.indexFromItem(selected))
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        item = self.currentItem()
        if item and event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter, QtCore.Qt.Key.Key_Space):
            self.rowActivateRequested.emit(item, self.currentColumn())
            return
        if item and (event.key() == QtCore.Qt.Key.Key_Menu or (
            event.key() == QtCore.Qt.Key.Key_F10 and event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier
        )):
            self.customContextMenuRequested.emit(self.visualItemRect(item).center())
            return
        super().keyPressEvent(event)


class DevelopmentHotCornerPanel(QtWidgets.QWidget):
    """Slide in a compact Development board selector after a short dwell."""

    # 2026-11-13: 500 ms (was 2.5 s), like a macOS hot corner. Corner area stays 8 px.
    DWELL_SECONDS = 0.5
    LEAVE_SECONDS = 0.45
    WIDTH = 400
    MENU_ROW_HEIGHT = 36
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
        pin_saver: Callable[[list[int]], None] | None = None,
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
        # Pins come from the shared board-key list; explicit ids/saver are test hooks.
        self._pinned_project_ids = {int(project_id) for project_id in (pinned_project_ids or [])}
        self._pinned_board_keys: set[str] = set()
        self._pin_saver = pin_saver
        self._uses_shared_pins = pinned_project_ids is None and pin_saver is None
        self._corner_screen: QtGui.QScreen | None = None
        self._corner_started_at: float | None = None
        self._open_screen: QtGui.QScreen | None = None
        self._open_geometry = QtCore.QRect()
        self._keyboard_mode = False
        self._close_for_pointer = False
        self._suppress_corner = False
        self._last_inside_at = 0.0
        self._selected_chat_id: int | None = None
        self._selected_item_key: tuple[str, int | str | None] | None = None
        self._thread_rows: list[tuple[QtWidgets.QTreeWidgetItem, dict[str, Any]]] = []
        self._thread_activity_widgets: list[
            tuple[QtWidgets.QTreeWidgetItem, dict[str, Any], ThreadActivityWidget]
        ] = []
        self._project_rows: list[tuple[QtWidgets.QTreeWidgetItem, dict[str, Any]]] = []
        self._section_rows: dict[str, QtWidgets.QTreeWidgetItem] = {}
        self._view_settings = QtCore.QSettings("Tensology", "DecisionsAI")
        self._section_expanded = {
            "running": self._view_settings.value("sidebar/runningExpanded", True, type=bool),
            "no_project": self._view_settings.value("sidebar/noProjectExpanded", False, type=bool),
            "pinned": self._view_settings.value("sidebar/pinnedExpanded", True, type=bool),
            "recent": self._view_settings.value("sidebar/recentExpanded", False, type=bool),
            "automations": self._view_settings.value("sidebar/automationsExpanded", False, type=bool),
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
        self._project_expanded: dict[str | int | None, bool] = {}
        self._structure = None
        # Threads with no project live in their own section, not as a project row.
        self._no_project_weekly = 0
        self._no_project_threads: list[dict[str, Any]] = []
        self._context_menu: QtWidgets.QMenu | None = None
        self._rendered_at = time.monotonic()
        self._last_live_sync = self._rendered_at

        self._build_ui()
        self._escape_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Escape"), self)
        self._escape_shortcut.activated.connect(self._dismiss_panel)
        self._proposal_task_timer = QtCore.QTimer(self)
        self._proposal_task_timer.setInterval(50)
        self._proposal_task_timer.timeout.connect(self._poll_proposal_tasks)
        self._proposal_expiry_timer = QtCore.QTimer(self)
        self._proposal_expiry_timer.setSingleShot(True)
        self._proposal_expiry_timer.timeout.connect(self._render_proposals)

        self._slide = QtCore.QPropertyAnimation(self, b"geometry", self)
        self._slide.setDuration(240)
        self._slide.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self._slide.finished.connect(self._finish_slide)
        self._hiding = False

        self._timer = QtCore.QTimer(self)
        # Precise timer: the default coarse timer may fire up to 5% late and gets batched by macOS.
        self._timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)
        if self._enabled:
            self._timer.start()
            # Pinned open when the app last closed: open it again on launch.
            if self.panel_pin.isChecked():
                QtCore.QTimer.singleShot(0, self._restore_pinned_open)

    @property
    def selected_chat_id(self) -> int | None:
        return self._selected_chat_id

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            #developmentHotCornerPanel {
                background: #191f3a;
                border-right: 1px solid #353b55;
            }
            QPushButton#developmentDrawerNewThread {
                border: 0; border-radius: 7px;
                background: #2b304c; color: #f5f6ff;
                font-size: 13px; font-weight: 600;
                padding: 0 10px; text-align: left;
            }
            QToolButton#developmentDrawerPanelPin {
                min-width: 28px; max-width: 28px;
                border: 0; background: transparent;
            }
            QPushButton#developmentDrawerNavigation {
                border: 0; border-radius: 5px;
                background: transparent;
                color: #e7e9f7; font-size: 13px;
                text-align: left; padding: 0 10px;
            }
            QPushButton#developmentDrawerNewThread:hover, QPushButton#developmentDrawerNewThread:focus,
            QToolButton#developmentDrawerPanelPin:hover, QToolButton#developmentDrawerPanelPin:focus,
            QPushButton#developmentDrawerNavigation:hover, QPushButton#developmentDrawerNavigation:focus {
                background: #353d61; color: #ffffff;
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
                padding: 0 4px;
            }
            QTreeWidget::item:hover {
                background: #262e4e;
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
            QScrollBar::handle:vertical:disabled {
                background: transparent;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
            """
        )
        # The window clips this surface during the slide. Its layout stays full width.
        self._content = QtWidgets.QWidget(self)
        self._content.resize(self.size())
        layout = QtWidgets.QVBoxLayout(self._content)
        layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetNoConstraint)
        layout.setContentsMargins(10, 10, 10, 8)
        layout.setSpacing(5)

        def navigation_icon(symbol: str) -> QtGui.QIcon:
            pixmap = QtGui.QPixmap(32, 32)
            pixmap.setDevicePixelRatio(2)
            pixmap.fill(QtCore.Qt.GlobalColor.transparent)
            painter = QtGui.QPainter(pixmap)
            font = painter.font()
            font.setPixelSize(13)
            painter.setFont(font)
            painter.setPen(QtGui.QColor("#d5daef"))
            painter.drawText(QtCore.QRect(0, 0, 16, 16), QtCore.Qt.AlignmentFlag.AlignCenter, symbol)
            painter.end()
            return QtGui.QIcon(pixmap)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(6)
        self.quick_actions: dict[str, QtWidgets.QPushButton] = {}
        new_thread = QtWidgets.QPushButton()
        new_thread.setObjectName("developmentDrawerNewThread")
        new_thread.setFixedHeight(self.MENU_ROW_HEIGHT)
        new_thread.setText("New Thread")
        new_thread.setIcon(navigation_icon("✎"))
        new_thread.setAccessibleName("New Thread")
        new_thread.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        new_thread.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        new_thread.clicked.connect(lambda: self._open_path("/development/new/"))
        header.addWidget(new_thread, 1)
        self.quick_actions["New thread"] = new_thread
        self.panel_pin = QtWidgets.QToolButton()
        self.panel_pin.setObjectName("developmentDrawerPanelPin")
        self.panel_pin.setFixedHeight(self.MENU_ROW_HEIGHT)
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
        self.panel_pin.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.panel_pin.setAccessibleName("Keep sidebar open")
        self.panel_pin.setToolTip("Keep sidebar open")
        # Remembered across app restarts (same app settings as the section collapse states).
        restored_pin = self._view_settings.value(PANEL_PINNED_SETTING, False, type=bool)
        self.panel_pin.setChecked(restored_pin)
        if restored_pin:
            self.panel_pin.setToolTip("Allow sidebar to hide")
            self.panel_pin.setAccessibleName("Allow sidebar to hide")
        self.panel_pin.toggled.connect(self._set_panel_pinned)
        header.addWidget(self.panel_pin)
        layout.addLayout(header)

        navigation = QtWidgets.QVBoxLayout()
        navigation.setSpacing(0)
        for label, symbol, path in (
            ("Incoming", "↓", "/development/incoming/"),
            ("Automations", "⚙", "/development/automations/"),
            ("Workflows", "⎇", "/development/workflows/"),
            ("Terminals", ">_", "/development/terminals/"),
            ("Reports", "▥", "/development/reports/"),
        ):
            button = QtWidgets.QPushButton()
            button.setObjectName("developmentDrawerNavigation")
            button.setFixedHeight(self.MENU_ROW_HEIGHT)
            button.setText(label)
            button.setIcon(navigation_icon(symbol))
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
            button.clicked.connect(lambda _checked=False, target=path: self._open_path(target))
            navigation.addWidget(button)
            self.quick_actions[label] = button
        layout.addLayout(navigation)
        layout.addSpacing(4)

        self.tree = DevelopmentProjectTree()
        self.tree.setItemDelegate(SidebarTitleDelegate(self.tree))
        self.tree.setIconSize(QtCore.QSize(22, 22))
        self.tree.toggleRequested.connect(self._queue_project_toggle)
        self.tree.threadOpenRequested.connect(self._open_thread_index)
        self.tree.rowActivateRequested.connect(self._activate_tree_row)
        self.tree.setColumnCount(6)
        self.tree.setColumnHidden(2, True)
        self.tree.setHeaderLabels(["", "", "", "", "", ""])
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tree.header().setMinimumSectionSize(0)
        self.tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        for column, width in ((3, 26), (4, 26), (5, 26)):
            self.tree.header().setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeMode.Fixed)
            self.tree.header().resizeSection(column, width)
        self.tree.header().moveSection(self.tree.header().visualIndex(3), 5)
        self.tree.header().hide()
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setWordWrap(False)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.tree.setTextElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self.tree.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.itemSelectionChanged.connect(self._remember_selection)
        self.tree.itemClicked.connect(self._handle_item_click)
        self.tree.itemExpanded.connect(self._save_section_state)
        self.tree.itemCollapsed.connect(self._save_section_state)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self._title_layout_timer = QtCore.QTimer(self)
        self._title_layout_timer.setSingleShot(True)
        self._title_layout_timer.timeout.connect(self._resize_title_rows)
        self.tree.header().sectionResized.connect(
            lambda column, _before, _after: self._title_layout_timer.start(0) if column == 0 else None
        )
        layout.addWidget(self.tree, 1)
        focus_order = (new_thread, self.panel_pin, *list(self.quick_actions.values())[1:], self.tree)
        for previous, current in zip(focus_order, focus_order[1:]):
            self.setTabOrder(previous, current)

        footer = QtWidgets.QFrame()
        footer.setObjectName("developmentDrawerFooter")
        footer_layout = QtWidgets.QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 10, 0, 0)
        footer_layout.setSpacing(3)
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
            self._slide.stop()
            self._open_screen = None
            self.hide()
            self._restore_hover_focus()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._open_screen is None:
            self._content.resize(event.size())

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
            self._refresh_thread_activity_widgets(now)
            if now - self._last_live_sync >= 1:
                self._sync_live_state(now)
            else:
                self._refresh_elapsed_labels(now)
            inside = self._pointer_inside(pointer) or (
                self._open_screen is not None and self._screen_at_hot_corner(pointer) is self._open_screen
            ) or bool(self._context_menu and self._context_menu.isVisible())
            keep_open = self.panel_pin.isChecked() or self._keyboard_mode or inside
            if self._hiding:
                if self._close_for_pointer and (keep_open or self._open_geometry.contains(pointer)):
                    self._hiding = False
                    self._last_inside_at = now
                    self._animate_geometry(self.geometry(), self._open_geometry)
            elif keep_open:
                self._last_inside_at = now
            elif now - self._last_inside_at >= self.LEAVE_SECONDS:
                self._hide_panel()
            return

        screen = self._screen_at_hot_corner(pointer)
        if screen is None:
            self._suppress_corner = False
            self._reset_dwell()
            return
        if self._suppress_corner:
            return
        if screen is not self._corner_screen:
            self._corner_screen = screen
            self._corner_started_at = now
            return
        if self._corner_started_at is not None and now - self._corner_started_at >= self.DWELL_SECONDS:
            waited = now - self._corner_started_at
            if waited > self.DWELL_SECONDS + 0.5:
                # Ticks run on the UI thread; a long gap means that thread was busy.
                logger.info(
                    "Development hot corner opened %.0f ms after the pointer reached the corner "
                    "(configured %.0f ms); the UI thread was busy",
                    waited * 1000, self.DWELL_SECONDS * 1000,
                )
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
        # Slide in first (with the last rendered list), then reload the data on the next
        # event-loop turn so loading never delays the panel appearing.
        available = screen.availableGeometry()
        width = min(self.WIDTH, available.width())
        end = QtCore.QRect(available.left(), available.top(), width, available.height())
        start = QtCore.QRect(available.left(), available.top(), 1, available.height())
        self._open_screen = screen
        self._open_geometry = end
        self._content.resize(end.size())
        self._last_inside_at = time.monotonic()
        self._hiding = False
        self.setGeometry(start)
        self.show()
        self._ensure_macos_overlay_level()
        self._animate_geometry(start, end)
        QtCore.QTimer.singleShot(0, self._load_after_show)

    def _load_after_show(self) -> None:
        if not self.isVisible() or self._hiding:
            return
        self._refresh_threads()
        self._refresh_proposals()

    def _set_panel_pinned(self, pinned: bool) -> None:
        self.panel_pin.setToolTip("Allow sidebar to hide" if pinned else "Keep sidebar open")
        self.panel_pin.setAccessibleName(self.panel_pin.toolTip())
        self._last_inside_at = time.monotonic()
        self._view_settings.setValue(PANEL_PINNED_SETTING, bool(pinned))
        # Qt aborts on an exception escaping a slot, so the screen name is best effort.
        screen_name = getattr(self._open_screen, "name", None)
        if pinned and callable(screen_name):
            try:
                self._view_settings.setValue(PANEL_PINNED_SCREEN_SETTING, str(screen_name()))
            except Exception:
                pass

    def _restore_pinned_open(self) -> None:
        """Show the panel pinned open on the screen it was pinned on (primary if that is gone)."""
        if not self._enabled or not self.panel_pin.isChecked() or self.isVisible():
            return
        wanted = str(self._view_settings.value(PANEL_PINNED_SCREEN_SETTING, "", type=str) or "")
        screens = QtGui.QGuiApplication.screens()
        screen = next((item for item in screens if wanted and item.name() == wanted), None)
        screen = screen or QtGui.QGuiApplication.primaryScreen()
        if screen is not None:
            self._show_for_screen(screen)

    def show_for_keyboard(self) -> None:
        if not self._enabled:
            return
        screen = self._open_screen if self.isVisible() else QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
        screen = screen or QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return
        self._keyboard_mode = True
        self._slide.stop()
        self.setWindowFlag(QtCore.Qt.WindowType.WindowDoesNotAcceptFocus, False)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self._show_for_screen(screen)
        if QtGui.QGuiApplication.platformName() == "cocoa":
            try:
                import objc
                from AppKit import NSApplication

                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
                native_view = objc.objc_object(c_void_p=int(self.winId()))
                native_view.window().makeKeyAndOrderFront_(None)
            except Exception:
                logger.debug("Could not activate the native Development sidebar window", exc_info=True)
        self.raise_()
        self.activateWindow()
        self.quick_actions["New thread"].setFocus(QtCore.Qt.FocusReason.ShortcutFocusReason)

    def _restore_hover_focus(self) -> None:
        if self._keyboard_mode:
            self._keyboard_mode = False
            self.setWindowFlag(QtCore.Qt.WindowType.WindowDoesNotAcceptFocus, True)
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)

    def _dismiss_panel(self) -> None:
        self._suppress_corner = True
        self._reset_dwell()
        self._hide_panel(for_pointer=False)

    def _hide_panel(self, *, for_pointer: bool = True) -> None:
        self._close_for_pointer = for_pointer
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
            self._restore_hover_focus()
        else:
            self._resize_title_rows()

    def _sidebar_sections(self, groups: list[dict[str, Any]]):
        threads = [thread for group in groups for thread in group["threads"]]
        auto_threads = sorted(
            (thread for thread in threads if not thread.get("pinned") and is_background_source(thread.get("source_type"))),
            key=thread_order, reverse=True,
        )
        pinned_threads = sorted((thread for thread in threads if thread.get("pinned")), key=thread_order, reverse=True)
        boards = [group for group in groups if group_identity(group) is not None]
        pinned = [group for group in boards if self._is_board_pinned(group)]
        provider_order = {"jira": 0, "trello": 1, "decisions": 2}
        regular = sorted(
            (group for group in boards if not self._is_board_pinned(group)),
            key=lambda group: provider_order.get((group.get("board_key") or "").partition(":")[0], 3),
        )
        loose = next((group for group in groups if group_identity(group) is None), None)
        return (
            ("running", "Running", running_automation_threads(groups), ()),
            ("recent", "Recent", recent_development_threads(groups), ()),
            ("automations", "Automations", auto_threads, ()),
            ("pinned", "Pinned", pinned_threads, pinned),
            ("projects", "Boards", (), regular),
            ("no_project", "No project", [thread for thread in loose["threads"] if not thread.get("pinned")] if loose else (), ()),
        )

    @staticmethod
    def _sidebar_structure(sections):
        return tuple(
            (key, tuple(thread["chat_id"] for thread in threads), tuple(
                (group_identity(group), group["project_id"], group["name"], group.get("has_terminal_commands"),
                 group.get("terminal_running"), tuple(thread["chat_id"] for thread in group["threads"] if not thread.get("pinned")))
                for group in boards
            ))
            for key, _title, threads, boards in sections
        )

    def _refresh_threads(self, groups: list[dict[str, Any]] | None = None) -> None:
        selected_key = self._selected_item_key
        scroll = self.tree.verticalScrollBar().value()
        for key, item in self._section_rows.items():
            # A childless section reports collapsed; keep the remembered choice instead.
            if key in self._section_expanded and item.childCount():
                self._section_expanded[key] = item.isExpanded()
        for item, group in self._project_rows:
            if item.childCount():
                self._project_expanded[group_identity(group)] = item.isExpanded()
        self.tree.clear()
        self._thread_rows.clear()
        self._thread_activity_widgets.clear()
        self._project_rows.clear()
        self._section_rows.clear()
        if groups is None:
            try:
                groups = load_development_groups(self._session_provider)
            except Exception as exc:
                logger.warning("Could not load Development hot-corner list: %s", exc)
                groups = []
        self._sync_pinned_projects(groups)
        sections = self._sidebar_sections(groups)
        self._structure = self._sidebar_structure(sections)
        no_project = next((group for group in groups if group_identity(group) is None), None)
        self._no_project_weekly = int(no_project["weekly_seconds"]) if no_project else 0
        self._no_project_threads = list(no_project["threads"]) if no_project else []

        for section_key, section_title, threads, boards in sections:
            if not threads and not boards and section_key != "projects":
                continue
            section = QtWidgets.QTreeWidgetItem([section_title_text(section_title, self._section_expanded[section_key])])
            section.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable)
            section.setData(0, QtCore.Qt.ItemDataRole.UserRole,
                            {"kind": "section", "key": section_key, "title": section_title})
            section_font = section.font(0)
            section_font.setBold(True)
            section_font.setPixelSize(13)
            section.setFont(0, section_font)
            section.setSizeHint(0, QtCore.QSize(0, self.MENU_ROW_HEIGHT))
            section.setForeground(0, QtGui.QColor("#c6cad1"))
            self.tree.addTopLevelItem(section)
            section.setFirstColumnSpanned(True)
            if section_key == "running":
                section.setForeground(0, QtGui.QColor("#f59e0b"))
            self._section_rows[section_key] = section

            if section_key == "recent":
                for thread in threads[:5]:
                    self._add_recent_row(section, thread, selected_key)
                if len(threads) > 5:
                    more = QtWidgets.QTreeWidgetItem(["Show more"])
                    more.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"kind": "recent_more"})
                    more.setForeground(0, QtGui.QColor("#8f949e"))
                    section.addChild(more)
            else:
                for thread in threads:
                    self._add_recent_row(section, thread, selected_key)
                previous_provider = None
                for group in boards:
                    provider = (group.get("board_key") or "").partition(":")[0]
                    if section_key == "projects" and provider and provider != previous_provider:
                        label = {"jira": "Jira", "trello": "Trello", "decisions": "Local"}.get(provider, provider)
                        provider_item = QtWidgets.QTreeWidgetItem(section, [label])
                        provider_item.setFirstColumnSpanned(True)
                        provider_item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
                        provider_item.setForeground(0, QtGui.QColor("#a6aecb"))
                        previous_provider = provider
                    self._add_project_row(section, group, selected_key)
                if section_key == "projects" and not boards:
                    placeholder = QtWidgets.QTreeWidgetItem(section, ["No ticket boards connected"])
                    placeholder.setFirstColumnSpanned(True)
                    placeholder.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
                    placeholder.setForeground(0, QtGui.QColor("#a6aecb"))
            # Expand after the rows exist so Qt keeps the state; each section is independent.
            section.setExpanded(self._section_expanded[section_key])

        self._rendered_at = time.monotonic()
        self._refresh_elapsed_labels(self._rendered_at)

        self._render_proposals()
        self._resize_title_rows()
        self.tree.verticalScrollBar().setValue(scroll)

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
        from distr.core.incoming.expiry import proposal_expiry, source_instant
        self._proposals = [proposal_expiry(row) for row in self._proposals]
        self._proposal_expiry_timer.stop()
        deadlines = [source_instant(row.get('expires_at')) for row in self._proposals if not row.get('is_expired')]
        now = datetime.now().astimezone()
        waits = [(at - now).total_seconds() for at in deadlines if at and at > now]
        if waits:
            self._proposal_expiry_timer.start(min(2_147_483_647, max(1, int(min(waits) * 1000) + 1)))
        section = self._section_rows.get('proposals')
        if section is None:
            section = QtWidgets.QTreeWidgetItem(['Proposals'])
            section.setData(0, QtCore.Qt.ItemDataRole.UserRole, {'kind': 'section', 'key': 'proposals'})
            section.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable)
            font = section.font(0)
            font.setBold(True)
            font.setPixelSize(13)
            section.setFont(0, font)
            section.setSizeHint(0, QtCore.QSize(0, self.MENU_ROW_HEIGHT))
            section.setForeground(0, QtGui.QColor('#c6cad1'))
            self.tree.addTopLevelItem(section)
            section.setFirstColumnSpanned(True)
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
                self.tree.setItemWidget(item, 0, error_row)
        for proposal in self._proposals:
            item = QtWidgets.QTreeWidgetItem()
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, {'kind': 'proposal', 'proposal': proposal})
            section.addChild(item)
            item.setFirstColumnSpanned(True)
            expired = bool(proposal.get('is_expired'))
            row = _ExpiredProposalCard() if expired else QtWidgets.QWidget()
            box = QtWidgets.QVBoxLayout(row)
            box.setContentsMargins(8, 8, 76 if expired else 8, 8)
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
            project.setObjectName('proposalProjectBadge')
            project.setWordWrap(True)
            project.setStyleSheet('background: #253958; color: #dce0e5; border-radius: 4px; padding: 4px 8px; font-size: 12px;')
            project.ensurePolished()
            project.setToolTip(project.text())
            controls.addWidget(project, 1)
            busy = str(proposal.get('id')) in self._proposal_busy
            approval_managed = (proposal.get('approval_managed') is True
                                or proposal.get('approval_ticket_id') is not None)
            button = QtWidgets.QPushButton('Starting...' if busy else 'Create thread')
            button.setObjectName('developmentProposalStart')
            button.setAccessibleName(f"Create thread for {proposal.get('title') or 'proposal'}")
            button.setEnabled(bool(linked_project) and not self._proposal_busy
                              and not expired and not approval_managed)
            if expired:
                button.setText('Expired')
                button.setToolTip('This proposal expired. It is kept for the record only.')
            elif approval_managed:
                button.setText('Approve in Decisions')
                button.setToolTip('This is a Decisions code change. Approve it in Decisions; Incoming cannot start it.')
            elif not linked_project:
                button.setToolTip('Assign this proposal to a project in Incoming before starting work.')
            button.setStyleSheet('QPushButton { background: #e7e9ed; color: #08090b; border: 0; border-radius: 4px; padding: 6px 8px; font-size: 12px; } QPushButton:disabled { color: #747b87; background: #292e35; }')
            button.clicked.connect(lambda _checked=False, selected=dict(proposal): self._start_proposal(selected))
            controls.addWidget(button)
            box.addLayout(controls)
            self.tree.setItemWidget(item, 0, row)
        self._resize_proposal_rows()

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
        selected_key: tuple[str, int | str | None] | None,
    ) -> None:
        item = QtWidgets.QTreeWidgetItem([
            str(thread["title"]),
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
        self._install_thread_activity(item, thread)
        if selected_key == ("thread", int(thread["chat_id"])):
            self.tree.setCurrentItem(item)

    def _add_project_row(
        self,
        section: QtWidgets.QTreeWidgetItem | None,
        group: dict[str, Any],
        selected_key: tuple[str, int | str | None] | None,
    ) -> None:
        key = group.get("board_key") or ""
        provider = key.partition(":")[0]
        board_name = str(group["name"])
        terminal_running = group.get("terminal_running")
        terminal_action = (
            "terminal-toggle" if terminal_running or group.get("has_terminal_commands")
            else "edit" if provider == "decisions" else ""
        )
        terminal_label = (
            "Stop terminal" if terminal_running else "Start terminal"
        ) if terminal_action == "terminal-toggle" else "Add terminal commands"
        project_item = QtWidgets.QTreeWidgetItem([
            f"{board_name} · Jira" if provider == "jira" and not board_name.lower().endswith("jira") else board_name,
            format_duration(group["weekly_seconds"]),
            "",
            "+" if group_identity(group) is not None else "",
            "▥" if key else "",
            ("■" if terminal_running else "▶") if terminal_action == "terminal-toggle"
            else "✎" if terminal_action else "",
        ])
        project_item.setFlags(
            QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
        )
        board_threads = [thread for thread in group["threads"] if not thread.get("pinned")]
        thread_count = len(board_threads)
        project_item.setIcon(0, project_folder_icon(bool(thread_count)))
        project_item.setToolTip(0, f"{group['name']}: {thread_count} thread{'s' if thread_count != 1 else ''}")
        project_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"kind": "project", **group, "terminal_action": terminal_action})
        project_font = project_item.font(0)
        project_font.setBold(True)
        project_item.setFont(0, project_font)
        project_item.setSizeHint(0, QtCore.QSize(0, 30))
        project_item.setForeground(0, QtGui.QColor("#aeb3bd"))
        project_item.setToolTip(1, "Total development time this week")
        project_item.setTextAlignment(1, QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        project_item.setToolTip(3, f"Create a new thread in {group['name']}")
        project_item.setToolTip(4, f"Open board for {board_name}" if key else "")
        project_item.setToolTip(5, f"{terminal_label} for {board_name}" if terminal_action else "")
        for column in (3, 4, 5):
            project_item.setData(column, QtCore.Qt.ItemDataRole.AccessibleTextRole, project_item.toolTip(column))
            project_item.setTextAlignment(column, QtCore.Qt.AlignmentFlag.AlignCenter)
        if terminal_action == "terminal-toggle":
            project_item.setForeground(5, QtGui.QColor("#fcb071" if terminal_running else "#63e3a1"))
        if section is None:
            self.tree.addTopLevelItem(project_item)
        else:
            section.addChild(project_item)
        self._project_rows.append((project_item, group))
        if selected_key in (("project", group_identity(group)), ("project", group["project_id"])):
            self.tree.setCurrentItem(project_item)
        for thread in board_threads:
            child = QtWidgets.QTreeWidgetItem([
                str(thread["title"]),
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
            self._install_thread_activity(child, thread)
            if selected_key == ("thread", int(thread["chat_id"])):
                self.tree.setCurrentItem(child)
        project_item.setExpanded(
            bool(board_threads) and self._project_expanded.get(
                group_identity(group),
                False,
            )
        )

    def _install_thread_activity(
        self,
        item: QtWidgets.QTreeWidgetItem,
        thread: dict[str, Any],
    ) -> None:
        activity = ThreadActivityWidget(self.tree, item, bool(thread["active"]))
        self.tree.setItemWidget(item, 3, activity)
        self._thread_activity_widgets.append((item, thread, activity))

    def _refresh_thread_activity_widgets(self, now: float) -> None:
        for _item, thread, widget in self._thread_activity_widgets:
            widget.advance(now, bool(thread["active"]))

    def _resize_title_rows(self) -> None:
        width = self.tree.columnWidth(0)
        if width < 60:
            return
        for rows, minimum, padding in ((self._project_rows, 30, 8), (self._thread_rows, 24, 6)):
            for item, _value in rows:
                index = self.tree.indexFromItem(item)
                font = item.font(0).resolve(self.tree.font())
                left = SidebarTitleDelegate.title_left(index)
                _, height = wrapped_title(item.text(0), font, width - left - 6)
                size = QtCore.QSize(0, max(minimum, height + padding))
                if item.sizeHint(0) != size:
                    item.setSizeHint(0, size)
        self._resize_proposal_rows()

    def _resize_proposal_rows(self) -> None:
        section = self._section_rows.get("proposals")
        width = self.tree.viewport().width()
        if section is None or width < 180:
            return
        for index in range(section.childCount()):
            item = section.child(index)
            widget = self.tree.itemWidget(item, 0)
            if widget and widget.layout():
                height = widget.layout().totalHeightForWidth(width)
                size = QtCore.QSize(0, max(72, height + 4))
                if item.sizeHint(0) != size:
                    item.setSizeHint(0, size)

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
        running_loose = sum(1 for thread in self._no_project_threads if thread["time_running"])
        total += int(self._no_project_weekly) + delta * running_loose
        self.footer.setText(f"Total  {format_duration(total)}")

    def _sync_live_state(self, now: float) -> None:
        """Update shared timer state without rebuilding or expanding the tree."""
        self._last_live_sync = now
        try:
            fresh_groups = load_development_groups(self._session_provider)
        except Exception as exc:
            logger.warning("Could not sync Development hot-corner timers: %s", exc)
            return
        self._sync_pinned_projects(fresh_groups)
        sections = self._sidebar_sections(fresh_groups)
        if self._sidebar_structure(sections) != self._structure:
            self._refresh_threads(fresh_groups)
            return
        fresh_by_identity = {group_identity(group): group for group in fresh_groups}
        fresh_threads = {
            int(thread["chat_id"]): thread for group in fresh_groups for thread in group["threads"]
        }
        loose = fresh_by_identity.get(None, {})
        self._no_project_weekly = loose.get("weekly_seconds", 0)
        self._no_project_threads = loose.get("threads", [])
        for item, group in self._project_rows:
            fresh_group = fresh_by_identity[group_identity(group)]
            group.update(fresh_group)
            value = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            value.update(fresh_group)
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, value)
        for item, thread in self._thread_rows:
            fresh_thread = fresh_threads[int(thread["chat_id"])]
            renamed = thread["title"] != fresh_thread["title"]
            thread.update(fresh_thread)
            value = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            value.update(fresh_thread)
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, value)
            item.setData(0, QtCore.Qt.ItemDataRole.AccessibleTextRole, str(thread["title"]))
            item.setToolTip(0, str(thread["title"]))
            if renamed:
                item.setText(0, str(thread["title"]))
                self._title_layout_timer.start(0)
        self._rendered_at = now
        self._refresh_elapsed_labels(now)
        self._refresh_thread_activity_widgets(now)

    def _is_board_pinned(self, group: dict[str, Any]) -> bool:
        if self._uses_shared_pins:
            return group.get("board_key") in self._pinned_board_keys
        return group["project_id"] in self._pinned_project_ids

    def _sync_pinned_projects(self, groups: list[dict[str, Any]]) -> bool:
        if not self._uses_shared_pins:
            return False
        from distr.core.workflow.development_sidebar import load_pinned_boards
        keys = set(load_pinned_boards() or ())
        changed = keys != self._pinned_board_keys
        self._pinned_board_keys = keys
        self._pinned_project_ids = {group['project_id'] for group in groups if group.get('board_key') in keys and group['project_id'] is not None}
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
            if column == 3 and group_identity(value) is not None:
                self._create_project_thread(value)
            elif column in (0, 4) and value.get("board_key"):
                provider, identity = value["board_key"].split(":", 1)
                self._open_path(f"/development/boards/{provider}/{identity}/kanban/")
            elif column == 5 and value.get("board_key") and value.get("terminal_action"):
                self._open_board_action(value["board_key"], value["terminal_action"])
        elif value.get("kind") == "thread":
            self.tree.setCurrentItem(item)

    def _activate_tree_row(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        value = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if isinstance(value, dict) and value.get("kind") == "thread":
            self._open_thread_index(self.tree.indexFromItem(item))
        else:
            self._handle_item_click(item, column)

    def _open_thread_index(self, index: QtCore.QModelIndex) -> None:
        item = self.tree.itemFromIndex(index)
        value = item.data(0, QtCore.Qt.ItemDataRole.UserRole) if item else None
        if isinstance(value, dict) and value.get("kind") == "thread":
            # Open the Development thread itself, not the plain chat view.
            # /agents/?id= lands on a different page, which is why the panel and
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
                if key in SECTION_SETTINGS:
                    self._view_settings.setValue(SECTION_SETTINGS[key], item.isExpanded())
                if value.get("title"):
                    item.setText(0, section_title_text(str(value["title"]), item.isExpanded()))
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
            self._project_expanded[group_identity(value)] = expanded

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
        if value["kind"] == "project" and group_identity(value) is not None:
            project_id = value.get("project_id")
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
                    ("Unpin Board" if self._is_board_pinned(value) else "Pin Board", "pin"),
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
                        menu.addAction(label, lambda: self._toggle_project_pin(project_id, board_key))
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
        if self._context_menu is not None:
            self._context_menu.close()
            self._context_menu.deleteLater()
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

    def _toggle_project_pin(self, project_id: int | None, board_key: str | None = None) -> None:
        pinning = project_id not in self._pinned_project_ids
        if self._uses_shared_pins:
            from distr.core.workflow.development_sidebar import set_board_pin

            groups = [group for _item, group in self._project_rows]
            key = board_key or next((group.get('board_key') for group in groups if group['project_id'] == project_id), None)
            if not key:
                return
            try:
                keys = set_board_pin(key, key not in self._pinned_board_keys)
            except Exception as exc:
                logger.warning("Could not save Development pin for board %s: %s", key, exc)
                return
            self._pinned_board_keys = set(keys)
        elif project_id is not None:
            if pinning:
                self._pinned_project_ids.add(project_id)
            else:
                self._pinned_project_ids.discard(project_id)
            if self._pin_saver is not None:
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
                project_id=group["project_id"],
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
            self._selected_item_key = ("project", group_identity(value))
        for _item, _thread, widget in self._thread_activity_widgets:
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
