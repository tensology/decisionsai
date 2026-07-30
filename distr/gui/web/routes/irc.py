"""Local proxy routes for the shared DecisionsAI IRC-style room chat."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Request


def _env_file_value(name: str) -> str:
    env_path = Path(__file__).resolve().parents[4] / ".env"
    try:
        for line in env_path.read_text().splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            if key.strip() == name:
                return value.strip().strip("'\"")
    except OSError:
        return ""
    return ""


def _relay_base() -> str:
    explicit = os.environ.get("DECISIONSAI_RELAY_BASE") or _env_file_value("DECISIONSAI_RELAY_BASE")
    return (explicit or "https://www.decisionsai.net").rstrip("/")


def _relay_ws_url() -> str:
    base = _relay_base()
    parsed = urlparse(base)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    host = parsed.netloc
    return f"{scheme}://{host}/ws/chat"


def _relay_internal_headers() -> dict[str, str]:
    token = (os.environ.get("RELAY_INTERNAL_TOKEN") or _env_file_value("RELAY_INTERNAL_TOKEN") or "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="RELAY_INTERNAL_TOKEN is not configured")
    return {"X-Relay-Internal-Token": token}


def _bearer_token(request: Request) -> str:
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    raise HTTPException(status_code=403, detail="Missing chat token")


async def _request_bridge_token(client: httpx.AsyncClient, base: str) -> str:
    token_resp = await client.post(
        f"{base}/api/telegram/ws-token",
        headers={**_relay_internal_headers(), "Content-Type": "application/json"},
        json={"app_user_id": "local-ui"},
    )
    if token_resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Relay token failed: HTTP {token_resp.status_code}")
    bridge_token = token_resp.json().get("token")
    if not bridge_token:
        raise HTTPException(status_code=502, detail="Relay token response was empty")
    return bridge_token


async def _request_chat_session(
    client: httpx.AsyncClient,
    base: str,
    authorization: str,
    payload: dict,
) -> httpx.Response:
    return await client.post(
        f"{base}/api/chat/session",
        headers={"Authorization": authorization, "Content-Type": "application/json"},
        json=payload,
    )


def create_routes() -> APIRouter:
    router = APIRouter()

    @router.post("/irc/session")
    async def create_irc_session(request: Request):
        body = await request.json()
        display_name = (body.get("display_name") or "Guest").strip() or "Guest"
        admin_code = (body.get("admin_code") or "").strip()
        client_id = (body.get("client_id") or "").strip()
        update_display_name = bool(body.get("update_display_name"))
        base = _relay_base()
        browser_auth = (request.headers.get("authorization") or "").strip()
        session_payload = {
            "display_name": display_name,
            "app_user_id": "local-ui",
            "admin_code": admin_code,
            "client_id": client_id,
            "update_display_name": update_display_name,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            session_resp = None
            if browser_auth.lower().startswith("bearer "):
                session_resp = await _request_chat_session(
                    client, base, browser_auth, session_payload
                )

            # A cached browser chat token may have expired or been invalidated.
            # Refresh through the trusted local relay bridge and retry instead of
            # trapping the join dialog in a permanent Unauthorized state.
            if session_resp is None or session_resp.status_code in {401, 403}:
                bridge_token = await _request_bridge_token(client, base)
                session_resp = await _request_chat_session(
                    client, base, f"Bearer {bridge_token}", session_payload
                )

            if session_resp.status_code >= 400:
                detail = f"Chat session failed: HTTP {session_resp.status_code}"
                try:
                    detail = session_resp.json().get("detail") or detail
                except ValueError:
                    detail = session_resp.text[:500] or detail
                raise HTTPException(status_code=session_resp.status_code, detail=detail)
            data = session_resp.json()
            data["ws_url"] = _relay_ws_url()
            return data

    @router.get("/irc/rooms")
    async def list_irc_rooms(request: Request):
        token = _bearer_token(request)
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_relay_base()}/api/chat/rooms", headers={"Authorization": f"Bearer {token}"})
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text[:500])
            return resp.json()

    @router.get("/irc/rooms/{room_slug}/messages")
    async def get_irc_room_messages(room_slug: str, request: Request, limit: int = Query(120)):
        token = _bearer_token(request)
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_relay_base()}/api/chat/rooms/{room_slug}/messages",
                headers={"Authorization": f"Bearer {token}"},
                params={"limit": limit},
            )
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text[:500])
            return resp.json()

    @router.get("/irc/audit")
    async def get_irc_audit(request: Request, limit: int = Query(80)):
        token = _bearer_token(request)
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_relay_base()}/api/chat/audit",
                headers={"Authorization": f"Bearer {token}"},
                params={"limit": limit},
            )
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text[:500])
            return resp.json()

    return router
