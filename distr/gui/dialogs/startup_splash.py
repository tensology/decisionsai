"""Brief splash while the .app finishes booting — not an installer or permission UI."""

from __future__ import annotations

from PyQt6 import QtCore, QtWidgets


class StartupSplash(QtWidgets.QWidget):
    def __init__(self, message: str = "Starting Decisions…"):
        super().__init__(
            None,
            QtCore.Qt.WindowType.SplashScreen | QtCore.Qt.WindowType.FramelessWindowHint,
        )
        self.setObjectName("startupSplash")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(36, 28, 36, 28)

        label = QtWidgets.QLabel(message)
        label.setObjectName("startupSplashLabel")
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        layout.addWidget(label)

        self.setStyleSheet(
            """
            QWidget#startupSplash {
                background-color: #343541;
                border-radius: 10px;
            }
            QLabel#startupSplashLabel {
                color: #ececf1;
                font-size: 15px;
                background: transparent;
                border: none;
                padding: 0;
            }
            """
        )
        self.adjustSize()
        screen = QtWidgets.QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(
                geo.center().x() - self.width() // 2,
                geo.center().y() - self.height() // 2,
            )


def show_startup_splash(message: str = "Starting Decisions…") -> StartupSplash:
    splash = StartupSplash(message=message)
    splash.show()
    QtWidgets.QApplication.processEvents()
    return splash
