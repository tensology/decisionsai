from distr.gui.oracle.lifecycle import (
    _OraclePositionedMessageBox,
    _center_dialog_position,
)


def test_center_dialog_position_uses_target_screen_origin():
    assert _center_dialog_position(
        screen_geometry=(1920, 0, 1440, 900),
        dialog_size=(420, 180),
    ) == (2430, 360)


def test_center_dialog_position_clamps_dialog_larger_than_screen():
    assert _center_dialog_position(
        screen_geometry=(-1280, 120, 800, 600),
        dialog_size=(1000, 700),
    ) == (-1280, 120)


def test_quit_dialog_is_centered_on_oracle_screen_not_on_oracle_window(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtCore, QtWidgets

    from distr.gui.oracle.lifecycle import LifecycleMixin

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    class Oracle(LifecycleMixin, QtWidgets.QWidget):
        pass

    class Screen:
        def availableGeometry(self):
            return QtCore.QRect(1920, 100, 1400, 900)

    oracle = Oracle()
    oracle.resize(80, 80)
    oracle.move(1940, 120)
    oracle._oracle_target_screen = lambda: Screen()

    class Dialog:
        def __init__(self):
            self.position = None

        def adjustSize(self):
            return None

        def windowHandle(self):
            return None

        def width(self):
            return 400

        def height(self):
            return 200

        def move(self, x, y):
            self.position = (x, y)

    dialog = Dialog()

    oracle._position_dialog_on_oracle_screen(dialog)

    assert dialog.position == (2420, 450)
    oracle.close()


def test_oracle_target_screen_uses_greatest_window_overlap(monkeypatch):
    from PyQt6 import QtCore

    import distr.gui.oracle.lifecycle as lifecycle

    class Screen:
        def __init__(self, geometry):
            self._geometry = geometry

        def geometry(self):
            return self._geometry

    left_screen = Screen(QtCore.QRect(-1920, 0, 1920, 1080))
    middle_screen = Screen(QtCore.QRect(0, 0, 1920, 1080))

    class App:
        def screens(self):
            return [middle_screen, left_screen]

    class Application:
        @staticmethod
        def instance():
            return App()

        @staticmethod
        def screenAt(_center):
            return middle_screen

    class Oracle(lifecycle.LifecycleMixin):
        def frameGeometry(self):
            return QtCore.QRect(-300, 200, 500, 300)

    monkeypatch.setattr(lifecycle, "QApplication", Application)

    assert Oracle()._oracle_target_screen() is left_screen


def test_exit_confirmation_uses_one_modal_presentation(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtWidgets

    from distr.gui.oracle.lifecycle import LifecycleMixin

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    class Oracle(LifecycleMixin, QtWidgets.QWidget):
        pass

    oracle = Oracle()
    screen_geometry = app.primaryScreen().availableGeometry()
    oracle.resize(120, 120)
    oracle.move(screen_geometry.center() - oracle.rect().center())
    oracle.show()
    app.processEvents()
    observed = {"show_calls": 0, "exec_calls": 0}

    def tracked_show(message_box):
        observed["show_calls"] += 1

    def fake_exec(message_box):
        observed["exec_calls"] += 1
        return QtWidgets.QMessageBox.StandardButton.No

    monkeypatch.setattr(QtWidgets.QMessageBox, "show", tracked_show)
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", fake_exec)
    oracle.exit_app(confirm=True)

    assert observed == {"show_calls": 0, "exec_calls": 1}
    oracle.close()
    app.processEvents()


def test_positioned_message_box_places_itself_from_show_event(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    positioned_while_visible = []
    message_box = _OraclePositionedMessageBox(
        lambda dialog: positioned_while_visible.append(dialog.isVisible())
    )
    message_box.show()
    app.processEvents()

    assert positioned_while_visible
    assert positioned_while_visible[0] is True
    message_box.close()
    app.processEvents()
