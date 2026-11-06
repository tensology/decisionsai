"""Page screenshots for the Interfaces map.

Reads images already stored in the linked project's /.planning/ folder. When the
project's startup terminals are up and they published a local web address, a
missing page can be captured with Playwright. A Python or desktop project with
no local URL is left alone. This does not scan the project tree and does not
crawl any host the project is not already serving.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_SCREEN = re.compile(r"^[A-Za-z][\w-]{0,63}$")
_ROUTE = re.compile(r"^/[A-Za-z0-9._~/-]*$")
_URL = re.compile(r"https?://[^\s\"'<>]+")
_IMAGE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
_FRONT = ("frontend", "vite", "ng serve", "npm run dev", "yarn dev", "pnpm dev", "bun dev", "next dev")
_BACK = ("backend", "runserver", "uvicorn", "gunicorn", "manage.py", "django", "flask", "fastapi")

TERMINALS_DOWN = (
    "Images cannot be fetched because the terminals are down. "
    "If they were up, this could show the frontend and the backend."
)
NO_WEB = (
    "The terminals are up, but this project did not publish a web address. "
    "A Python or desktop interface has no page to capture."
)


def concrete_route(route: str) -> str:
    """A path this process may open. Empty when the route is not a concrete local page."""
    value = str(route or "").strip()
    if not _ROUTE.fullmatch(value) or ".." in value:
        return ""
    return value


def _clean_url(value) -> str:
    raw = str(value or "").strip().rstrip(".,;)")
    if not raw:
        return ""
    if "0.0.0.0" in raw:
        raw = raw.replace("0.0.0.0", "127.0.0.1", 1)
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or host not in {"127.0.0.1", "localhost", "::1"}:
        return ""
    return raw.rstrip("/")


def _kind(command: str) -> str:
    text = str(command or "").lower()
    front = any(token in text for token in _FRONT)
    back = any(token in text for token in _BACK)
    if front and not back:
        return "frontend"
    if back and not front:
        return "backend"
    return ""


def _session_urls(session: dict) -> list[str]:
    found = []
    for item in session.get("urls") or []:
        raw = item.get("url") if isinstance(item, dict) else item
        url = _clean_url(raw)
        if url and url not in found:
            found.append(url)
    return found


def _instruction_urls(startup_instructions: str) -> list[tuple[str, str]]:
    found = []
    for line in str(startup_instructions or "").splitlines():
        kind = _kind(line)
        for match in _URL.findall(line):
            url = _clean_url(match)
            if url:
                found.append((kind, url))
    return found


def describe_targets(running: bool, sessions: list | None, startup_instructions: str = "") -> dict:
    """Frontend and backend addresses from live terminals, then startup instructions.

    Instruction URLs are used only when the terminals are already up. They never
    invent a host the project did not write down.
    """
    if not running:
        return {"terminals_up": False, "frontend_url": "", "backend_url": "", "reason": TERMINALS_DOWN}
    front, back = "", ""
    for session in sessions or []:
        urls = _session_urls(session if isinstance(session, dict) else {})
        if not urls:
            continue
        kind = _kind(str(session.get("command") or ""))
        url = urls[0]
        if kind == "backend" and not back:
            back = url
        elif kind == "frontend" and not front:
            front = url
        elif not kind:
            if not front:
                front = url
            elif not back and url != front:
                back = url
    if not front or not back:
        for kind, url in _instruction_urls(startup_instructions):
            if kind == "backend" and not back:
                back = url
            elif kind == "frontend" and not front:
                front = url
            elif not kind and not front:
                front = url
            elif not kind and not back and url != front:
                back = url
    if not front and not back:
        return {"terminals_up": True, "frontend_url": "", "backend_url": "", "reason": NO_WEB}
    return {"terminals_up": True, "frontend_url": front, "backend_url": back, "reason": ""}


def capture_plan(targets: dict, *, lane: str, route: str, saved: bool) -> dict:
    """Choose read, grab, or skip. Does not launch a browser."""
    if saved:
        return {"action": "read", "url": "", "reason": ""}
    if lane not in {"frontend", "backend"}:
        return {"action": "skip", "url": "", "reason": "This is not a page capture."}
    path = concrete_route(route)
    if not path:
        return {"action": "skip", "url": "", "reason": "This route has no concrete page to open."}
    if not targets.get("terminals_up"):
        return {"action": "skip", "url": "", "reason": targets.get("reason") or TERMINALS_DOWN}
    base = targets.get("frontend_url") if lane == "frontend" else targets.get("backend_url")
    if not base:
        which = "frontend" if lane == "frontend" else "backend"
        return {"action": "skip", "url": "", "reason": f"No {which} address is published in the running terminals."}
    return {"action": "grab", "url": str(base).rstrip("/") + path, "reason": ""}


def _load(workspace_id: int):
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    from distr.core.planning.service import get_workspace

    workspace = get_workspace(int(workspace_id))
    if workspace is None:
        raise LookupError("Plan workspace not found.")
    project_id = workspace.get("project_id")
    if type(project_id) is not int or project_id <= 0:
        raise ValueError("Select a project before using the planning CLI.")
    with get_session() as session:
        project = session.get(Project, project_id)
    if project is None:
        raise LookupError("Project not found.")
    return workspace, project


def _planning_dir(folder: str) -> Path:
    root = Path(folder).expanduser().resolve()
    planning = (root / ".planning").resolve()
    if planning.parent != root:
        raise ValueError("Invalid project folder.")
    return planning


def _find_saved(folder: str, screen_id: str) -> Path | None:
    if not _SCREEN.fullmatch(str(screen_id or "")) or not str(folder or "").strip():
        return None
    planning = _planning_dir(folder)
    if not planning.is_dir():
        return None
    for ext in _IMAGE:
        path = planning / f"{screen_id}{ext}"
        if path.is_file():
            return path
    return None


def _saved_names(folder: str) -> list[str]:
    if not str(folder or "").strip():
        return []
    planning = _planning_dir(folder)
    if not planning.is_dir():
        return []
    names = []
    for path in sorted(planning.iterdir()):
        if path.is_file() and path.suffix.lower() in _IMAGE:
            names.append(path.name)
        if len(names) >= 80:
            break
    return names


def _image_url(workspace_id: int, screen_id: str) -> str:
    return f"/api/workflows/studio/plan-workspaces/{int(workspace_id)}/interface-shots/{screen_id}"


def interface_shot_status(workspace_id: int) -> dict:
    """Whether page images can be fetched, plus filenames already in /.planning/."""
    _workspace, project = _load(workspace_id)
    from distr.core.project_startup_terminals import project_startup_terminals_running
    from distr.core.terminal import get_startup_sessions_for_project

    project_id = int(project.id)
    try:
        running = bool(project_startup_terminals_running(project_id))
        sessions = get_startup_sessions_for_project(project_id, purpose="startup") if running else []
    except Exception:
        logger.exception("Interface shot status failed")
        running, sessions = False, []
    targets = describe_targets(running, sessions, project.startup_instructions or "")
    if not running and not targets["reason"]:
        targets["reason"] = TERMINALS_DOWN
    return {**targets, "saved": _saved_names(project.folder_location or "")}


def interface_shot_file(workspace_id: int, screen_id: str) -> tuple[Path, str]:
    """One image already saved for this screen id. No directory walk past /.planning/."""
    if not _SCREEN.fullmatch(str(screen_id or "")):
        raise ValueError("Invalid page id.")
    _workspace, project = _load(workspace_id)
    path = _find_saved(project.folder_location or "", screen_id)
    if path is None:
        raise LookupError("No screenshot for that page.")
    return path, _IMAGE[path.suffix.lower()]


def _grab(url: str, dest: Path) -> None:
    from playwright.sync_api import sync_playwright

    if not _clean_url(url):
        raise ValueError("Refusing a non-local page.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 960, "height": 600})
            response = page.goto(url, wait_until="domcontentloaded", timeout=8000)
            if not _clean_url(page.url):
                raise ValueError("The page left the local project.")
            if response is not None and response.status >= 400:
                raise ValueError(f"The page returned {response.status}.")
            page.screenshot(path=str(dest), type="png")
        finally:
            browser.close()


def capture_interface_shot(workspace_id: int, *, screen_id: str, route: str, lane: str) -> dict:
    """Return a saved image, capture one local page, or explain why it was skipped."""
    if not _SCREEN.fullmatch(str(screen_id or "")):
        raise ValueError("Invalid page id.")
    _workspace, project = _load(workspace_id)
    from distr.core.project_startup_terminals import project_startup_terminals_running
    from distr.core.terminal import get_startup_sessions_for_project

    project_id = int(project.id)
    running = bool(project_startup_terminals_running(project_id))
    sessions = get_startup_sessions_for_project(project_id, purpose="startup") if running else []
    targets = describe_targets(running, sessions, project.startup_instructions or "")
    saved = _find_saved(project.folder_location or "", screen_id)
    plan = capture_plan(targets, lane=str(lane or ""), route=str(route or ""), saved=saved is not None)
    image = _image_url(workspace_id, screen_id)
    if plan["action"] == "read":
        return {"image": image, "reason": ""}
    if plan["action"] != "grab":
        return {"image": "", "reason": plan["reason"]}
    dest = _planning_dir(project.folder_location or "") / f"{screen_id}.png"
    try:
        _grab(plan["url"], dest)
    except Exception as exc:
        logger.info("Interface shot skipped: %s", exc)
        return {"image": "", "reason": "The page did not load from the running project."}
    return {"image": image, "reason": ""}
