"""Automation-only tool: publish one media item to the owner's WhatsApp Status.

Registered for tool-bound automations (``_TOOL_BINDINGS``) and deliberately NOT in
the LLM tool registry, so the conversational agent cannot post a Status on its own.
A scheduled automation that names a media file is the owner's standing authorisation.
"""

from __future__ import annotations

import re
from typing import Any

from langchain.tools import BaseTool

_INDIVIDUAL_JID = re.compile(r"[1-9]\d{5,14}@s\.whatsapp\.net")


class WhatsAppStatusTool(BaseTool):
    name: str = "whatsapp_status"
    description: str = (
        "Publish one image or video to the owner's WhatsApp Status for all contacts. "
        "Used only by scheduled automations."
    )

    def _run(self, media_path: str = "", text: str = "", **_: Any) -> dict[str, Any]:
        from distr.core.agent.tools.integrations.whatsapp_toolkit import _status_payload
        from distr.core.integrations.whatsapp.relay_client import (
            publish_status_via_relay,
            relay_api_base,
            relay_auth_headers,
        )

        label = str(media_path or "").rsplit("/", 1)[-1] or "status"
        if not str(media_path or "").strip():
            return {"success": False, "spoken_summary": "No Status media path was provided."}

        try:
            import requests

            response = requests.get(
                f"{relay_api_base()}/contacts",
                params={"limit": 500},
                headers=relay_auth_headers(),
                timeout=30,
            )
            contacts = response.json().get("contacts") or []
        except Exception as exc:
            return {"success": False, "spoken_summary": f"Could not load the WhatsApp contacts: {exc}"}

        audience = sorted({
            str(contact.get("jid"))
            for contact in contacts
            if _INDIVIDUAL_JID.fullmatch(str(contact.get("jid") or ""))
        })
        if not audience:
            return {"success": False, "spoken_summary": "No WhatsApp contacts were available for the Status audience."}

        try:
            payload = _status_payload(str(text or ""), audience, str(media_path))
        except (ValueError, OSError) as exc:
            return {"success": False, "spoken_summary": f"Could not prepare {label}: {exc}"}

        result = publish_status_via_relay(payload)
        if not bool(result.get("success")):
            return {
                "success": False,
                "spoken_summary": f"Could not post {label} to your WhatsApp Status: {result.get('error') or result.get('state')}",
                "raw": result,
            }
        return {
            "success": True,
            "spoken_summary": f"Posted {label} to your WhatsApp Status.",
            "state": result.get("state"),
            "message_id": result.get("message_id"),
            "audience_count": len(audience),
        }
