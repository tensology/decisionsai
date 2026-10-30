"""Small proposal invalidations shared by desktop and web navigation."""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_version = 0


def proposal_change_version():
    with _lock:
        return _version


def publish_incoming_change(*, event="proposal_changed", request_id=None, status="", chat_id=None):
    global _version
    with _lock:
        _version += 1
    from distr.gui.web.workflow_events import increment_workflow_updated

    increment_workflow_updated()
    try:
        from distr.gui.web.routes.chat import publish_topic_event_threadsafe

        publish_topic_event_threadsafe("incoming", {
            "type": event, "request_id": request_id, "status": status, "chat_id": chat_id,
        })
    except Exception:
        logger.debug("Incoming change push unavailable", exc_info=True)
