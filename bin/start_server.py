#!/usr/bin/env python3
"""
Run the project's unified web server (Flow, Board, Settings, Chat).

Usage:
    python bin/start_server.py
    ./bin/start_server.py

Serves: /flow, /board, /settings, /chat on 127.0.0.1:8765.
Uses project venv if present so deps (fastapi, uvicorn) are available.
"""
import os
import sys
from pathlib import Path

def _project_root():
    return Path(__file__).resolve().parent.parent

def _ensure_venv_and_reexec():
    """Re-exec with project venv Python if we're not already using it."""
    project_root = _project_root()
    venv_python = None
    for name in ("venv",):
        candidate = project_root / name / "bin" / "python"
        if candidate.exists():
            venv_python = candidate
            break
    if not venv_python:
        return
    current = Path(sys.executable).resolve()
    if current == venv_python:
        return
    script = project_root / "bin" / "start_server.py"
    os.execv(venv_python, [str(venv_python), str(script)])

def _load_dotenv():
    project_root = _project_root()
    env_path = project_root / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
    except ImportError:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())

def main():
    _ensure_venv_and_reexec()
    project_root = _project_root()
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    _load_dotenv()
    try:
        from distr.gui.web.server import create_app, DEFAULT_HOST, DEFAULT_PORT
        import uvicorn
    except ModuleNotFoundError as e:
        sys.stderr.write(
            f"Missing dependency: {e.name}. Install project deps first, e.g.\n"
            f"  cd {project_root} && pip install -r requirements.txt\n"
            "Or run from project venv: source venv/bin/activate && python bin/start_server.py\n"
        )
        sys.exit(1)
    app = create_app()
    print(f"Serving at http://{DEFAULT_HOST}:{DEFAULT_PORT} (flow, board, settings, chat)")
    uvicorn.run(app, host=DEFAULT_HOST, port=DEFAULT_PORT)

if __name__ == "__main__":
    main()
