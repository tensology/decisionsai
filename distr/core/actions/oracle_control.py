"""
Oracle/globe window control — hide, show, cycle images.

These functions communicate with the GUI process via event_queue
(cross-process) or fall back to direct signal emission (same process).
"""

import os
import logging
from distr.core.paths import ORACLE_DIR
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


def _cycle_oracle(event_queue, direction=1):
    """Cycle oracle image forward (1) or backward (-1)."""
    oracle_files = sorted(
        [f for f in os.listdir(ORACLE_DIR) if f.endswith('.gif')],
        key=lambda x: int(os.path.splitext(x)[0]) if os.path.splitext(x)[0].isdigit() else float('inf')
    )
    if not oracle_files:
        logger.error("No oracle files found in %s", ORACLE_DIR)
        return

    settings = load_settings_from_db()
    current = settings.get('selected_oracle', '0.gif')
    try:
        idx = oracle_files.index(current)
    except ValueError:
        idx = 0
    next_file = oracle_files[(idx + direction) % len(oracle_files)]

    settings['selected_oracle'] = next_file
    save_settings_to_db(settings)
    _send_event(event_queue, 'oracle_change', {'filename': next_file})
    logger.info("Changed oracle to: %s", next_file)


def change_oracle(event_queue=None):
    """Cycle to the next oracle image."""
    _cycle_oracle(event_queue, direction=1)


def change_previous_oracle(event_queue=None):
    """Cycle to the previous oracle image."""
    _cycle_oracle(event_queue, direction=-1)
