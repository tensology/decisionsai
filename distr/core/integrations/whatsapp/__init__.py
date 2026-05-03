"""WhatsApp integration for DecisionsAI — receive messages via Baileys relay server."""

__all__ = ["WhatsAppWebSocketManager"]


def __getattr__(name: str):
    if name == "WhatsAppWebSocketManager":
        from .manager import WhatsAppWebSocketManager

        return WhatsAppWebSocketManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")