"""macOS desktop permission setup dialog — shown on .app launch when checks fail."""

from __future__ import annotations

import logging

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt

from distr.core.macos_permissions import (
    collect_permission_report,
    mark_permissions_setup_dismissed,
    open_privacy_pane,
    permissions_setup_needed,
    request_python_permission,
    user_facing_permission_failures,
)

logger = logging.getLogger(__name__)

_DIALOG_STYLE = """
    QDialog {
        background-color: #343541;
        color: #ececf1;
    }
    QLabel {
        color: #ececf1;
        background: transparent;
        border: none;
    }
    QPushButton {
        background-color: #40414f;
        color: #ececf1;
        border: 1px solid #565869;
        border-radius: 4px;
        padding: 6px 12px;
    }
    QPushButton:hover {
        background-color: #565869;
    }
    QPushButton#primaryBtn {
        background-color: #007bff;
        border-color: #007bff;
    }
    QPushButton#primaryBtn:hover {
        background-color: #0069d9;
    }
    QFrame#itemFrame {
        background-color: #40414f;
        border: 1px solid #565869;
        border-radius: 6px;
    }
"""


class MacOSPermissionsDialog(QtWidgets.QDialog):
    """Guide the user through macOS privacy toggles for desktop tools."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Allow Decisions on this Mac")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setMinimumHeight(320)
        self.setStyleSheet(_DIALOG_STYLE)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )

        self._report: dict = {}
        self._items_layout: QtWidgets.QVBoxLayout | None = None

        root = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "Decisions needs macOS permission for screenshots, clicks, and voice. "
            "Enable Decisions in System Settings, then click Check again. "
            "You can continue without voice if you do not use it."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._items_host = QtWidgets.QWidget()
        self._items_layout = QtWidgets.QVBoxLayout(self._items_host)
        self._items_layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(self._items_host)
        root.addWidget(scroll, stretch=1)

        actions = QtWidgets.QHBoxLayout()
        self._check_btn = QtWidgets.QPushButton("Check again")
        self._check_btn.setObjectName("primaryBtn")
        self._check_btn.clicked.connect(self._reload)
        actions.addWidget(self._check_btn)

        self._open_all_btn = QtWidgets.QPushButton("Open System Settings")
        self._open_all_btn.clicked.connect(self._open_all_missing)
        actions.addWidget(self._open_all_btn)

        actions.addStretch()

        continue_btn = QtWidgets.QPushButton("Continue")
        continue_btn.clicked.connect(self._continue)
        actions.addWidget(continue_btn)
        root.addLayout(actions)

        self._reload()

    def _continue(self) -> None:
        mark_permissions_setup_dismissed()
        self.accept()

    def _clear_items(self) -> None:
        if not self._items_layout:
            return
        while self._items_layout.count():
            child = self._items_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def _reload(self) -> None:
        """Refresh checks and close automatically when setup is complete."""
        self._report = collect_permission_report(start_sidecar=True)
        if not permissions_setup_needed(self._report):
            mark_permissions_setup_dismissed()
            self.accept()
            return

        self._clear_items()
        failures = user_facing_permission_failures(self._report)
        if not failures:
            mark_permissions_setup_dismissed()
            self.accept()
            return

        for item in failures:
            self._items_layout.addWidget(self._build_item_row(item))
        self._items_layout.addStretch()

    def _build_item_row(self, item: dict) -> QtWidgets.QFrame:
        frame = QtWidgets.QFrame()
        frame.setObjectName("itemFrame")
        layout = QtWidgets.QVBoxLayout(frame)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel(item.get("title") or item.get("id") or "Permission")
        title.setStyleSheet("font-weight: 600; border: none; background: transparent;")
        header.addWidget(title, stretch=1)
        status = QtWidgets.QLabel("Needs setup")
        status.setStyleSheet("color: #fbbf24; border: none; background: transparent;")
        header.addWidget(status)
        layout.addLayout(header)

        if item.get("detail"):
            detail = QtWidgets.QLabel(str(item["detail"]))
            detail.setWordWrap(True)
            detail.setStyleSheet("color: #9ca3af; font-size: 13px; border: none; background: transparent;")
            layout.addWidget(detail)

        if item.get("enable_in_settings"):
            hint = QtWidgets.QLabel(str(item["enable_in_settings"]))
            hint.setWordWrap(True)
            hint.setStyleSheet("color: #9ca3af; font-size: 12px; border: none; background: transparent;")
            layout.addWidget(hint)

        row = QtWidgets.QHBoxLayout()
        pane = item.get("settings_pane")
        if pane:
            open_btn = QtWidgets.QPushButton("Open Settings")
            open_btn.clicked.connect(lambda _=False, p=pane: open_privacy_pane(str(p)))
            row.addWidget(open_btn)
        if item.get("can_prompt") and item.get("prompt_target"):
            prompt_btn = QtWidgets.QPushButton("Show permission prompt")
            target = str(item["prompt_target"])

            def _prompt(t=target) -> None:
                request_python_permission(t)
                self._reload()

            prompt_btn.clicked.connect(_prompt)
            row.addWidget(prompt_btn)
        row.addStretch()
        layout.addLayout(row)

        return frame

    def _open_all_missing(self) -> None:
        opened: set[str] = set()
        for item in user_facing_permission_failures(self._report):
            panes = item.get("settings_panes") or []
            if not panes and item.get("settings_pane"):
                panes = [item["settings_pane"]]
            for pane in panes:
                key = str(pane)
                if key not in opened:
                    open_privacy_pane(key)
                    opened.add(key)


def offer_macos_permissions_setup(parent=None) -> bool:
    """
    Show the setup dialog only when desktop permissions are still incomplete.

    Returns True when setup is complete (or platform is not macOS).
    """
    import platform

    if platform.system() != "Darwin":
        return True

    report = collect_permission_report(start_sidecar=True)
    if not permissions_setup_needed(report):
        logger.debug("macOS permissions complete — skipping setup dialog")
        return True

    try:
        dialog = MacOSPermissionsDialog(parent=parent)
        dialog.exec()
    except Exception as exc:
        logger.warning("macOS permissions dialog failed: %s", exc, exc_info=True)
        return False

    return not permissions_setup_needed(collect_permission_report(start_sidecar=False))
