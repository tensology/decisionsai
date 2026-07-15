"""Public HTTP hooks for external integrations (Slack Events API, etc.).

These routes live **outside** ``/api`` so third-party POSTs are not blocked by the
internal-token middleware used for the GUI APIs.

Slack Events: signing secret + bot token can be set in **Advanced → Discord & Slack** (stored encrypted) or via env
``DECISIONSAI_SLACK_SIGNING_SECRET`` / ``DECISIONSAI_SLACK_BOT_TOKEN``. Request URL:
``https://<host>/hooks/slack/events`` (tunnel/ngrok for local dev). Env overrides saved credentials when both exist.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from distr.core.integrations.slack.bridge import route_slack_inbound_to_agent
from distr.core.integrations.slack.signing import verify_slack_signature
from distr.core.integrations.token_resolve import resolve_slack_signing_secret

logger = logging.getLogger(__name__)

router = APIRouter(tags=["integrations"])


def _slack_signing_secret() -> str:
    return (resolve_slack_signing_secret() or "").strip()


def _slack_should_ignore_message_event(evt: dict[str, Any]) -> bool:
    """Drop edits, bot echoes, and empty payloads Slack still ships."""
    if evt.get("type") != "message":
        return True
    subtype = evt.get("subtype") or ""
    if subtype in (
        "bot_message",
        "message_deleted",
        "message_changed",
        "channel_join",
        "channel_leave",
        "group_join",
        "group_leave",
    ):
        return True
    text = (evt.get("text") or "").strip()
    if not text:
        return True
    if not (evt.get("channel") or "").strip():
        return True
    return False


@router.post("/hooks/slack/events")
async def slack_events(request: Request) -> JSONResponse:
    """Slack Events API — URL verification + ``event_callback`` → MessageBus."""
    secret = _slack_signing_secret()
    if not secret:
        logger.warning("POST /hooks/slack/events ignored: DECISIONSAI_SLACK_SIGNING_SECRET not set")
        raise HTTPException(
            status_code=503,
            detail="Slack signing secret not configured (DECISIONSAI_SLACK_SIGNING_SECRET)",
        )

    body = await request.body()
    ts = request.headers.get("X-Slack-Request-Timestamp")
    sig = request.headers.get("X-Slack-Signature")
    if not verify_slack_signature(
        signing_secret=secret,
        body=body,
        timestamp_header=ts,
        signature_header=sig,
    ):
        logger.warning("Slack signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if data.get("type") == "url_verification":
        ch = data.get("challenge")
        if not isinstance(ch, str):
            raise HTTPException(status_code=400, detail="Missing challenge")
        return JSONResponse({"challenge": ch})

    if data.get("type") != "event_callback":
        return JSONResponse({"ok": True})

    evt = data.get("event")
    if not isinstance(evt, dict):
        return JSONResponse({"ok": True})

    if _slack_should_ignore_message_event(evt):
        return JSONResponse({"ok": True})

    try:
        from distr.core.work_intake import WorkIntake, get_work_intake_service

        decision = get_work_intake_service().ingest(WorkIntake(
            source="slack",
            user_text=str(evt.get("text") or ""),
            source_user_id=str(evt.get("user") or ""),
            source_thread_id=str(evt.get("thread_ts") or evt.get("channel") or ""),
            source_message_id=str(evt.get("ts") or ""),
            metadata={"channel_id": str(evt.get("channel") or "")},
        ))
        if decision.handled:
            logger.info(
                "Slack request routed action=%s ticket=%s run=%s",
                decision.action.value, decision.ticket_id, decision.workflow_run_id,
            )
            return JSONResponse({"ok": True, "handled": True, "decision": decision.to_dict()})

        route_slack_inbound_to_agent(
            channel_id=str(evt["channel"]).strip(),
            user_id=str(evt["user"]).strip() if evt.get("user") else None,
            text=str(evt.get("text") or ""),
            attachment_paths=None,
            raw=evt,
            speak=None,
        )
    except Exception:
        # Acknowledge to Slack so it does not retry-spam; fix routing separately.
        logger.exception("route_slack_inbound_to_agent failed for Slack event")

    return JSONResponse({"ok": True})
