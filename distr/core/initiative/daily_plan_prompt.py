"""Initiative-side opt-in prompt for the Daily plan automation preset."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SUGGESTION_TEXT = (
    "I can set up a Daily plan automation that builds your day from boards, projects, "
    "email, and IDE context on a schedule you choose — for example 9:00. "
    "Would you like me to add that in Automations, or skip it for now?"
)


def maybe_suggest_daily_plan_automation(service, settings: dict) -> None:
    """Ask once whether to enable the Daily plan automation preset."""
    from distr.core.automation_resolver import has_active_daily_plan_automation
    from distr.core.engagement_gates import daily_plan_prompt_due, mark_daily_plan_prompt_sent
    from distr.core.initiative.tiers import PermissionTier

    if has_active_daily_plan_automation():
        return
    if not daily_plan_prompt_due():
        return

    level = service._get_level(settings)
    if level in ("observe",):
        return

    mark_daily_plan_prompt_sent()
    try:
        service._log_to_chat(f"[Initiative]\n\n{_SUGGESTION_TEXT}")
    except Exception:
        logger.debug("daily plan suggestion chat log failed", exc_info=True)

    allow_telegram = settings.get("initiative_allow_telegram", False)
    if allow_telegram:
        try:
            service._send_telegram_if_allowed(
                _SUGGESTION_TEXT,
                settings,
                kind="initiative_suggestion",
                requires_response=True,
                priority="normal",
            )
        except Exception:
            logger.debug("daily plan suggestion telegram failed", exc_info=True)

    from distr.core.initiative.proposed_action import ProposedAction

    action = ProposedAction(
        action_type="suggestion",
        description=_SUGGESTION_TEXT,
        payload={"source": "daily_plan_preset_offer", "preset_id": "daily_plan"},
        telegram_message=_SUGGESTION_TEXT,
        requires_confirmation=True,
    )
    try:
        service._draft_and_ask(action, settings, tier=PermissionTier.ASSIST)
    except Exception:
        logger.debug("daily plan suggestion draft failed", exc_info=True)
