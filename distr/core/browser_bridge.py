"""Browser/Playwright artifact sessions tied to the orchestration ledger."""

from __future__ import annotations

import json
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from distr.core.orchestration_events import emit_orchestration_event


BROWSER_BRIDGE_ROOT = Path(tempfile.gettempdir()) / "decisions-browser-bridge"


@dataclass(frozen=True)
class BrowserArtifactSession:
    session_id: str
    artifact_dir: str
    surface: str = "browser"
    project_id: int | None = None
    workflow_id: int | None = None
    run_id: int | None = None
    step_id: int | None = None
    execution_session_id: int | None = None
    correlation_id: str = ""

    @property
    def console_log_path(self) -> str:
        return str(Path(self.artifact_dir) / "console.json")

    def screenshot_path(self, filename: str = "result.png") -> str:
        safe_name = Path(filename or "result.png").name
        return str(Path(self.artifact_dir) / safe_name)


def create_browser_artifact_session(
    *,
    surface: str = "browser",
    project_id: int | None = None,
    workflow_id: int | None = None,
    run_id: int | None = None,
    step_id: int | None = None,
    execution_session_id: int | None = None,
    correlation_id: str = "",
) -> BrowserArtifactSession:
    session_id = f"browser_{uuid.uuid4().hex[:12]}"
    artifact_dir = BROWSER_BRIDGE_ROOT / session_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return BrowserArtifactSession(
        session_id=session_id,
        artifact_dir=str(artifact_dir),
        surface=(surface or "browser").strip().lower(),
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=step_id,
        execution_session_id=execution_session_id,
        correlation_id=correlation_id or session_id,
    )


def write_browser_session_manifest(session: BrowserArtifactSession, extra: dict[str, Any] | None = None) -> str:
    path = Path(session.artifact_dir) / "manifest.json"
    payload = {
        "session_id": session.session_id,
        "surface": session.surface,
        "artifact_dir": session.artifact_dir,
        "console_log_path": session.console_log_path,
        "project_id": session.project_id,
        "workflow_id": session.workflow_id,
        "run_id": session.run_id,
        "step_id": session.step_id,
        "execution_session_id": session.execution_session_id,
        "correlation_id": session.correlation_id,
        **(extra or {}),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return str(path)


def record_browser_snapshot(
    session: BrowserArtifactSession,
    *,
    status: str = "completed",
    summary: str = "Browser snapshot captured.",
    url: str = "",
    screenshot_path: str = "",
    console_logs: dict[str, Any] | None = None,
    network_logs: dict[str, Any] | None = None,
    viewport: dict[str, Any] | None = None,
) -> int | None:
    payload = {
        "surface": "browser",
        "subtype": "browser_snapshot_captured",
        "browser_surface": session.surface,
        "browser_session_id": session.session_id,
        "artifact_dir": session.artifact_dir,
        "screenshot_path": screenshot_path or "",
        "console_log_path": session.console_log_path,
        "url": url or "",
        "viewport": viewport or {},
        "correlation_id": session.correlation_id,
        "is_workflow_attached": bool(session.workflow_id or session.run_id or session.step_id),
    }
    evidence = {
        "screenshot_path": screenshot_path or "",
        "console_logs": console_logs or {},
        "network_logs": network_logs or {},
    }
    return emit_orchestration_event(
        source="browser",
        event_type="browser_snapshot_captured",
        status=status,
        workflow_id=session.workflow_id,
        run_id=session.run_id,
        step_id=session.step_id,
        project_id=session.project_id,
        execution_session_id=session.execution_session_id,
        summary=summary,
        payload=payload,
        evidence=evidence,
    )
