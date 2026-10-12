import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat
from distr.core.db.projects import Project
from distr.core.db.workflow import DevelopmentWorkItem
from distr.gui.development_hot_corner import (
    DevelopmentHotCornerPanel,
    is_top_left_corner,
    load_development_groups,
)


def _app() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_corner_detection_uses_each_displays_own_origin() -> None:
    left_screen = QtCore.QRect(-1920, 0, 1920, 1080)
    upper_screen = QtCore.QRect(0, -1200, 1920, 1200)

    assert is_top_left_corner(QtCore.QPoint(-1918, 2), left_screen)
    assert is_top_left_corner(QtCore.QPoint(2, -1198), upper_screen)
    assert not is_top_left_corner(QtCore.QPoint(-1900, 2), left_screen)


def test_three_second_dwell_opens_once_and_leaving_resets(monkeypatch) -> None:
    app = _app()
    panel = DevelopmentHotCornerPanel(enabled=False)
    screen = object()
    shown = []
    times = iter((10.0, 12.9, 13.0, 20.0, 24.0))
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
                "name": "Decisions AI",
                "threads": [
                    {"chat_id": 42, "title": "Hot corner drawer", "status": "running"}
                ],
            }
        ],
    )

    panel._refresh_threads()
    project = panel.tree.topLevelItem(0)
    thread = project.child(0)
    panel.tree.setCurrentItem(thread)
    app.processEvents()

    assert panel.windowFlags() & QtCore.Qt.WindowType.WindowDoesNotAcceptFocus
    assert panel.testAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert not project.flags() & QtCore.Qt.ItemFlag.ItemIsSelectable
    assert panel.selected_chat_id == 42
    assert thread.text(0).startswith("●")

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
        db.add(
            DevelopmentWorkItem(
                chat_id=reassigned.id,
                identity_key="project:second:thread",
                project_id=second.id,
            )
        )
        db.commit()

    groups = load_development_groups(Session)

    assert [group["name"] for group in groups] == ["First", "Second"]
    assert [thread["title"] for thread in groups[0]["threads"]] == ["Active build"]
    assert groups[0]["threads"][0]["status"] == "running"
    assert [thread["title"] for thread in groups[1]["threads"]] == ["Board-owned"]

