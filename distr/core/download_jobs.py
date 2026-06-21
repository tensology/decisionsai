"""Download job registry with progress tracking for yt-dlp and similar tools."""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_JOB_HISTORY_FILE = Path.home() / ".decisions" / "download-jobs-history.json"
_MAX_HISTORY = 100
_lock = threading.Lock()
_jobs: Dict[str, Dict[str, Any]] = {}
_processes: Dict[str, Any] = {}
_progress_re = re.compile(
    r"\[download\]\s+(?:(\d+(?:\.\d+)?)% of\s+~?([\d.]+\w+)|(\d+(?:\.\d+)?)% of\s+([\d.]+\w+))"
    r"(?:\s+at\s+([\d.]+\w+/s))?(?:\s+ETA\s+(\S+))?",
    re.I,
)


def _now() -> float:
    return time.time()


def _default_download_dir() -> str:
    return str(Path.home() / "Downloads" / "DecisionsAI")


def _load_history() -> None:
    if not _JOB_HISTORY_FILE.exists():
        return
    try:
        rows = json.loads(_JOB_HISTORY_FILE.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            return
        with _lock:
            for row in rows[-_MAX_HISTORY:]:
                if isinstance(row, dict) and row.get("id"):
                    _jobs[row["id"]] = row
    except Exception as exc:
        logger.warning("Could not load download job history: %s", exc)


def _persist_history() -> None:
    try:
        _JOB_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            done = [
                dict(row)
                for row in _jobs.values()
                if row.get("status") in {"completed", "failed", "cancelled"}
            ]
        done.sort(key=lambda r: r.get("updated_at") or 0, reverse=True)
        _JOB_HISTORY_FILE.write_text(
            json.dumps(done[:_MAX_HISTORY], indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Could not persist download job history: %s", exc)


def _public_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "status": row.get("status"),
        "progress": row.get("progress", 0),
        "bytes_downloaded": row.get("bytes_downloaded"),
        "bytes_total": row.get("bytes_total"),
        "speed": row.get("speed"),
        "eta": row.get("eta"),
        "url": row.get("url"),
        "urls": row.get("urls") or [],
        "output_dir": row.get("output_dir"),
        "current_file": row.get("current_file"),
        "message": row.get("message"),
        "error": row.get("error"),
        "files": row.get("files") or [],
        "file_items": row.get("file_items") or [],
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def list_jobs(*, include_completed: bool = True) -> List[Dict[str, Any]]:
    with _lock:
        rows = list(_jobs.values())
    rows.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    if not include_completed:
        rows = [r for r in rows if r.get("status") in {"queued", "running"}]
    return [_public_row(r) for r in rows]


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        row = _jobs.get(job_id)
        return _public_row(dict(row)) if row else None


def _basename(path: str) -> str:
    return Path(path or "").name if path else ""


def _ensure_file_item(job_id: str, file_path: str, *, status: str = "running") -> None:
    with _lock:
        row = _jobs.get(job_id)
        if not row:
            return
        items = list(row.get("file_items") or [])
        for item in items:
            if item.get("path") == file_path:
                item["status"] = status
                item["name"] = _basename(file_path)
                row["file_items"] = items
                row["updated_at"] = _now()
                return
        items.append({
            "path": file_path,
            "name": _basename(file_path),
            "status": status,
            "progress": 0.0,
            "speed": "",
            "eta": "",
            "error": "",
        })
        row["file_items"] = items
        row["updated_at"] = _now()


def _complete_current_file(job_id: str) -> None:
    with _lock:
        row = _jobs.get(job_id)
        if not row:
            return
        current = row.get("current_file") or ""
        items = list(row.get("file_items") or [])
        changed = False
        for item in items:
            if item.get("path") == current and item.get("status") not in {"completed", "failed", "cancelled"}:
                item["status"] = "completed"
                item["progress"] = 100.0
                item["speed"] = ""
                item["eta"] = ""
                changed = True
        if changed:
            row["file_items"] = items
            row["updated_at"] = _now()


def _update_current_file_progress(job_id: str, *, progress: float, speed: str = "", eta: str = "") -> None:
    with _lock:
        row = _jobs.get(job_id)
        if not row:
            return
        current = row.get("current_file") or ""
        items = list(row.get("file_items") or [])
        changed = False
        for item in items:
            if item.get("path") == current:
                item["status"] = "running"
                item["progress"] = progress
                item["speed"] = speed
                item["eta"] = eta
                changed = True
        if changed:
            row["file_items"] = items
            row["updated_at"] = _now()


def _mark_job_files(job_id: str, *, status: str, error: str = "") -> None:
    with _lock:
        row = _jobs.get(job_id)
        if not row:
            return
        items = list(row.get("file_items") or [])
        for item in items:
            if item.get("status") in {"completed", "failed", "cancelled"}:
                continue
            item["status"] = status
            item["error"] = error
            item["speed"] = ""
            item["eta"] = ""
        row["file_items"] = items
        row["updated_at"] = _now()


def _update_job(job_id: str, **fields: Any) -> None:
    with _lock:
        row = _jobs.get(job_id)
        if not row:
            return
        row.update(fields)
        row["updated_at"] = _now()


def create_job(
    urls: List[str],
    *,
    title: str = "",
    output_dir: Optional[str] = None,
) -> str:
    clean_urls = [u.strip() for u in urls if (u or "").strip()]
    if not clean_urls:
        raise ValueError("At least one URL is required")

    job_id = secrets.token_urlsafe(9)
    label = (title or "").strip()
    if not label:
        label = f"Download ({len(clean_urls)} item{'s' if len(clean_urls) != 1 else ''})"

    out_dir = (output_dir or "").strip() or _default_download_dir()
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    row = {
        "id": job_id,
        "title": label[:200],
        "status": "queued",
        "progress": 0.0,
        "bytes_downloaded": None,
        "bytes_total": None,
        "speed": "",
        "eta": "",
        "url": clean_urls[0],
        "urls": clean_urls,
        "output_dir": out_dir,
        "current_file": "",
        "message": "Queued",
        "error": "",
        "files": [],
        "file_items": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    with _lock:
        _jobs[job_id] = row
    return job_id


def cancel_job(job_id: str) -> bool:
    proc = None
    row = None
    with _lock:
        proc = _processes.pop(job_id, None)
        row = _jobs.get(job_id)
        if row and row.get("status") in {"queued", "running"}:
            row["status"] = "cancelled"
            row["message"] = "Cancelled"
            row["updated_at"] = _now()
            for item in row.get("file_items") or []:
                if item.get("status") not in {"completed", "failed"}:
                    item["status"] = "cancelled"
                    item["speed"] = ""
                    item["eta"] = ""
    if proc is not None:
        try:
            proc.terminate()
        except Exception:
            pass
    _persist_history()
    return proc is not None or (row is not None)


def remove_job(job_id: str) -> bool:
    with _lock:
        row = _jobs.get(job_id)
        if not row or row.get("status") in {"queued", "running"}:
            return False
        _jobs.pop(job_id, None)
    _persist_history()
    return True


def clear_inactive_jobs() -> int:
    removed = 0
    with _lock:
        inactive_ids = [job_id for job_id, row in _jobs.items() if row.get("status") not in {"queued", "running"}]
        for job_id in inactive_ids:
            _jobs.pop(job_id, None)
            removed += 1
    if removed:
        _persist_history()
    return removed


def reveal_file_in_folder(job_id: str, file_path: str) -> bool:
    with _lock:
        row = _jobs.get(job_id)
        if not row:
            return False
        allowed = {str(p) for p in (row.get("files") or [])}
        allowed.update(str(item.get("path") or "") for item in (row.get("file_items") or []))
    if file_path not in allowed:
        return False
    target = Path(file_path)
    if not target.exists():
        target = Path(file_path).parent
    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.run(["open", "-R", str(target)], check=False, timeout=5)
        elif system == "Windows":
            subprocess.run(["explorer", "/select,", str(target)], check=False, timeout=5)
        else:
            folder = str(target.parent if target.is_file() else target)
            subprocess.run(["xdg-open", folder], check=False, timeout=5)
        return True
    except Exception as exc:
        logger.warning("Could not reveal download file: %s", exc)
        return False


def parse_ytdlp_progress(line: str) -> Optional[Dict[str, Any]]:
    m = _progress_re.search(line)
    if not m:
        return None
    pct = m.group(1) or m.group(3)
    total = m.group(2) or m.group(4)
    return {
        "progress": float(pct) if pct else 0.0,
        "bytes_total": total,
        "speed": m.group(5) or "",
        "eta": m.group(6) or "",
    }


def start_ytdlp_job(job_id: str) -> None:
    thread = threading.Thread(target=_run_ytdlp_job, args=(job_id,), daemon=True)
    thread.start()


def _run_ytdlp_job(job_id: str) -> None:
    import subprocess

    from distr.core.yt_dlp_support import ytdlp_command

    with _lock:
        row = _jobs.get(job_id)
        if not row:
            return
        urls = list(row.get("urls") or [])
        output_dir = row.get("output_dir") or _default_download_dir()

    if not urls:
        _update_job(job_id, status="failed", error="No URLs", message="Failed")
        _persist_history()
        return

    _update_job(job_id, status="running", message="Starting download…", progress=0.0)

    output_template = str(Path(output_dir) / "%(title).200B [%(id)s].%(ext)s")
    cmd = [
        *ytdlp_command(),
        "--newline",
        "--progress",
        "--no-playlist",
        "-o",
        output_template,
        *urls,
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as exc:
        _update_job(job_id, status="failed", error=str(exc), message="Failed to start yt-dlp")
        _persist_history()
        return

    with _lock:
        _processes[job_id] = proc

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if not line:
                continue
            if line.startswith("[download] Destination:"):
                dest = line.split("Destination:", 1)[-1].strip()
                _complete_current_file(job_id)
                _ensure_file_item(job_id, dest, status="running")
                _update_job(job_id, current_file=dest, message=f"Downloading {Path(dest).name}")
            elif "has already been downloaded" in line:
                already = line.split("has already been downloaded", 1)[0].replace("[download]", "").strip()
                if already:
                    maybe_path = str(Path(output_dir) / already)
                    _ensure_file_item(job_id, maybe_path, status="completed")
                    _complete_current_file(job_id)
                _update_job(job_id, message=line[:200])
            elif line.startswith("[Merger]") or line.startswith("[ExtractAudio]"):
                _update_job(job_id, message=line[:200])
            elif line.startswith("[download]") and "100%" in line:
                _update_current_file_progress(job_id, progress=100.0)
                _update_job(job_id, progress=100.0, message="Finishing…")
            else:
                parsed = parse_ytdlp_progress(line)
                if parsed:
                    _update_current_file_progress(
                        job_id,
                        progress=parsed["progress"],
                        speed=parsed.get("speed") or "",
                        eta=parsed.get("eta") or "",
                    )
                    _update_job(
                        job_id,
                        progress=parsed["progress"],
                        bytes_total=parsed.get("bytes_total"),
                        speed=parsed.get("speed") or "",
                        eta=parsed.get("eta") or "",
                        message=line[:200],
                    )
        rc = proc.wait()
    finally:
        with _lock:
            _processes.pop(job_id, None)

    with _lock:
        latest_row = _jobs.get(job_id) or {}
        collected_files = [
            str(item.get("path") or "")
            for item in (latest_row.get("file_items") or [])
            if str(item.get("path") or "").strip()
        ][:20]

    if rc == 0:
        _complete_current_file(job_id)
        for file_path in collected_files:
            _ensure_file_item(job_id, file_path, status="completed")
        _update_job(
            job_id,
            status="completed",
            progress=100.0,
            message="Download complete",
            files=collected_files,
            error="",
        )
    else:
        _mark_job_files(job_id, status="failed", error=f"yt-dlp exited with code {rc}")
        _update_job(
            job_id,
            status="failed",
            error=f"yt-dlp exited with code {rc}",
            message="Download failed",
        )
    _persist_history()


_load_history()
