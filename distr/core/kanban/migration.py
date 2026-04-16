"""
Ticket Board settings migration module.

Provides a one-time migration that copies agent settings from the first
agent-enabled KanbanBoard into the global Settings store.  Called during
application startup so that existing per-board configuration is preserved
when the system moves to centralised global settings.
"""

import logging

from distr.core.db import get_session
from distr.core.db.kanban import KanbanBoard
from distr.core.settings import load_settings_from_db, save_settings_to_db

logger = logging.getLogger(__name__)

# Mapping from KanbanBoard column names to global settings keys.
_BOARD_TO_GLOBAL = {
    "agent_enabled":              "kanban_agent_enabled",
    "agent_frequency":            "kanban_agent_frequency",
    "agent_time":                 "kanban_agent_time",
    "agent_days":                 "kanban_agent_days",
    "agent_monthly_day":          "kanban_agent_monthly_day",
    "agent_orchestrator_provider": "kanban_agent_orchestrator_provider",
    "agent_orchestrator_model":   "kanban_agent_orchestrator_model",
    "agent_coder_provider":       "kanban_agent_coder_provider",
    "agent_coder_model":          "kanban_agent_coder_model",
    "agent_sub_provider":         "kanban_agent_sub_provider",
    "agent_sub_model":            "kanban_agent_sub_model",
    "agent_source_lane":          "kanban_agent_source_lane",
    "agent_done_lane":            "kanban_agent_done_lane",
}


def migrate_board_agent_settings_to_global() -> None:
    """One-time migration: copy agent settings from first enabled board to global settings."""
    try:
        settings = load_settings_from_db()

        # If the key was explicitly set before (i.e. migration already ran or
        # user configured global settings), skip.
        if settings.get("_kanban_migration_done", False):
            logger.debug("Ticket Board global settings migration already completed, skipping.")
            return

        # Look for the first board with agent_enabled=True, ordered by ID.
        board = None
        with get_session() as session:
            board = (
                session.query(KanbanBoard)
                .filter(KanbanBoard.agent_enabled == True)  # noqa: E712
                .order_by(KanbanBoard.id)
                .first()
            )

            if board is not None:
                # Copy agent fields from the board to global settings keys.
                for board_col, settings_key in _BOARD_TO_GLOBAL.items():
                    value = getattr(board, board_col, None)
                    if value is not None:
                        settings[settings_key] = value
                logger.info(
                    "Ticket Board migration: copied agent settings from board id=%s ('%s') to global settings.",
                    board.id,
                    board.name,
                )
            else:
                # No agent-enabled board found — initialise with defaults.
                settings["kanban_agent_enabled"] = False
                settings["kanban_agent_frequency"] = "daily"
                settings["kanban_agent_time"] = "09:00"
                settings["kanban_agent_hours"] = "[]"
                settings["kanban_agent_days"] = "[]"
                settings["kanban_agent_monthly_day"] = 1
                settings["kanban_agent_source_lane"] = ""
                settings["kanban_agent_done_lane"] = ""
                settings["kanban_agent_orchestrator_provider"] = ""
                settings["kanban_agent_orchestrator_model"] = ""
                settings["kanban_agent_coder_provider"] = ""
                settings["kanban_agent_coder_model"] = ""
                settings["kanban_agent_sub_provider"] = ""
                settings["kanban_agent_sub_model"] = ""
                logger.info("Ticket Board migration: no agent-enabled boards found, initialized with defaults.")

        # Mark migration as done so it won't run again.
        settings["_kanban_migration_done"] = True
        save_settings_to_db(settings)

    except Exception as e:
        logger.error("Ticket Board settings migration failed: %s", e)
