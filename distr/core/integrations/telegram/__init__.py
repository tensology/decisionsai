"""Telegram WebSocket integration — split into mixins for maintainability.

Public API:
    from distr.core.integrations.telegram import TelegramWebSocketManager, hash_channel_id
"""

from distr.core.integrations.telegram.utils import hash_channel_id
from distr.core.integrations.telegram.manager import TelegramWebSocketManager

__all__ = ["TelegramWebSocketManager", "hash_channel_id"]
