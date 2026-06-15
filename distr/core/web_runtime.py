"""Resolve the local unified web server base URL."""

from __future__ import annotations

import os


def get_local_web_base_url(default_port: int = 8765) -> str:
    port = (os.environ.get("DECISIONS_WEB_PORT") or "").strip()
    if port.isdigit():
        return f"http://127.0.0.1:{int(port)}"
    try:
        from distr.gui.web.server import get_unified_server

        server = get_unified_server()
        if server and getattr(server, "is_running", False):
            return server.get_url()
    except Exception:
        pass
    return f"http://127.0.0.1:{default_port}"
