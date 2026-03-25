"""
Logs routes — /logs
"""
import os
import logging

from fastapi.responses import JSONResponse

from ._shared import logger
from distr.core.paths import DB_DIR


def register_routes(router, templates):

    def _get_log_file_path():
        """Use the same log file as the app: discover from logging or fall back to DB_DIR/logs/decisions.log."""
        for name in ("distr", ""):
            log = logging.getLogger(name)
            for h in log.handlers:
                if getattr(h, "baseFilename", None):
                    p = h.baseFilename
                    if os.path.isfile(p):
                        return os.path.abspath(p)
        return os.path.abspath(os.path.join(DB_DIR, "logs", "decisions.log"))

    @router.get("/logs")
    async def get_logs(tail_lines: int = 500):
        """Return tail of decisions.log for the Logs settings tab (polling is client-side)."""
        log_path_abs = _get_log_file_path()
        default_dir = os.path.join(DB_DIR, "logs")
        if not os.path.isfile(log_path_abs):
            if not os.path.isdir(default_dir):
                os.makedirs(default_dir, exist_ok=True)
            return JSONResponse({
                "content": "(No log file yet. Logs are written to:\n  " + log_path_abs + "\nStart the full app (not just the web server) so logging is set up.)",
                "path": log_path_abs,
            })
        try:
            max_bytes = 256 * 1024
            with open(log_path_abs, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                size = f.tell()
                if size <= max_bytes:
                    f.seek(0)
                    content = f.read()
                else:
                    f.seek(size - max_bytes)
                    f.readline()
                    content = f.read()
            lines = content.splitlines()
            if len(lines) > tail_lines:
                lines = lines[-tail_lines:]
            content = "\n".join(lines)
            if not content.strip():
                content = "(Log file is empty. Use the app to generate some log entries.)"
            return JSONResponse({"content": content, "path": log_path_abs})
        except Exception as e:
            logger.error(f"Failed to read logs: {e}", exc_info=True)
            return JSONResponse({"content": f"(error reading log: {e})", "path": log_path_abs}, status_code=500)
