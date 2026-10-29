"""gap3 2026-10-29: make an automation's notification_policy actually notify.

Policy (stored at ``action_config.notification_policy``)::

    {"mode": "on_new" | "always" | "never", "channels": ["in_app", "macos"]}

Only local, owner-facing channels exist here on purpose: ``in_app`` (an
orchestration event plus a notice in the automation's own thread chat) and
``macos`` (native Notification Center banner). Nothing is sent over WhatsApp,
email or Telegram and nothing is spoken.

A tool signals news by returning ``{"notify": True, "headline": ..., "markdown": ...}``.
With mode ``on_new`` a run without ``notify`` is silent (``quiet`` True).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

SAFE_CHANNELS = ("in_app", "macos")
DEFAULT_POLICY = {"mode": "always", "channels": []}


def normalize_policy(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not raw:
        return dict(DEFAULT_POLICY)
    mode = str(raw.get("mode") or "on_new").strip().lower()
    if mode not in {"on_new", "always", "never"}:
        mode = "on_new"
    channels = [c for c in (raw.get("channels") or ["in_app", "macos"]) if c in SAFE_CHANNELS]
    return {"mode": mode, "channels": channels}


def has_policy(action_config: dict[str, Any] | None) -> bool:
    return isinstance(action_config, dict) and isinstance(action_config.get("notification_policy"), dict) and bool(action_config["notification_policy"])


def should_notify(policy: dict[str, Any], raw_result: dict[str, Any] | None) -> bool:
    mode = policy.get("mode")
    if mode == "never":
        return False
    if mode == "always":
        return True
    return bool((raw_result or {}).get("notify"))


def deliver(
    automation: dict[str, Any],
    raw_result: dict[str, Any] | None,
    *,
    emit_event: Callable[..., Any] | None = None,
    post_to_thread: Callable[[int, str], Any] | None = None,
    desktop: Callable[[str, str], Any] | None = None,
) -> dict[str, Any]:
    """Deliver per policy. Returns {"notified": bool, "quiet": bool, "channels": [...]}."""
    action_config = automation.get("action_config") if isinstance(automation.get("action_config"), dict) else {}
    if not has_policy(action_config):
        return {"notified": False, "quiet": False, "channels": [], "policy": None}
    policy = normalize_policy(action_config.get("notification_policy"))
    raw = raw_result if isinstance(raw_result, dict) else {}
    if not should_notify(policy, raw):
        return {"notified": False, "quiet": True, "channels": [], "policy": policy}
    name = str(automation.get("name") or "Automation")
    headline = str(raw.get("headline") or raw.get("spoken_summary") or "New items").strip()[:200]
    body = str(raw.get("markdown") or headline).strip()
    delivered: list[str] = []
    if "in_app" in policy["channels"]:
        try:
            if emit_event is None:
                from distr.core.automation_orchestrator import emit_automation_event as emit_event  # type: ignore
            emit_event(automation=automation, event_type="notification", status="completed", summary=f"{name}: {headline}", payload={"headline": headline, "needs_action": bool(raw.get("needs_action"))})
            delivered.append("in_app_event")
        except Exception:
            logger.debug("in-app automation event failed", exc_info=True)
        chat_id = automation.get("thread_chat_id")
        if chat_id:
            try:
                if post_to_thread is None:
                    from distr.core.chat import ChatService

                    post_to_thread = ChatService.append_assistant_notice
                if post_to_thread(int(chat_id), body):
                    delivered.append("in_app_thread")
            except Exception:
                logger.debug("in-app thread notice failed", exc_info=True)
    if "macos" in policy["channels"]:
        try:
            if desktop is None:
                from distr.core.tensology_notify import show_desktop_notification as desktop  # type: ignore
            desktop(name, headline)
            delivered.append("macos")
        except Exception:
            logger.debug("macOS notification failed", exc_info=True)
    return {"notified": bool(delivered), "quiet": False, "channels": delivered, "policy": policy}
