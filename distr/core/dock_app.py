"""macOS Dock integration when launched from decisions.app."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

ACTIVATE_REQUEST_PATH = Path.home() / ".decisions" / "run" / "activate.request"


def is_dock_app() -> bool:
    return os.environ.get("DECISIONS_DOCK_APP", "").strip().lower() in ("1", "true", "yes")


def request_instance_activation() -> None:
    """Ask a running instance to come to the front (second Dock click)."""
    ACTIVATE_REQUEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACTIVATE_REQUEST_PATH.touch()


def consume_activation_request() -> bool:
    try:
        if ACTIVATE_REQUEST_PATH.exists():
            ACTIVATE_REQUEST_PATH.unlink()
            return True
    except OSError:
        logger.debug("Could not consume dock activation request", exc_info=True)
    return False


def configure_qt_dock_identity(app) -> None:
    """Use DecisionsAI naming in the Dock instead of a generic Python label."""
    if not is_dock_app():
        return
    app.setApplicationName("DecisionsAI")
    app.setApplicationDisplayName("DecisionsAI")
    app.setOrganizationName("Tensology")
    if sys.platform == "darwin":
        try:
            from Foundation import NSProcessInfo

            NSProcessInfo.processInfo().setProcessName_("DecisionsAI")
        except Exception:
            logger.debug("Could not set macOS process name", exc_info=True)


def activate_application_window(app) -> None:
    """Bring the oracle to the front."""
    if sys.platform == "darwin":
        try:
            import AppKit

            AppKit.NSApp.activateIgnoringOtherApps_(True)
        except Exception:
            logger.debug("NSApp activation failed", exc_info=True)

    oracle = getattr(app, "oracle_window", None)
    if oracle is None:
        return
    try:
        if hasattr(oracle, "show_oracle"):
            oracle.show_oracle()
        else:
            oracle.show()
        oracle.raise_()
        oracle.activateWindow()
    except Exception:
        logger.debug("Oracle activation failed", exc_info=True)


def check_dock_activation_request(app) -> None:
    if consume_activation_request():
        activate_application_window(app)
