"""gap4 2026-10-29: read-only Gmail search for automations.

Uses DecisionsAI's own Google connection (Settings > Connected accounts,
gmail scope). Only GET calls: messages.list + messages.get(format=metadata),
which never marks mail read. No send/modify/trash/label calls exist here.
Tokens are never returned or logged.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailUnavailable(RuntimeError):
    pass


def connect(service: Any = None) -> Any:
    """Load credentials and make sure the access token is fresh (call outside read-only scope)."""
    if service is None:
        from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector

        service = GoogleWorkspaceConnector()
    if not service.is_connected():
        raise GmailUnavailable("Google account is not connected in DecisionsAI (Settings > Connected accounts).")
    if not service._ensure_valid_token():
        raise GmailUnavailable(str(getattr(service, "last_error", "") or "Google token refresh failed; reconnect Google in DecisionsAI Settings."))
    return service


def search(service: Any, query: str, *, max_results: int = 20) -> list[dict[str, Any]]:
    """Return [{id, thread_id, from, subject, date, internal_ms, labels}] for ``query``."""
    listing = service._make_request("GET", f"{GMAIL}/messages?maxResults={int(max_results)}&q={quote(query)}")
    if listing is None:
        raise GmailUnavailable(str(getattr(service, "last_error", "") or "Gmail list failed"))
    out: list[dict[str, Any]] = []
    for ref in listing.get("messages", []) or []:
        msg = service._make_request(
            "GET",
            f"{GMAIL}/messages/{ref['id']}?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date",
        )
        if not msg:
            continue
        headers = {h.get("name", "").lower(): h.get("value", "") for h in (msg.get("payload") or {}).get("headers", [])}
        out.append(
            {
                "id": msg.get("id"),
                "thread_id": msg.get("threadId"),
                "from": headers.get("from", ""),
                "subject": headers.get("subject", ""),
                "date": headers.get("date", ""),
                "internal_ms": int(msg.get("internalDate") or 0),
                "labels": msg.get("labelIds") or [],
                "snippet": (msg.get("snippet") or "")[:160],
            }
        )
    return out
