"""macOS Dock integration when launched from decisions.app."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

ACTIVATE_REQUEST_PATH = Path.home() / ".decisions" / "run" / "activate.request"
DOCK_LAUNCH_PREFERENCE_PATH = Path.home() / ".decisions" / "run" / "dock_launch.json"


def is_dock_app() -> bool:
    return os.environ.get("DECISIONS_DOCK_APP", "").strip().lower() in ("1", "true", "yes")


def load_dock_launch_preference() -> dict:
    try:
        if DOCK_LAUNCH_PREFERENCE_PATH.is_file():
            data = json.loads(DOCK_LAUNCH_PREFERENCE_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        logger.debug("Could not read dock launch preference", exc_info=True)
    return {}


def persist_dock_launch_preference(app_bundle: str = "") -> None:
    """Remember that the user runs Decisions from the Dock .app bundle."""
    if not is_dock_app():
        return
    bundle = (app_bundle or os.environ.get("DECISIONS_APP_BUNDLE", "")).strip()
    payload = {"dock": True, "app_bundle": bundle}
    try:
        DOCK_LAUNCH_PREFERENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        DOCK_LAUNCH_PREFERENCE_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        logger.debug("Could not persist dock launch preference", exc_info=True)


def resolve_app_bundle_path(project_root: Path | str | None = None) -> str:
    env_bundle = os.environ.get("DECISIONS_APP_BUNDLE", "").strip()
    if env_bundle and Path(env_bundle).is_dir():
        return env_bundle

    if project_root:
        candidate = Path(project_root) / "decisions.app"
        if candidate.is_dir():
            return str(candidate)

    pref_bundle = str(load_dock_launch_preference().get("app_bundle") or "").strip()
    if pref_bundle and Path(pref_bundle).is_dir():
        return pref_bundle

    return ""


def wants_dock_icon(project_root: Path | str | None = None) -> bool:
    """Whether this process should show in the Dock (dot), not run as a background app."""
    if is_dock_app():
        return True
    if os.environ.get("DECISIONS_RESTARTING", "").strip() == "1":
        pref = load_dock_launch_preference()
        if pref.get("dock") or pref.get("app_bundle"):
            return True
    bundle = resolve_app_bundle_path(project_root)
    return bool(bundle)


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
    if not wants_dock_icon():
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


def ensure_macos_dock_visible(app=None) -> None:
    """Show the Dock running indicator for DecisionsAI (not a background Python agent)."""
    if sys.platform != "darwin" or not wants_dock_icon():
        return
    try:
        import AppKit

        info = AppKit.NSBundle.mainBundle().infoDictionary()
        if isinstance(info, dict):
            info.pop("LSUIElement", None)
        if AppKit.NSApp is not None:
            AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
        if app is not None:
            configure_qt_dock_identity(app)
    except Exception:
        logger.debug("Could not apply macOS dock visibility", exc_info=True)


def check_dock_activation_request(app) -> None:
    if consume_activation_request():
        activate_application_window(app)
