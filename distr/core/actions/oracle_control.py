"""
Oracle/globe window control — hide, show, cycle skins.

These functions communicate with the GUI process via event_queue
(cross-process) or fall back to direct signal emission (same process).
"""

import logging
from distr.core.utils import load_settings_from_db, save_settings_to_db

logger = logging.getLogger(__name__)


def _send_event(event_queue, event_name, data=None):
    """Send an event to the main GUI process, falling back to signals."""
    if event_queue:
        try:
            event_queue.put((event_name, data or {}), block=False)
            return True
        except Exception as e:
            logger.error("Error sending %s event: %s", event_name, e)
    else:
        try:
            from distr.core.signals import signal_manager
            getattr(signal_manager, event_name).emit()
            return True
        except Exception as e:
            logger.error("No event_queue and signal emission failed for %s: %s", event_name, e)
    return False


def hide_oracle(event_queue=None):
    """Hide the oracle/globe window."""
    _send_event(event_queue, 'hide_oracle')


def show_oracle(event_queue=None):
    """Show the oracle/globe window."""
    _send_event(event_queue, 'show_oracle')


def _get_skins():
    """Return list of (folder_name, SkinConfig) sorted as the UI does."""
    try:
        from distr.core.paths import AVATARS_DIR
        from distr.core.skin_discovery import discover_skins
        return discover_skins(AVATARS_DIR)
    except Exception as e:
        logger.error("Failed to discover skins: %s", e)
        return []


def _current_skin_is_oracle_type() -> bool:
    """Return True if the currently selected skin has type == 'oracle'."""
    try:
        settings = load_settings_from_db()
        current = settings.get("selected_oracle", "oracle") or "oracle"
        from distr.core.paths import AVATARS_DIR
        from distr.core.skin_discovery import get_skin_by_name
        result = get_skin_by_name(AVATARS_DIR, current)
        if result:
            _, config = result
            return config.type == "oracle"
    except Exception as e:
        logger.error("Failed to check current skin type: %s", e)
    return False


def _cycle_oracle(event_queue, direction=1):
    """Cycle oracle animation (if on oracle skin) or open skin settings (if on another skin).

    direction=1 → next, direction=-1 → previous (previous not yet supported for webm cycling,
    falls back to next).
    """
    if not _current_skin_is_oracle_type():
        # Not on oracle skin — open skin settings in the web UI
        _open_skin_settings(event_queue)
        return

    # On oracle skin — emit signal so the window's cycle_oracle() runs
    if event_queue:
        try:
            event = 'change_oracle' if direction >= 0 else 'change_oracle_previous'
            event_queue.put((event, {}), block=False)
        except Exception as e:
            logger.error("Error sending change_oracle event: %s", e)
    else:
        try:
            from distr.core.signals import signal_manager
            if direction >= 0:
                signal_manager.change_oracle.emit()
            else:
                signal_manager.change_oracle_previous.emit()
        except Exception as e:
            logger.error("change_oracle signal failed: %s", e)

    logger.info("Emitted change_oracle (direction=%d)", direction)


def _open_skin_settings(event_queue):
    """Open the skin settings page in the web UI."""
    try:
        import webbrowser
        webbrowser.open("http://localhost:8765/settings#skins")
        logger.info("Opened skin settings page")
    except Exception as e:
        logger.error("Failed to open skin settings: %s", e)


def change_oracle(event_queue=None):
    """Cycle to the next skin (or open settings if not on oracle skin)."""
    _cycle_oracle(event_queue, direction=1)


def change_previous_oracle(event_queue=None):
    """Cycle to the previous skin (or open settings if not on oracle skin)."""
    _cycle_oracle(event_queue, direction=-1)
