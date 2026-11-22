"""Local browser controls for updating a running desktop Git installation."""

import os
import subprocess
import threading
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from distr.core import software_update
from distr.core.app_restart import resolve_project_root
from distr.gui.web.security import is_allowed_local_origin


_check_lock = threading.Lock()
_start_lock = threading.Lock()
_last_check = 0.0
_cached_check = None


def _current_check(root, refresh):
    global _last_check, _cached_check
    with _check_lock:
        if not refresh and _cached_check and time.monotonic() - _last_check < 300:
            return _cached_check
        try:
            result = software_update.check_update(root)
        except (OSError, subprocess.SubprocessError, ValueError):
            result = {"status": "unavailable", "message": "Could not check origin/main. Check the connection and try again."}
        _cached_check = result
        _last_check = time.monotonic()
        return result


def _require_local_click(request):
    origin = request.headers.get("origin", "")
    expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
    if (origin != expected or not is_allowed_local_origin(origin)
            or request.headers.get("sec-fetch-site") != "same-origin"
            or request.headers.get("sec-fetch-mode") != "cors"
            or request.headers.get("x-decisions-update") != "click"):
        raise HTTPException(403, "Start the update from DecisionsAI Settings.")


def _begin(root, port):
    with _start_lock:
        if software_update.read_state().get("phase") in software_update.ACTIVE_STATES:
            raise HTTPException(409, "An update is already running.")
        check = _current_check(root, True)
        if check["status"] != "available":
            raise HTTPException(409, check["message"])
        try:
            software_update.start_update(root, os.getpid(), port)
        except OSError as exc:
            raise HTTPException(500, "Could not start the update process.") from exc


def _quit_for_update():
    from distr.core.app_restart import mark_restart_pending
    from distr.core.runtime_lifecycle import write_exit_intent
    from distr.core.signals import signal_manager

    try:
        mark_restart_pending()
        write_exit_intent("software_update", source="settings.software_update", expected_restart=True)
    finally:
        signal_manager.exit_app.emit()


def register_routes(router, templates):
    @router.get("/software-update")
    async def update_status(refresh: bool = False, monitor: bool = False):
        operation = software_update.read_state()
        if monitor or operation.get("phase") in software_update.ACTIVE_STATES:
            return {"operation": operation}
        check = await run_in_threadpool(_current_check, resolve_project_root(), refresh)
        return {**check, "operation": operation}

    @router.post("/software-update")
    async def apply_update(request: Request):
        from PyQt6.QtCore import QCoreApplication

        _require_local_click(request)
        if QCoreApplication.instance() is None:
            raise HTTPException(409, "The DecisionsAI desktop app must be running.")
        await run_in_threadpool(_begin, resolve_project_root(), request.url.port or 8765)
        return JSONResponse({"status": "waiting"}, status_code=202, background=BackgroundTask(_quit_for_update))
