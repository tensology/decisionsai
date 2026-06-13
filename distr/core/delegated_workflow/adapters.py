"""Default adapters for Hermes delegated workflow execution."""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib.util
import os
import re
import subprocess
import tempfile
from typing import Any

from distr.core.project_cli_backends.base import BackendTaskResult


class GoogleEmailAdapter:
    """Email adapter backed by the existing Google Workspace connector."""

    def __init__(self, connector: Any = None) -> None:
        if connector is None:
            from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector

            connector = GoogleWorkspaceConnector()
        self.connector = connector
        self._message_cache: dict[str, dict[str, Any]] = {}

    @property
    def connected(self) -> bool:
        try:
            return bool(self.connector.is_connected())
        except Exception:
            return False

    def search_latest_email(self, *, sender_hint: str, query: str) -> dict[str, Any] | None:
        messages = self.connector.check_inbox(max_results=10, query=query)
        if not messages:
            return None
        first = dict(messages[0])
        first["message_id"] = first.get("message_id") or first.get("id")
        if first.get("message_id"):
            self._message_cache[str(first["message_id"])] = first
        return first

    def download_attachments(self, *, message_id: str, destination_dir: str) -> list[dict[str, Any]]:
        email = self._message_cache.get(str(message_id))
        if email is None and hasattr(self.connector, "get_email"):
            email = self.connector.get_email(message_id)
        if not email:
            return []
        downloaded: list[dict[str, Any]] = []
        for item in email.get("attachments") or []:
            path = self.connector.download_email_attachment(
                message_id=message_id,
                attachment_id=item.get("attachment_id") or item.get("id") or "",
                filename=item.get("filename") or "attachment",
                destination_dir=destination_dir,
            )
            if path:
                downloaded.append({
                    "path": path,
                    "name": item.get("filename") or "attachment",
                    "mime_type": item.get("mime_type") or "",
                    "size": item.get("size") or 0,
                })
        return downloaded

    def check_readiness(self) -> dict[str, Any]:
        connected = self.connected
        return {
            "ready": connected,
            "detail": "Google Workspace email is connected." if connected else "Google Workspace email is not connected.",
            "connected": connected,
            "capabilities": ["search_latest_email", "download_attachments"],
        }


class DocumentExtractorAdapter:
    """Document adapter backed by the existing document extractor tool."""

    def __init__(self, tool: Any = None) -> None:
        if tool is None:
            from distr.core.agent.tools.files.document_extractor import DocumentExtractorTool

            tool = DocumentExtractorTool()
        self.tool = tool

    def extract(self, file_path: str) -> str:
        return str(self.tool._run(file_path=file_path))

    def check_readiness(self) -> dict[str, Any]:
        ready = hasattr(self.tool, "_run")
        return {
            "ready": ready,
            "detail": "Document extraction tool is available." if ready else "Document extraction tool is not available.",
        }


class DirectDesktopAdapter:
    """Desktop sequence adapter that prefers direct clipboard/filesystem operations."""

    def __init__(
        self,
        *,
        home_dir: str | None = None,
        clipboard_getter: Any = None,
        clipboard_setter: Any = None,
    ) -> None:
        self.home_dir = home_dir or os.path.expanduser("~")
        self.clipboard_getter = clipboard_getter or _system_clipboard_get
        self.clipboard_setter = clipboard_setter or _system_clipboard_set

    def capture_source_content(self, instruction: str) -> str:
        try:
            return str(self.clipboard_getter() or "")
        except Exception:
            return ""

    def set_clipboard(self, text: str) -> bool:
        try:
            return bool(self.clipboard_setter(text))
        except Exception:
            return False

    def launch_or_focus_app(self, instruction: str) -> dict[str, Any]:
        app = "Sublime Text" if re.search(r"\bsublime\b", instruction or "", re.IGNORECASE) else ""
        return {"app": app, "focused": False, "strategy": "direct_file_preferred"}

    def create_or_open_file(self, instruction: str) -> str:
        downloads = os.path.join(self.home_dir, "Downloads")
        os.makedirs(downloads, exist_ok=True)
        filename = _extract_requested_filename(instruction) or "delegated-output.txt"
        return os.path.join(downloads, filename)

    def write_text(self, path: str, text: str) -> bool:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            return True
        except Exception:
            return False

    def verify_result(self, path: str, expected_text: str) -> bool:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read() == expected_text
        except Exception:
            return False

    def check_readiness(self) -> dict[str, Any]:
        downloads = os.path.join(self.home_dir, "Downloads")
        home_exists = os.path.isdir(self.home_dir)
        downloads_parent_writable = os.access(self.home_dir, os.W_OK) if home_exists else False
        return {
            "ready": home_exists and downloads_parent_writable,
            "detail": (
                "Direct desktop clipboard/filesystem route is available."
                if home_exists and downloads_parent_writable
                else "Direct desktop route cannot write to the configured home/Downloads path."
            ),
            "home_dir": self.home_dir,
            "downloads_dir": downloads,
            "home_exists": home_exists,
            "home_writable": downloads_parent_writable,
        }


class PlaywrightBrowserAdapter:
    """Browser workflow adapter backed by the existing Playwright tool."""

    def __init__(self, tool: Any = None) -> None:
        if tool is None:
            from distr.core.agent.tools.integrations.playwright_tool import PlaywrightTool

            tool = PlaywrightTool()
        self.tool = tool

    def execute(self, *, instruction: str, context: dict[str, Any]) -> dict[str, Any]:
        url = _extract_url(instruction)
        if not url:
            return {
                "success": False,
                "error": "A URL is required before Playwright can execute this delegated browser workflow.",
            }
        code = _navigation_playwright_code(url)
        output = str(
            self.tool._run(
                code=code,
                description=instruction,
                analyze_screenshot=True,
            )
        )
        success = not output.lstrip().startswith("\u2717") and "failed" not in output[:120].lower()
        screenshot_path = os.path.join(tempfile.gettempdir(), "pw_screenshots", "result.png")
        return {
            "success": success,
            "output": output,
            "url": url,
            "screenshot_path": screenshot_path,
        }

    def check_readiness(self) -> dict[str, Any]:
        installed = importlib.util.find_spec("playwright") is not None
        return {
            "ready": installed and hasattr(self.tool, "_run"),
            "detail": (
                "Playwright Python package is available."
                if installed
                else "Playwright Python package is not installed for browser automation."
            ),
            "playwright_python_installed": installed,
            "tool_available": hasattr(self.tool, "_run"),
        }


class ProjectCliDispatcher:
    """Dispatch delegated implementation scopes through existing project CLI backends."""

    def dispatch(self, *, backend_id: str, instruction: str, scope: dict[str, Any], context: dict[str, Any]) -> BackendTaskResult:
        project_id = context.get("project_id")
        if not project_id:
            return BackendTaskResult(
                success=False,
                backend_id=backend_id,
                engine="project_cli",
                error="project_id is required for Codex/Cursor handoff.",
            )
        try:
            project = self._load_project(int(project_id))
        except Exception as exc:
            return BackendTaskResult(
                success=False,
                backend_id=backend_id,
                engine="project_cli",
                error=f"Could not load project_id {project_id}: {exc}",
            )
        if project is None:
            return BackendTaskResult(
                success=False,
                backend_id=backend_id,
                engine="project_cli",
                error=f"Project {project_id} was not found.",
            )
        return _run_async_project_task(project, instruction, backend_id, context)

    def check_backend_status(self, backend_id: str) -> dict[str, Any]:
        try:
            from distr.core.project_cli_backends.registry import get_backend

            return get_backend(backend_id).setup_status().to_dict()
        except Exception as exc:
            return {
                "id": backend_id,
                "ready": False,
                "can_receive_remote_handoff": False,
                "message": f"Could not read {backend_id} backend status: {exc}",
            }

    def _load_project(self, project_id: int) -> Any:
        from distr.core.db import get_session
        from distr.core.db.projects import Project

        with get_session() as session:
            project = session.query(Project).filter(Project.id == project_id).first()
            if project is not None:
                session.expunge(project)
            return project


def _run_async_project_task(project: Any, instruction: str, backend_id: str, context: dict[str, Any]) -> BackendTaskResult:
    from distr.core.project_cli_backends.registry import run_project_task

    async def _call() -> BackendTaskResult:
        return await run_project_task(
            project,
            instruction,
            run_id=context.get("run_id"),
            workflow_id=context.get("workflow_id"),
            step_id=context.get("step_id"),
            origin="delegated_workflow",
            ticket_id=context.get("ticket_id"),
            backend_id_override=backend_id,
        )

    try:
        return asyncio.run(_call())
    except RuntimeError:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(_call())).result()


def _extract_requested_filename(instruction: str) -> str:
    text = instruction or ""
    for pattern in (
        r"\bcalled\s+([A-Za-z0-9._ -]+\.[A-Za-z0-9]{1,12})",
        r"\bnamed\s+([A-Za-z0-9._ -]+\.[A-Za-z0-9]{1,12})",
        r"\b(?:file|as)\s+([A-Za-z0-9._-]+\.[A-Za-z0-9]{1,12})",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _safe_desktop_filename(match.group(1))
    return ""


def _safe_desktop_filename(filename: str) -> str:
    name = os.path.basename(filename or "").strip()
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return name or "delegated-output.txt"


def _system_clipboard_get() -> str:
    try:
        result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2)
        return result.stdout if result.returncode == 0 else ""
    except Exception:
        return ""


def _system_clipboard_set(value: str) -> bool:
    try:
        result = subprocess.run(["pbcopy"], input=value or "", text=True, timeout=2)
        return result.returncode == 0
    except Exception:
        return False


def _extract_url(instruction: str) -> str:
    text = instruction or ""
    match = re.search(r"(?:https?|file)://[^\s'\"<>]+", text, flags=re.IGNORECASE)
    if match:
        return match.group(0).rstrip(".,);]")
    localhost = re.search(r"\b(?:localhost|127\.0\.0\.1):\d+(?:/[^\s'\"<>]*)?", text, flags=re.IGNORECASE)
    if localhost:
        return "http://" + localhost.group(0).rstrip(".,);]")
    return ""


def _navigation_playwright_code(url: str) -> str:
    escaped_url = url.replace("\\", "\\\\").replace("'", "\\'")
    return (
        "import os, tempfile\n"
        "from playwright.sync_api import sync_playwright\n"
        "pw_dir = os.path.join(tempfile.gettempdir(), 'pw_screenshots')\n"
        "os.makedirs(pw_dir, exist_ok=True)\n"
        "with sync_playwright() as p:\n"
        "    browser = p.chromium.launch(headless=True)\n"
        "    page = browser.new_page(viewport={'width': 1920, 'height': 1080})\n"
        f"    page.goto('{escaped_url}', wait_until='networkidle', timeout=30000)\n"
        "    print('title=' + page.title())\n"
        "    print('url=' + page.url)\n"
        "    page.screenshot(path=os.path.join(pw_dir, 'result.png'), full_page=True)\n"
        "    browser.close()\n"
    )
