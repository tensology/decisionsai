#!/usr/bin/env python3
"""
Development server with hot-reload for the unified GUI (Settings, Chat, Actions, Skills, Projects, Flow, Board).

Usage:
    python bin/dev_server.py
"""
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from distr.gui.web.server import DEFAULT_HOST, DEFAULT_PORT
import uvicorn

if __name__ == "__main__":
    gui_dir = Path(__file__).resolve().parent.parent / "distr" / "gui"
    reload_dirs = [str(gui_dir / "web")]
    print(f"Unified server: http://{DEFAULT_HOST}:{DEFAULT_PORT}")
    print("  Settings: /settings   Chat: /chat   Actions: /actions   Skills: /skills/   Projects: /projects")
    uvicorn.run(
        "distr.gui.web.server:create_app",
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        reload=True,
        reload_dirs=reload_dirs,
        reload_includes=["*.py", "*.html", "*.css", "*.js"],
        log_level="info",
        factory=True,
    )
