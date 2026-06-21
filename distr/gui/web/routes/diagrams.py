"""
Mermaid diagram storage, history, and viewer API.
"""

from __future__ import annotations

import json
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

_DIAGRAM_TTL_SECONDS = 3600
_HISTORY_FILE = Path.home() / ".decisions" / "mermaid-history.json"
_MAX_HISTORY = 80
_diagram_lock = threading.Lock()
_diagram_store: Dict[str, Dict[str, Any]] = {}


def _purge_expired(now: Optional[float] = None) -> None:
    ts = now if now is not None else time.time()
    expired = [key for key, row in _diagram_store.items() if row.get("expires_at", 0) <= ts]
    for key in expired:
        _diagram_store.pop(key, None)


def _load_history_rows() -> List[Dict[str, Any]]:
    if not _HISTORY_FILE.exists():
        return []
    try:
        rows = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _save_history_rows(rows: List[Dict[str, Any]]) -> None:
    try:
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _HISTORY_FILE.write_text(json.dumps(rows[:_MAX_HISTORY], indent=2), encoding="utf-8")
    except Exception:
        pass


def append_diagram_history(diagram_id: str, code: str, title: str = "Diagram") -> None:
    """Persist a rendered diagram to local history (survives TTL expiry)."""
    now = time.time()
    entry = {
        "id": diagram_id,
        "title": (title or "Diagram").strip()[:200] or "Diagram",
        "code": code or "",
        "created_at": now,
    }
    rows = _load_history_rows()
    rows = [r for r in rows if r.get("id") != diagram_id]
    rows.insert(0, entry)
    _save_history_rows(rows)


def list_diagram_history() -> List[Dict[str, Any]]:
    return _load_history_rows()[:_MAX_HISTORY]


def delete_diagram_entry(diagram_id: str) -> bool:
    """Delete a diagram from memory and persisted history."""
    removed = False
    with _diagram_lock:
        if _diagram_store.pop(diagram_id, None) is not None:
            removed = True
    rows = _load_history_rows()
    filtered_rows = [row for row in rows if row.get("id") != diagram_id]
    if len(filtered_rows) != len(rows):
        removed = True
        _save_history_rows(filtered_rows)
    return removed


def store_diagram(code: str, title: str = "Diagram") -> str:
    diagram_id = secrets.token_urlsafe(9)
    now = time.time()
    with _diagram_lock:
        _purge_expired(now)
        _diagram_store[diagram_id] = {
            "title": (title or "Diagram").strip()[:200] or "Diagram",
            "code": code or "",
            "created_at": now,
            "expires_at": now + _DIAGRAM_TTL_SECONDS,
        }
    append_diagram_history(diagram_id, code, title)
    return diagram_id


def get_diagram(diagram_id: str) -> Optional[Dict[str, Any]]:
    now = time.time()
    with _diagram_lock:
        _purge_expired(now)
        row = _diagram_store.get(diagram_id)
        if row:
            if row.get("expires_at", 0) <= now:
                _diagram_store.pop(diagram_id, None)
            else:
                return dict(row)
    for row in _load_history_rows():
        if row.get("id") == diagram_id:
            return dict(row)
    return None


class DiagramCreateRequest(BaseModel):
    code: str = Field(..., description="Mermaid diagram source")
    title: str = Field("Diagram", description="Window title for the viewer")


class DiagramGoogleExportRequest(BaseModel):
    svg: str = Field(..., description="Rendered SVG markup")
    title: str = Field("Diagram", description="Google Drive file name")
    folder_id: str = Field("root", description="Google Drive folder id")


def create_routes(templates_dir: Path, base_path: str = "") -> APIRouter:
    router = APIRouter()

    @router.get("/diagrams/history")
    async def diagram_history():
        return {"items": list_diagram_history()}

    @router.post("/diagrams")
    async def create_diagram(body: DiagramCreateRequest):
        code = (body.code or "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="code is required")
        diagram_id = store_diagram(code, body.title)
        return {"id": diagram_id, "viewer_path": f"/diagram/?id={diagram_id}"}

    @router.get("/diagrams/{diagram_id}")
    async def read_diagram(diagram_id: str):
        row = get_diagram(diagram_id)
        if not row:
            raise HTTPException(status_code=404, detail="Diagram not found or expired")
        return {
            "id": diagram_id,
            "title": row.get("title") or "Diagram",
            "code": row.get("code") or "",
            "created_at": row.get("created_at"),
        }

    @router.delete("/diagrams/{diagram_id}")
    async def delete_diagram(diagram_id: str):
        if not delete_diagram_entry(diagram_id):
            raise HTTPException(status_code=404, detail="Diagram not found")
        return {"success": True}

    @router.post("/diagrams/export/google-drawing")
    async def export_diagram_google_drawing(body: DiagramGoogleExportRequest):
        svg = (body.svg or "").strip()
        if not svg:
            raise HTTPException(status_code=400, detail="svg is required")
        try:
            from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector

            connector = GoogleWorkspaceConnector()
            if not connector.is_connected():
                raise HTTPException(
                    status_code=400,
                    detail="Google account not connected. Connect Google in Settings → Advanced.",
                )
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".svg",
                delete=False,
                encoding="utf-8",
            ) as tmp:
                tmp.write(svg)
                svg_path = tmp.name
            file_id = connector.upload_image_as_google_drawing(
                svg_path,
                folder_id=(body.folder_id or "root").strip() or "root",
                name=(body.title or "Diagram").strip() or "Diagram",
            )
            Path(svg_path).unlink(missing_ok=True)
            if not file_id:
                raise HTTPException(status_code=500, detail="Google Drive export failed")
            return {
                "file_id": file_id,
                "url": f"https://docs.google.com/drawings/d/{file_id}/edit",
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return router
