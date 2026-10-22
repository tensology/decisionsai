import json
import os
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core import utils as core_utils
from distr.core.db import Base, Chat, Settings
from distr.core.db.kanban import KanbanBoard
from distr.core.db.projects import Project
from distr.core.db.workflow import DevelopmentTimeEntry, DevelopmentWorkItem
from distr.gui.development_hot_corner import (
    DevelopmentHotCornerPanel,
    ThreadTitleWidget,
    format_duration,
    is_top_left_corner,
    load_development_groups,
    load_pinned_projects,
    recent_development_threads,
    weekly_entry_seconds,
)


def _app() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _project_item(panel: DevelopmentHotCornerPanel, project_id: int):
    return next(item for item, group in panel._project_rows if group["project_id"] == project_id)


def test_corner_detection_uses_each_displays_own_origin() -> None:
    left_screen = QtCore.QRect(-1920, 0, 1920, 1080)
    upper_screen = QtCore.QRect(0, -1200, 1920, 1200)

    assert is_top_left_corner(QtCore.QPoint(-1918, 2), left_screen)
    assert is_top_left_corner(QtCore.QPoint(2, -1198), upper_screen)
    assert not is_top_left_corner(QtCore.QPoint(-1900, 2), left_screen)


def test_two_and_a_half_second_dwell_opens_once_and_leaving_resets(monkeypatch) -> None:
    app = _app()
    panel = DevelopmentHotCornerPanel(enabled=False)
    screen = object()
    shown = []
    times = iter((10.0, 12.4, 12.5, 20.0, 24.0))
    panel._enabled = True
    monkeypatch.setattr(
        "distr.gui.development_hot_corner.time.monotonic", lambda: next(times)
    )
    monkeypatch.setattr(panel, "_screen_at_hot_corner", lambda _pointer: screen)
    monkeypatch.setattr(panel, "_show_for_screen", shown.append)

    panel._tick()
    panel._tick()
    assert shown == []
    panel._tick()
    assert shown == [screen]

    monkeypatch.setattr(panel, "_screen_at_hot_corner", lambda _pointer: None)
    panel._tick()
    assert panel._corner_started_at is None
    monkeypatch.setattr(panel, "_screen_at_hot_corner", lambda _pointer: screen)
    panel._tick()
    assert shown == [screen]

    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_panel_does_not_accept_focus_and_thread_click_only_selects(monkeypatch) -> None:
    app = _app()
    panel = DevelopmentHotCornerPanel(enabled=False)
    monkeypatch.setattr(
        "distr.gui.development_hot_corner.load_development_groups",
        lambda _provider: [
            {
                "project_id": 9,
                "board_key": "decisions:7",
                "name": "Decisions AI",
                "weekly_seconds": 90,
                "threads": [
                    {
                        "chat_id": 42,
                        "title": "Hot corner drawer",
                        "status": "running",
                        "active": True,
                        "elapsed_seconds": 65,
                        "weekly_seconds": 90,
                        "time_running": True,
                    }
                ],
            }
        ],
    )

    panel._refresh_threads()
    project = _project_item(panel, 9)
    thread = project.child(0)
    panel.tree.setCurrentItem(thread)
    app.processEvents()

    assert panel.windowFlags() & QtCore.Qt.WindowType.WindowDoesNotAcceptFocus
    assert panel.testAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert project.flags() & QtCore.Qt.ItemFlag.ItemIsSelectable
    assert panel.selected_chat_id == 42
    title_widget = panel.tree.itemWidget(thread, 0)
    assert isinstance(title_widget, ThreadTitleWidget)
    assert thread.text(0) == ""
    assert thread.data(0, QtCore.Qt.ItemDataRole.AccessibleTextRole) == "Hot corner drawer"
    assert title_widget.title == "Hot corner drawer"
    assert title_widget.active is True
    assert thread.sizeHint(0).height() == 24
    assert panel.tree.indentation() == 8
    assert not panel.tree.wordWrap()
    assert project.isExpanded()
    assert thread.text(1) == "00:01:05"
    assert panel.footer.text() == "Total  00:01:30"
    assert panel.footer.toolTip() == "This week"

    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_loader_groups_only_development_threads_by_authoritative_project() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        first = Project(name="First", position=1)
        second = Project(name="Second", position=2)
        db.add_all((first, second))
        db.flush()
        active = Chat(
            title="Active build",
            project_id=first.id,
            params=json.dumps({"development": {"execution": {"status": "running"}}}),
        )
        ordinary = Chat(title="Ordinary chat", project_id=first.id, params="{}")
        reassigned = Chat(title="Board-owned", project_id=first.id, params="{}")
        db.add_all((active, ordinary, reassigned))
        db.flush()
        board = KanbanBoard(name="Second board", default_project_id=second.id)
        db.add(board)
        db.flush()
        board_id = board.id
        work_item = DevelopmentWorkItem(
                chat_id=reassigned.id,
                identity_key="project:second:thread",
                project_id=second.id,
                time_accumulated_seconds=300,
                time_started_at=datetime(2026, 10, 12, 8, 0),
                time_paused=False,
        )
        db.add(work_item)
        db.flush()
        db.add(DevelopmentTimeEntry(
            work_item_id=work_item.id,
            chat_id=reassigned.id,
            started_at=datetime(2026, 10, 12, 7, 0),
            ended_at=datetime(2026, 10, 12, 7, 5),
            seconds=300,
        ))
        db.commit()

    groups = load_development_groups(Session, now=datetime(2026, 10, 12, 8, 2))

    assert [group["name"] for group in groups] == ["First", "Second"]
    assert [thread["title"] for thread in groups[0]["threads"]] == ["Active build"]
    assert groups[0]["threads"][0]["status"] == "running"
    assert [thread["title"] for thread in groups[1]["threads"]] == ["Board-owned"]
    assert groups[1]["board_key"] == f"decisions:{board_id}"
    assert groups[1]["weekly_seconds"] == 420
    assert groups[1]["threads"][0]["elapsed_seconds"] == 420


def test_reset_entry_clears_thread_contribution_to_project_total() -> None:
    before = DevelopmentTimeEntry(id=1, chat_id=7, seconds=300, source="play")
    reset = DevelopmentTimeEntry(id=2, chat_id=7, seconds=0, source="reset")
    after = DevelopmentTimeEntry(id=3, chat_id=7, seconds=45, source="play")

    assert weekly_entry_seconds([before, reset]) == 0
    assert weekly_entry_seconds([before, reset, after]) == 45


def test_pinned_project_ids_round_trip_through_settings(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        db.add(Settings())
        db.commit()
    monkeypatch.setattr(core_utils, "Session", session_factory)

    core_utils.save_settings_to_db({"development_hot_corner_pinned_project_ids": [2, 7]})

    assert core_utils.load_settings_from_db()["development_hot_corner_pinned_project_ids"] == [2, 7]


def test_load_pinned_projects_normalizes_saved_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"development_hot_corner_pinned_project_ids": [7, 2, 7]},
    )

    assert load_pinned_projects() == [2, 7]


def test_panel_loads_pins_directly_instead_of_relying_on_startup_settings(monkeypatch) -> None:
    app = _app()
    monkeypatch.setattr(
        "distr.gui.development_hot_corner.load_pinned_projects",
        lambda: [2, 7],
    )

    panel = DevelopmentHotCornerPanel(enabled=False)

    assert panel._pinned_project_ids == {2, 7}
    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_recent_threads_rank_active_first_then_latest_and_show_five(monkeypatch) -> None:
    app = _app()
    opened = []
    threads = [
        {
            "chat_id": chat_id,
            "title": f"Thread {chat_id}",
            "modified_at": float(chat_id),
            "source_type": "prompt",
            "status": "running" if chat_id == 1 else "idle",
            "active": chat_id == 1,
            "elapsed_seconds": chat_id,
            "weekly_seconds": chat_id,
            "time_running": False,
        }
        for chat_id in range(1, 7)
    ]
    groups = [{
        "project_id": 9,
        "board_key": "decisions:7",
        "name": "Recent project",
        "weekly_seconds": sum(thread["weekly_seconds"] for thread in threads),
        "threads": threads,
    }]
    monkeypatch.setattr(
        "distr.gui.development_hot_corner.load_development_groups",
        lambda _provider: groups,
    )
    panel = DevelopmentHotCornerPanel(enabled=False, open_path=opened.append)
    panel._refresh_threads()

    assert [thread["chat_id"] for thread in recent_development_threads(groups)] == [1, 6, 5, 4, 3, 2]
    recent = panel._section_rows["recent"]
    assert not recent.isExpanded()
    assert panel.tree.indexOfTopLevelItem(panel._section_rows["projects"]) < panel.tree.indexOfTopLevelItem(recent)
    assert recent.childCount() == 6
    assert recent.child(0).data(0, QtCore.Qt.ItemDataRole.UserRole)["chat_id"] == 1
    assert recent.child(5).text(0) == "Show more"
    panel._handle_item_click(recent.child(5), 0)
    assert opened == ["/development/?recent=1"]

    panel._handle_item_click(recent, 0)
    assert recent.isExpanded()
    panel._handle_item_click(recent, 0)
    assert not recent.isExpanded()

    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_board_actions_open_and_create_in_the_selected_project(monkeypatch) -> None:
    app = _app()
    opened = []
    created = []
    panel = DevelopmentHotCornerPanel(
        enabled=False,
        open_path=opened.append,
        thread_factory=lambda **kwargs: created.append(kwargs) or 77,
    )
    monkeypatch.setattr(
        "distr.gui.development_hot_corner.load_development_groups",
        lambda _provider: [{
            "project_id": 9,
            "board_key": "decisions:7",
            "name": "Decisions AI",
            "weekly_seconds": 0,
            "threads": [],
        }],
    )

    panel._refresh_threads()
    project = _project_item(panel, 9)
    panel.tree.setCurrentItem(project)
    panel._handle_item_click(project, 0)
    assert opened == []
    panel._handle_item_click(project, 3)
    panel._handle_item_click(project, 2)

    assert opened == ["/development/boards/decisions/7/", "/chat/?id=77"]
    assert created == [{
        "title": "New Development thread",
        "project_id": 9,
        "source_type": "prompt",
        "board_key": "decisions:7",
        "board_provider": "decisions",
    }]
    assert format_duration(3661) == "01:01:01"

    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_thread_title_opens_and_timer_controls_use_shared_actions(monkeypatch) -> None:
    app = _app()
    opened = []
    updates = []
    panel = DevelopmentHotCornerPanel(
        enabled=False,
        open_path=opened.append,
        time_updater=lambda chat_id, action: updates.append((chat_id, action)),
    )
    monkeypatch.setattr(
        "distr.gui.development_hot_corner.load_development_groups",
        lambda _provider: [{
            "project_id": 9,
            "board_key": "decisions:7",
            "name": "A project title that must remain fully visible",
            "weekly_seconds": 65,
            "threads": [{
                "chat_id": 42,
                "title": "Timer synchronization",
                "status": "running",
                "active": True,
                "elapsed_seconds": 65,
                "weekly_seconds": 65,
                "time_running": True,
            }],
        }],
    )

    panel._refresh_threads()
    project = _project_item(panel, 9)
    thread = project.child(0)
    panel.resize(360, 600)
    panel.show()
    app.processEvents()
    panel._show_context_menu(panel.tree.visualItemRect(thread).center())
    timer_actions = panel._context_menu.actions()
    assert timer_actions[0].text() == "Timer"
    assert timer_actions[0].isSeparator()
    assert [action.text() for action in timer_actions[1:]] == [
        "Stop timer",
        "Stop and reset timer",
    ]
    panel._context_menu.close()
    panel._handle_item_click(thread, 0)
    panel._update_thread_time(42, "pause")
    panel._update_thread_time(42, "reset")
    project = _project_item(panel, 9)
    panel.tree.setCurrentItem(project)
    app.processEvents()

    assert opened == ["/chat/?id=42"]
    assert updates == [(42, "pause"), (42, "reset")]
    assert panel.selected_chat_id is None
    assert project.sizeHint(0).height() > 30

    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_long_thread_titles_use_compact_marquee_widget_without_idle_bullet(monkeypatch) -> None:
    app = _app()
    long_title = "A very long development thread title that should remain on one compact row"
    monkeypatch.setattr(
        "distr.gui.development_hot_corner.load_development_groups",
        lambda _provider: [{
            "project_id": 9,
            "board_key": "decisions:7",
            "name": "Compact project",
            "weekly_seconds": 0,
            "threads": [{
                "chat_id": 42,
                "title": long_title,
                "status": "idle",
                "active": False,
                "elapsed_seconds": 0,
                "weekly_seconds": 0,
                "time_running": False,
            }],
        }],
    )
    panel = DevelopmentHotCornerPanel(enabled=False)
    panel._refresh_threads()
    thread = _project_item(panel, 9).child(0)
    title_widget = panel.tree.itemWidget(thread, 0)

    assert thread.text(0) == ""
    assert thread.data(0, QtCore.Qt.ItemDataRole.AccessibleTextRole) == long_title
    assert isinstance(title_widget, ThreadTitleWidget)
    assert title_widget.title == long_title
    assert title_widget.active is False
    assert title_widget.accessibleName() == long_title
    assert thread.sizeHint(0).height() == 24

    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_pinned_projects_move_to_own_section_and_collapse_state_survives(monkeypatch) -> None:
    app = _app()
    saved = []
    groups = [
        {
            "project_id": 1,
            "board_key": "decisions:1",
            "name": "First",
            "weekly_seconds": 0,
            "threads": [],
        },
        {
            "project_id": 2,
            "board_key": "decisions:2",
            "name": "Second",
            "weekly_seconds": 0,
            "threads": [],
        },
    ]
    monkeypatch.setattr(
        "distr.gui.development_hot_corner.load_development_groups",
        lambda _provider: [dict(group) for group in groups],
    )
    panel = DevelopmentHotCornerPanel(enabled=False, pin_saver=saved.append)

    panel._refresh_threads()
    projects = panel._section_rows["projects"]
    projects.setExpanded(False)
    panel._toggle_project_pin(2)

    assert saved == [[2]]
    assert panel.tree.topLevelItem(0).text(0) == "Pinned"
    assert _project_item(panel, 2).text(0) == "Second"
    assert _project_item(panel, 2).parent() is None
    assert panel._section_rows["projects"].child(0).text(0) == "First"
    assert not panel._section_rows["projects"].isExpanded()

    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_pinned_section_remains_visible_when_no_projects_are_pinned(monkeypatch) -> None:
    app = _app()
    monkeypatch.setattr(
        "distr.gui.development_hot_corner.load_development_groups",
        lambda _provider: [{
            "project_id": 1,
            "board_key": "decisions:1",
            "name": "Only board",
            "weekly_seconds": 0,
            "threads": [],
        }],
    )
    panel = DevelopmentHotCornerPanel(enabled=False)

    panel._refresh_threads()

    assert panel.tree.topLevelItem(0).text(0) == "Pinned"
    assert panel._section_rows["pinned"].childCount() == 0
    assert panel.tree.indexOfTopLevelItem(panel._section_rows["pinned"]) < panel.tree.indexOfTopLevelItem(panel._section_rows["projects"])

    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_project_selection_survives_refresh(monkeypatch) -> None:
    app = _app()
    groups = [{
        "project_id": 9,
        "board_key": "decisions:7",
        "name": "Selected project",
        "weekly_seconds": 0,
        "threads": [],
    }]
    monkeypatch.setattr(
        "distr.gui.development_hot_corner.load_development_groups",
        lambda _provider: groups,
    )
    panel = DevelopmentHotCornerPanel(enabled=False)

    panel._refresh_threads()
    panel.tree.setCurrentItem(_project_item(panel, 9))
    panel._refresh_threads()

    selected = panel.tree.currentItem().data(0, QtCore.Qt.ItemDataRole.UserRole)
    assert selected["kind"] == "project"
    assert selected["project_id"] == 9

    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_quick_action_icons_open_development_areas() -> None:
    app = _app()
    opened = []
    panel = DevelopmentHotCornerPanel(enabled=False, open_path=opened.append)
    panel.resize(360, 560)
    panel.show()
    app.processEvents()

    for button in panel.quick_actions.values():
        button.click()

    assert list(panel.quick_actions) == [
        "New thread",
        "Planning",
        "Incoming",
        "Scheduled",
        "Workflows",
        "Terminals",
        "Reports",
    ]
    assert opened == [
        "/development/new/",
        "/development/plan/",
        "/development/incoming/",
        "/development/automations/",
        "/development/workflows/",
        "/development/terminals/",
        "/development/reports/",
    ]
    title = panel.findChild(QtWidgets.QLabel, "developmentDrawerTitle")
    new_thread = panel.quick_actions["New thread"]
    planning = panel.quick_actions["Planning"]
    assert new_thread.mapTo(panel, QtCore.QPoint()).y() == title.mapTo(panel, QtCore.QPoint()).y()
    assert planning.mapTo(panel, QtCore.QPoint()).x() < panel.footer.mapTo(panel, QtCore.QPoint()).x()
    assert abs(
        planning.mapTo(panel, QtCore.QPoint()).y() + planning.height() / 2
        - (panel.footer.mapTo(panel, QtCore.QPoint()).y() + panel.footer.height() / 2)
    ) <= 1

    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_default_route_is_visible_and_cog_opens_canonical_editor(monkeypatch) -> None:
    app = _app()
    opened = []
    routes = iter((("ollama", "muse-glimmer"), ("Kilo", "kilo-auto/free")))
    monkeypatch.setattr(
        "distr.gui.development_hot_corner.load_development_default_route",
        lambda: next(routes),
    )
    panel = DevelopmentHotCornerPanel(enabled=False, open_path=opened.append)

    assert panel.default_route.text() == "ollama / muse-glimmer"
    assert panel.default_route.toolTip() == "Default medium-complexity Development harness and model"
    panel._refresh_default_route()
    assert panel.default_route.text() == "Kilo / kilo-auto/free"
    edit = panel.findChild(QtWidgets.QToolButton, "developmentDrawerDefaultRouteEdit")
    edit.click()
    assert opened == ["/settings?llm=workflow#llms"]

    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_panel_animation_stays_inside_the_selected_screen(monkeypatch) -> None:
    app = _app()
    panel = DevelopmentHotCornerPanel(enabled=False)
    monkeypatch.setattr(panel, "_refresh_threads", lambda: None)
    monkeypatch.setattr(panel, "_ensure_macos_overlay_level", lambda: None)
    geometries = []
    monkeypatch.setattr(panel, "_animate_geometry", lambda start, end: geometries.append((start, end)))
    screen = type("Screen", (), {"availableGeometry": lambda self: QtCore.QRect(1920, 20, 1440, 900)})()

    panel._show_for_screen(screen)

    start, end = geometries[0]
    assert start.left() == end.left() == 1920
    assert start.width() == 1
    assert end.width() == 360

    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_visible_panel_does_not_rebuild_while_open(monkeypatch) -> None:
    app = _app()
    panel = DevelopmentHotCornerPanel(enabled=False)
    refreshed = []
    monkeypatch.setattr(panel, "isVisible", lambda: True)
    monkeypatch.setattr(panel, "_pointer_inside", lambda _pointer: True)
    monkeypatch.setattr(panel, "_refresh_threads", lambda: refreshed.append(True))
    monkeypatch.setattr(panel, "_refresh_elapsed_labels", lambda _now: None)
    times = iter((10.0, 12.5, 20.0))
    monkeypatch.setattr("distr.gui.development_hot_corner.time.monotonic", lambda: next(times))
    panel._enabled = True

    panel._tick()
    panel._tick()
    panel._tick()

    assert refreshed == []

    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_live_timer_sync_updates_rows_without_resetting_collapsed_state(monkeypatch) -> None:
    app = _app()
    snapshot = [{
        "project_id": 9,
        "board_key": "decisions:7",
        "name": "Selected project",
        "weekly_seconds": 65,
        "threads": [{
            "chat_id": 42,
            "title": "Shared timer",
            "status": "idle",
            "active": False,
            "elapsed_seconds": 65,
            "weekly_seconds": 65,
            "time_running": False,
        }],
    }]
    monkeypatch.setattr(
        "distr.gui.development_hot_corner.load_development_groups",
        lambda _provider: snapshot,
    )
    panel = DevelopmentHotCornerPanel(enabled=False)
    panel._refresh_threads()
    section = panel._section_rows["projects"]
    project = _project_item(panel, 9)
    section.setExpanded(False)
    project.setExpanded(False)

    snapshot[0]["weekly_seconds"] = 90
    snapshot[0]["threads"][0].update(
        elapsed_seconds=90,
        weekly_seconds=90,
        time_running=True,
    )
    panel._sync_live_state(10.0)

    assert not section.isExpanded()
    assert not project.isExpanded()
    assert project.child(0).text(1) == "00:01:30"
    assert project.child(0).data(0, QtCore.Qt.ItemDataRole.UserRole)["time_running"] is True
    assert panel.footer.text() == "Total  00:01:30"

    panel.close()
    panel.deleteLater()
    app.processEvents()

