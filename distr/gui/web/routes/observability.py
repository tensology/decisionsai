"""Browser responsiveness diagnostics."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from distr.core.paths import DATA_DIR, DB_DIR
from distr.core.storage_maintenance import read_storage_maintenance_state, storage_policy

logger = logging.getLogger(__name__)


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def runtime_diagnostics() -> dict:
    """Return safe runtime identity and storage facts for support work."""
    db_root = Path(DB_DIR).expanduser().resolve()
    data_root = Path(DATA_DIR).expanduser().resolve()
    settings_db = db_root / "settings.db"
    repo_root = Path(__file__).resolve().parents[4]
    try:
        version = (repo_root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        version = "unknown"
    try:
        disk = shutil.disk_usage(db_root)
        disk_free_bytes = disk.free
    except OSError:
        disk_free_bytes = 0
    return {
        "status": "ok",
        "pid": os.getpid(),
        "version": version,
        "data_root": str(data_root),
        "db_root": str(db_root),
        "settings_db_bytes": _file_size(settings_db),
        "settings_wal_bytes": _file_size(Path(f"{settings_db}-wal")),
        "decisions_log_bytes": _file_size(db_root / "logs" / "decisions.log"),
        "launcher_log_bytes": _file_size(Path.home() / ".decisions" / "logs" / "launcher.log"),
        "disk_free_bytes": disk_free_bytes,
        "storage_policy": storage_policy(),
        "storage_maintenance": read_storage_maintenance_state(db_root),
    }


def create_routes() -> APIRouter:
    router = APIRouter()

    @router.post("/diagnostics/ui-stall")
    async def record_ui_stall(request: Request):
        payload = await request.json()
        logger.warning(
            "Web UI stall detected duration_ms=%s drift_ms=%s path=%s visibility=%s",
            payload.get("duration_ms"),
            payload.get("drift_ms"),
            payload.get("path"),
            payload.get("visibility"),
        )
        return {"success": True}

    @router.get("/diagnostics/runtime")
    async def get_runtime_diagnostics():
        return runtime_diagnostics()

    @router.post("/system/restart")
    async def restart_application():
        """Restart the desktop app cleanly.

        Deliberately goes through the same in-app path as the restart button:
        the app spawns a detached relaunch and then quits *itself*. Killing the
        process from outside leaves the multiprocessing shared-memory manager
        dead, and the next agent worker dies with ConnectionRefusedError, so an
        external kill is not a restart - it is an outage.
        """
        try:
            from PyQt6.QtCore import QTimer

            from distr.core.signals import signal_manager

            def _request_restart() -> None:
                try:
                    signal_manager.restart_app.emit()
                except Exception:
                    logger.warning("Restart signal could not be emitted", exc_info=True)

            # Reply first, then restart on the Qt thread so the response is not
            # cut off mid-flight by the app quitting.
            QTimer.singleShot(300, _request_restart)
            logger.info("Restart requested via /api/system/restart")
            return {"success": True, "restarting": True}
        except Exception as exc:
            logger.error("Restart request failed: %s", exc, exc_info=True)
            return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

    return router
