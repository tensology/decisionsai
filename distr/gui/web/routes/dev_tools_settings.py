"""Settings for Development-agent tools (files, browser, calendar provider, iCloud).

GET returns masked values only (the iCloud app password is never returned).
All routes require the internal token (POSTs are also gated by the middleware).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/dev-tools", tags=["dev-tools"])
_NO_STORE = {"Cache-Control": "no-store"}
_PROVIDERS = {"auto", "google", "icloud", "eventkit"}


def _require_token(request: Request) -> None:
    from distr.gui.web.security import require_internal_token_request
    require_internal_token_request(request)


def _view(s: dict) -> dict:
    apple_id = str(s.get("icloud_apple_id") or "")
    masked_id = (apple_id[:2] + "***" + apple_id[apple_id.find("@"):]) if "@" in apple_id else ("***" if apple_id else "")
    return {
        "dev_files_enabled": s.get("dev_files_enabled") is not False,
        "dev_browser_enabled": bool(s.get("dev_browser_enabled")),
        "dev_calendar_provider": s.get("dev_calendar_provider") or "auto",
        "icloud_apple_id_masked": masked_id,
        "icloud_apple_id_set": bool(apple_id),
        "icloud_app_password_set": bool(str(s.get("icloud_app_password") or "").strip()),
        "icloud_calendar_name": s.get("icloud_calendar_name") or "",
        "browser_profile": "~/.decisions/chrome-profile",
    }


@router.get("/settings")
async def get_settings(request: Request):
    _require_token(request)
    from distr.core.settings import load_settings_from_db
    return JSONResponse(_view(load_settings_from_db() or {}), headers=_NO_STORE)


@router.post("/settings")
async def post_settings(request: Request):
    _require_token(request)
    body = await request.json()
    from distr.core.settings import load_settings_from_db, save_settings_to_db
    s = load_settings_from_db() or {}
    for key in ("dev_files_enabled", "dev_browser_enabled"):
        if key in body:
            s[key] = bool(body[key])
    if "dev_calendar_provider" in body and str(body["dev_calendar_provider"]) in _PROVIDERS:
        s["dev_calendar_provider"] = str(body["dev_calendar_provider"])
    if "icloud_calendar_name" in body:
        s["icloud_calendar_name"] = str(body["icloud_calendar_name"] or "")[:200]
    if str(body.get("icloud_apple_id") or "").strip():
        s["icloud_apple_id"] = str(body["icloud_apple_id"]).strip()[:320]
    if str(body.get("icloud_app_password") or "").strip():  # blank = keep existing
        s["icloud_app_password"] = str(body["icloud_app_password"]).strip()[:128]
    if body.get("icloud_clear"):
        s["icloud_apple_id"] = ""
        s["icloud_app_password"] = ""
    save_settings_to_db(s)
    return JSONResponse({"success": True, **_view(load_settings_from_db() or {})}, headers=_NO_STORE)


@router.post("/icloud/test")
async def test_icloud(request: Request):
    _require_token(request)
    import asyncio
    from distr.core.settings import load_settings_from_db
    from distr.core.workflow.dev_calendar_tools import NotConnected, resolve_provider

    def _check():
        try:
            cal = resolve_provider("icloud", load_settings_from_db() or {})
            return cal.health()
        except NotConnected as exc:
            return False, str(exc)
    ok, why = await asyncio.to_thread(_check)
    return JSONResponse({"ok": ok, "message": "Connected" if ok else why}, headers=_NO_STORE)


@router.post("/browser/signin")
async def browser_signin(request: Request):
    """Open the dedicated Decisions Chrome profile visibly so the user can sign in once."""
    _require_token(request)
    repo = Path(__file__).resolve().parents[4]
    subprocess.Popen([sys.executable, "-m", "distr.core.workflow.dev_browser_tools", "signin"],
                     cwd=str(repo), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return JSONResponse({"ok": True, "message": "Chrome (Decisions profile) is opening. Sign in, then close the window."},
                        headers=_NO_STORE)
