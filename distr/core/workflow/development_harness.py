"""Direct, conversation-first execution for Development threads."""

from __future__ import annotations

import asyncio
import logging
import hashlib
import json
import os
import mimetypes
import re
import subprocess
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from distr.core.chat import ChatService, _chat_params
from distr.core.db import Chat, get_session
from distr.core.db.projects import Project
from distr.core.db.time import utc_now_naive
from distr.core.workflow.development_threads import development_thread_metadata

logger = logging.getLogger(__name__)

ACTIVE_EXECUTION_STATUSES = {"initializing", "queued", "running", "waiting"}
CLI_BACKENDS = {"pi", "cursor", "codex", "claude_code"}
# Local Ollama workers appear beside Pi/Cursor/Codex. The picker source of truth
# is the live installed catalog (ollama list /api/tags) — not a hard-coded list.
# Optional label hints + legacy alias ids only; models are never pulled here.
LOCAL_OLLAMA_WORKER_PREFIX = "local_ollama:"
LOCAL_OLLAMA_LABEL_HINTS = {
    "qwen3.8:27b": "Qwen 3.8 27B",
    "muse-glimmer:30b-mlx": "Glimmer",
}
# Older UI pinned these synthetic harness ids; map them to exact Ollama tags.
LEGACY_LOCAL_WORKER_ALIASES = {
    "qwen38_27b": "qwen3.8:27b",
    "glimmer": "muse-glimmer:30b-mlx",
}
# Kept for callers/tests that still import the old name; not the picker source of truth.
LOCAL_HARNESS_PRESETS = {
    alias: {
        "backend": "pi",
        "model": model,
        "provider": "ollama",
        "label": LOCAL_OLLAMA_LABEL_HINTS.get(model, model),
    }
    for alias, model in LEGACY_LOCAL_WORKER_ALIASES.items()
}


def local_ollama_worker_id(model: str) -> str:
    tag = str(model or "").strip()
    return f"{LOCAL_OLLAMA_WORKER_PREFIX}{tag}" if tag else ""


def parse_local_ollama_worker(backend: str | None) -> str | None:
    """Return the Ollama model tag for a local-worker harness id, else None."""
    raw = str(backend or "").strip()
    if not raw:
        return None
    lower = raw.lower()
    if lower.startswith(LOCAL_OLLAMA_WORKER_PREFIX):
        tag = raw[len(LOCAL_OLLAMA_WORKER_PREFIX) :].strip()
        return tag or None
    legacy = LEGACY_LOCAL_WORKER_ALIASES.get(lower)
    if legacy:
        return legacy
    preset = LOCAL_HARNESS_PRESETS.get(lower)
    if preset:
        return str(preset.get("model") or "").strip() or None
    return None


def local_ollama_worker_label(model: str) -> str:
    tag = str(model or "").strip()
    if not tag:
        return "Ollama"
    return LOCAL_OLLAMA_LABEL_HINTS.get(tag, tag)


def _is_excluded_local_worker_model(model: str) -> bool:
    lower = str(model or "").strip().lower()
    if not lower:
        return True
    if any(token in lower for token in ("embed", "embedding", "nomic-embed")):
        return True
    # Cloud tags stay selectable via Pi + provider; local workers are on-box only.
    # Covers ":cloud", "80b-cloud", "24b-cloud", etc.
    tag = lower.rsplit(":", 1)[-1]
    if "cloud" in tag:
        return True
    return False


def _ollama_reachable_installed_ids() -> set[str] | None:
    """Return installed Ollama model ids when the daemon is reachable.

    Returns None when Ollama cannot be listed (not installed / not running),
    so callers can fail closed instead of treating absence as an empty catalog.
    """
    ids: set[str] = set()
    listed = False
    try:
        from distr.gui.utils.get_ollama_models import get_installed_ollama_models

        for entry in get_installed_ollama_models() or []:
            mid = str(entry.get("id") or "").strip()
            if mid:
                ids.add(mid)
                listed = True
    except Exception:
        logger.debug("get_installed_ollama_models failed for local harness probe", exc_info=True)
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            listed = True
            for line in result.stdout.strip().splitlines()[1:]:
                parts = line.split()
                if parts:
                    ids.add(parts[0])
    except Exception:
        logger.debug("ollama list failed for local harness probe", exc_info=True)
    if not listed:
        try:
            import urllib.request

            host = (os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
            if not host.startswith(("http://", "https://")):
                host = "http://" + host
            req = urllib.request.Request(host + "/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
            listed = True
            for entry in payload.get("models") or []:
                mid = str(entry.get("name") or entry.get("model") or "").strip()
                if mid:
                    ids.add(mid)
        except Exception:
            logger.debug("ollama /api/tags failed for local harness probe", exc_info=True)
    if not listed:
        return None
    return ids


def local_harness_model_is_available(
    model: str,
    *,
    installed: set[str] | None = None,
) -> bool:
    """True when the exact Ollama tag is installed and Ollama is reachable."""
    tag = str(model or "").strip()
    if not tag or _is_excluded_local_worker_model(tag):
        return False
    ids = installed if installed is not None else _ollama_reachable_installed_ids()
    if ids is None:
        return False
    return tag in ids


def local_harness_preset_is_available(
    preset_id: str,
    *,
    installed: set[str] | None = None,
) -> bool:
    """Compatibility wrapper: legacy alias or local_ollama:<tag> worker ids."""
    tag = parse_local_ollama_worker(preset_id)
    if not tag:
        return False
    return local_harness_model_is_available(tag, installed=installed)


def available_local_harness_workers() -> list[dict[str, Any]]:
    """Dynamic local workers for the picker = installed on-box Ollama chat models."""
    installed = _ollama_reachable_installed_ids()
    if installed is None:
        return []
    rows: list[dict[str, Any]] = []
    for tag in sorted(installed, key=lambda m: (local_ollama_worker_label(m).lower(), m.lower())):
        if _is_excluded_local_worker_model(tag):
            continue
        rows.append(
            {
                "id": local_ollama_worker_id(tag),
                "backend": "pi",
                "model": tag,
                "provider": "ollama",
                "label": local_ollama_worker_label(tag),
                "installed": True,
                "reachable": True,
            }
        )
    return rows


def available_local_harness_presets() -> list[dict[str, Any]]:
    """Alias for available_local_harness_workers (legacy name)."""
    return available_local_harness_workers()


def resolve_local_harness_selection(backend: str | None, model_name: str | None = None) -> dict[str, Any] | None:
    """Resolve a manual local-worker selection into Pi + Ollama route fields."""
    tag = parse_local_ollama_worker(backend)
    if not tag:
        # Also accept explicit pi+ollama pin that originated from a local worker.
        return None
    return {
        "worker_id": local_ollama_worker_id(tag),
        "backend": "pi",
        "model": tag,
        "provider": "ollama",
        "label": local_ollama_worker_label(tag),
        "local_harness_preset": local_ollama_worker_id(tag),
    }


def _unload_ollama_model(model: str) -> None:
    """Best-effort unload after a local harness preset finishes (no pulls)."""
    model_id = str(model or "").strip()
    if not model_id:
        return
    try:
        import json
        import urllib.request

        host = (os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
        if not host.startswith(("http://", "https://")):
            host = "http://" + host
        payload = json.dumps({"model": model_id, "keep_alive": 0}).encode("utf-8")
        req = urllib.request.Request(
            host + "/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            response.read()
        logger.info("Unloaded local harness model %s (keep_alive=0)", model_id)
    except Exception:
        logger.debug("Could not unload local harness model %s", model_id, exc_info=True)



def _output_justifies_noop(output: str) -> bool:
    lowered = str(output or "").strip().lower()
    return any(token in lowered for token in ("no changes needed", "already", "nothing to"))


def _turn_outcome(
    *,
    backend_id: str,
    runtime_id: str,
    success: bool,
    result: Any,
    changes: dict[str, Any],
    summary: str,
    noop: bool = False,
) -> dict[str, Any]:
    evidence = result.evidence if isinstance(getattr(result, "evidence", None), dict) else {}
    tool_counts = evidence.get("tool_counts") if isinstance(evidence.get("tool_counts"), dict) else {}
    files = changes.get("files") if isinstance(changes.get("files"), list) else []
    payload = {
        "backend_id": str(backend_id or ""),
        "runtime_id": str(runtime_id or ""),
        "success": bool(success),
        "tool_counts": tool_counts,
        "git_change_count": len(files),
        "summary": str(summary or "")[:2000],
    }
    if noop:
        payload["noop"] = True
    return payload


def _prompt_expects_project_change(prompt: str, *, autonomy_level: str) -> bool:
    """Conservatively identify direct implementation requests.

    This is a runtime completion guard, not an intent router. It prevents an
    agent from treating an offer to do the work as successful completion.
    """
    if str(autonomy_level or "").strip().lower() == "plan":
        return False
    clean = re.sub(r"\s+", " ", str(prompt or "")).strip().lower()
    if not clean:
        return False
    if re.match(r"^(?:what|why|how|where|when|who|explain|describe|tell me|review|audit|inspect)\b", clean):
        return False
    return bool(
        re.search(
            r"\b(?:add|build|change|create|delete|edit|fix|give\s+(?:this|the|my)\b|implement|make|move|refactor|remove|rename|replace|set|update|write)\b",
            clean,
        )
    )


def _git_project_root(folder: str) -> Path | None:
    path = Path(str(folder or "")).expanduser()
    if not path.is_dir():
        return None
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode:
        return None
    root = Path(result.stdout.strip()).resolve()
    return root if root.is_dir() else None


def _git_status_snapshot(folder: str) -> dict[str, str]:
    root = _git_project_root(folder)
    if root is None:
        return {}
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        return {}
    snapshot: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2], line[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        path = path.strip('"')
        if path:
            snapshot[path] = status
    return snapshot


def _git_untracked_fingerprints(folder: str, status: dict[str, str] | None = None) -> dict[str, str]:
    """Fingerprint untracked files so mid-run edits stay visible.

    Git porcelain keeps ``??`` after content changes. Status-only baselines
    therefore hide edits to files that were already untracked when the turn
    started. Content hashes close that gap for the harness change counter.
    """
    root = _git_project_root(folder)
    if root is None:
        return {}
    snapshot = status if status is not None else _git_status_snapshot(str(root))
    fingerprints: dict[str, str] = {}
    for relative, entry_status in snapshot.items():
        if entry_status != "??":
            continue
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            fingerprints[relative] = _file_fingerprint(candidate)
    return fingerprints


def _file_fingerprint(path: Path) -> str:
    if not path.exists():
        return "missing"
    if not path.is_file():
        return "unsupported"
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _turn_change_manifest(
    folder: str,
    before: dict[str, str],
    *,
    before_untracked: dict[str, str] | None = None,
) -> dict[str, Any]:
    root = _git_project_root(folder)
    if root is None:
        return {"files": [], "additions": 0, "deletions": 0, "reversible": False}
    after = _git_status_snapshot(str(root))
    before_fps = {
        str(path): str(digest)
        for path, digest in dict(before_untracked or {}).items()
        if path and digest
    }
    after_fps = _git_untracked_fingerprints(str(root), after)

    def _untracked_content_changed(path: str) -> bool:
        return (
            before.get(path) == "??"
            and after.get(path) == "??"
            and bool(before_fps.get(path))
            and before_fps.get(path) != after_fps.get(path)
        )

    paths = sorted(
        path
        for path in after
        if before.get(path) != after.get(path) or _untracked_content_changed(path)
    )
    files: list[dict[str, Any]] = []
    total_additions = 0
    total_deletions = 0
    for relative in paths:
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        status = after[relative]
        additions = deletions = 0
        if status == "??" and candidate.is_file():
            try:
                additions = len(candidate.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                additions = 0
            # Content-only edits to an already-untracked file still need a
            # non-zero counter so Studio does not classify the turn as noop.
            if additions == 0 and _untracked_content_changed(relative):
                additions = 1
        else:
            result = subprocess.run(
                ["git", "-C", str(root), "diff", "--numstat", "HEAD", "--", relative],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if result.stdout.strip():
                columns = result.stdout.splitlines()[-1].split("\t", 2)
                if len(columns) >= 2:
                    additions = int(columns[0]) if columns[0].isdigit() else 0
                    deletions = int(columns[1]) if columns[1].isdigit() else 0
        total_additions += additions
        total_deletions += deletions
        files.append(
            {
                "path": relative,
                "status": status,
                "additions": additions,
                "deletions": deletions,
                "after_fingerprint": _file_fingerprint(candidate),
                "reversible": relative not in before and status in {"??", " M", "M ", "MM", " D", "D "},
            }
        )
    return {
        "files": files,
        "additions": total_additions,
        "deletions": total_deletions,
        "reversible": bool(files) and all(item["reversible"] for item in files),
        "undone": False,
    }


_DIFF_HUNK_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?P<label>.*)$"
)


def _structured_diff_files(diff: str, manifest_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn a unified diff into compact, line-addressable review data."""
    manifest = {str(item.get("path") or ""): item for item in manifest_files}
    sections: dict[str, list[str]] = {}
    current_path = ""
    current_lines: list[str] = []

    def finish_section() -> None:
        if current_path:
            sections[current_path] = list(current_lines)

    for line in str(diff or "").splitlines():
        if line.startswith("diff --git "):
            finish_section()
            current_lines = [line]
            match = re.match(r"^diff --git a/(.+) b/(.+)$", line)
            current_path = match.group(2) if match else ""
            continue
        if current_path:
            current_lines.append(line)
            if line.startswith("+++ b/"):
                current_path = line[6:]
    finish_section()

    review_files: list[dict[str, Any]] = []
    for path, item in manifest.items():
        section = sections.get(path, [])
        hunks: list[dict[str, Any]] = []
        active_hunk: dict[str, Any] | None = None
        old_line = new_line = 0
        binary = any(line.startswith("Binary files ") or line == "GIT binary patch" for line in section)
        for line in section:
            match = _DIFF_HUNK_RE.match(line)
            if match:
                old_line = int(match.group(1))
                new_line = int(match.group(3))
                active_hunk = {
                    "header": line,
                    "old_start": old_line,
                    "new_start": new_line,
                    "lines": [],
                }
                hunks.append(active_hunk)
                continue
            if active_hunk is None or line.startswith(("---", "+++")):
                continue
            if line.startswith("+"):
                active_hunk["lines"].append(
                    {"kind": "addition", "old_line": None, "new_line": new_line, "content": line[1:]}
                )
                new_line += 1
            elif line.startswith("-"):
                active_hunk["lines"].append(
                    {"kind": "deletion", "old_line": old_line, "new_line": None, "content": line[1:]}
                )
                old_line += 1
            elif line.startswith(" "):
                active_hunk["lines"].append(
                    {"kind": "context", "old_line": old_line, "new_line": new_line, "content": line[1:]}
                )
                old_line += 1
                new_line += 1
            elif line.startswith("\\"):
                active_hunk["lines"].append(
                    {"kind": "meta", "old_line": None, "new_line": None, "content": line}
                )
        review_files.append(
            {
                **item,
                "path": path,
                "binary": binary,
                "diff": "\n".join(section),
                "hunks": hunks,
            }
        )
    return review_files


def development_change_review(chat_id: int) -> dict[str, Any]:
    project, metadata, _, _ = _project_and_scope(int(chat_id))
    execution = dict(metadata.get("execution") or {})
    changes = dict(execution.get("changes") or {})
    files = [item for item in changes.get("files", []) if isinstance(item, dict) and item.get("path")]
    root = _git_project_root(str(project.folder_location or ""))
    if root is None or not files:
        return {**changes, "diff": ""}
    paths = [str(item["path"]) for item in files]
    result = subprocess.run(
        ["git", "-C", str(root), "diff", "--no-ext-diff", "--unified=3", "HEAD", "--", *paths],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    diff = result.stdout
    for item in files:
        if item.get("status") != "??":
            continue
        path = (root / str(item["path"])).resolve()
        if path.is_file():
            body = path.read_text(encoding="utf-8", errors="replace")
            diff += f"\ndiff --git a/{item['path']} b/{item['path']}\nnew file mode 100644\n--- /dev/null\n+++ b/{item['path']}\n"
            line_count = len(body.splitlines())
            diff += f"@@ -0,0 +1,{line_count} @@\n"
            diff += "".join(f"+{line}\n" for line in body.splitlines())
    diff = diff[-120_000:]
    return {**changes, "diff": diff, "review_files": _structured_diff_files(diff, files)}


def undo_development_changes(chat_id: int) -> dict[str, Any]:
    project, metadata, _, _ = _project_and_scope(int(chat_id))
    execution = dict(metadata.get("execution") or {})
    changes = dict(execution.get("changes") or {})
    files = [item for item in changes.get("files", []) if isinstance(item, dict)]
    if changes.get("undone"):
        return {"ok": True, "already_undone": True, "changes": changes}
    if not files or not changes.get("reversible"):
        raise ValueError("This turn does not have an isolated, reversible change set.")
    root = _git_project_root(str(project.folder_location or ""))
    if root is None:
        raise ValueError("The project repository is not available.")
    for item in files:
        path = (root / str(item.get("path") or "")).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("A reported file is outside the project repository.") from exc
        if _file_fingerprint(path) != item.get("after_fingerprint"):
            raise ValueError(f"{item.get('path')} changed after this turn. Review it before undoing.")
    for item in files:
        relative = str(item["path"])
        path = (root / relative).resolve()
        if item.get("status") == "??":
            if path.is_file():
                path.unlink()
                parent = path.parent
                while parent != root:
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
        else:
            result = subprocess.run(
                ["git", "-C", str(root), "restore", "--source=HEAD", "--staged", "--worktree", "--", relative],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if result.returncode:
                raise ValueError(result.stderr.strip() or f"Could not restore {relative}.")
    changes["undone"] = True
    changes["undone_at"] = utc_now_naive().isoformat()
    _update_execution(int(chat_id), job_id=str(execution.get("job_id") or "") or None, changes=changes)
    return {"ok": True, "changes": changes}


def _json(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _notify() -> None:
    try:
        from distr.gui.web.workflow_events import increment_workflow_updated

        increment_workflow_updated()
    except Exception:
        pass


def _update_execution(chat_id: int, *, job_id: str | None = None, **changes: Any) -> dict[str, Any]:
    with get_session() as db:
        root = db.get(Chat, int(chat_id))
        if root is None or root.parent_id is not None:
            raise LookupError("Development thread not found.")
        params = _chat_params(root.params)
        development = dict(params.get("development") or {})
        execution = dict(development.get("execution") or {})
        existing_job_id = execution.get("job_id")
        starts_new_turn = bool(
            job_id
            and existing_job_id not in {None, job_id}
            and str(changes.get("status") or "").lower() in {"initializing", "queued"}
            and changes.get("started_at")
        )
        if job_id and existing_job_id not in {None, job_id} and not starts_new_turn:
            return execution
        if starts_new_turn:
            execution = {}
        execution.update({key: value for key, value in changes.items() if value is not None})
        if job_id:
            execution["job_id"] = job_id
        development["execution"] = execution
        params["development"] = development
        root.params = json.dumps(params, ensure_ascii=False, default=str)
        root.modified_date = utc_now_naive()
        from distr.core.db.kanban import KanbanTicket
        from distr.core.db.workflow import DevelopmentWorkItem

        work_item = db.query(DevelopmentWorkItem).filter(DevelopmentWorkItem.chat_id == int(chat_id)).first()
        ticket = db.get(KanbanTicket, int(work_item.local_ticket_id)) if work_item and work_item.local_ticket_id else None
        next_status = str(execution.get("status") or "").strip().lower()
        if ticket is not None and next_status in {
            "initializing", "queued", "running", "waiting", "paused",
            "completed", "completed_noop", "failed", "cancelled",
        }:
            ticket.workflow_status = "completed" if next_status == "completed_noop" else next_status
        db.commit()
    _notify()
    return execution


def _append_streamed_output(chat_id: int, *, job_id: str, delta: str) -> str:
    clean = str(delta or "")
    if not clean:
        return ""
    with get_session() as db:
        root = db.get(Chat, int(chat_id))
        if root is None or root.parent_id is not None:
            return ""
        params = _chat_params(root.params)
        development = dict(params.get("development") or {})
        execution = dict(development.get("execution") or {})
        if execution.get("job_id") != job_id or str(execution.get("status") or "").lower() == "cancelled":
            return str(execution.get("streamed_output") or "")
        output = (str(execution.get("streamed_output") or "") + clean)[-24_000:]
        execution["streamed_output"] = output
        development["execution"] = execution
        params["development"] = development
        root.params = json.dumps(params, ensure_ascii=False, default=str)
        root.modified_date = utc_now_naive()
        db.commit()
    _notify()
    return output


def development_execution_state(chat_id: int) -> dict[str, Any]:
    terminal_reconciliation: dict[str, Any] | None = None
    with get_session() as db:
        root = db.get(Chat, int(chat_id))
        if root is None or root.parent_id is not None:
            raise LookupError("Development thread not found.")
        execution = dict(development_thread_metadata(root).get("execution") or {})
        persisted_status = str(execution.get("status") or "").lower()
        session_id = execution.get("execution_session_id")
        if session_id:
            from distr.core.db.kanban import ProjectExecutionSession

            row = db.get(ProjectExecutionSession, int(session_id))
            if row is not None and execution.get("status") != "cancelled":
                execution.update(
                    {
                        "status": row.status or execution.get("status") or "queued",
                        "backend_id": row.route_backend or execution.get("backend_id") or "",
                        "model": row.selected_model or execution.get("model") or "auto",
                        "started_at": row.started_at.isoformat() if row.started_at else execution.get("started_at"),
                        "completed_at": row.completed_at.isoformat() if row.completed_at else execution.get("completed_at"),
                        "error": row.error or execution.get("error") or "",
                    }
                )
                output = _json(row.output_packet)
                if output:
                    execution["output_packet"] = output
                row_status = str(row.status or "").lower()
                if row_status in {"completed", "failed", "cancelled"} and row_status != persisted_status:
                    terminal_reconciliation = {
                        "status": row_status,
                        "backend_id": row.route_backend or execution.get("backend_id") or "",
                        "model": row.selected_model or execution.get("model") or "auto",
                        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                        "error": row.error or execution.get("error") or "",
                        "activity_status": row_status,
                    }
    if terminal_reconciliation is not None:
        execution = _update_execution(
            int(chat_id),
            job_id=str(execution.get("job_id") or "") or None,
            **terminal_reconciliation,
        )
    if (
        execution.get("runtime_id") == "native"
        and str(execution.get("status") or "").lower() in ACTIVE_EXECUTION_STATUSES
    ):
        from distr.core.turn_runtime import active_turn_registered

        started_at = str(execution.get("started_at") or "")
        try:
            stale_for = (utc_now_naive() - datetime.fromisoformat(started_at)).total_seconds()
        except (TypeError, ValueError):
            stale_for = 0
        if stale_for >= 30 and not active_turn_registered(int(chat_id)):
            execution = _update_execution(
                int(chat_id),
                job_id=str(execution.get("job_id") or "") or None,
                status="failed",
                completed_at=utc_now_naive().isoformat(),
                activity_status="failed",
                summary="The native Development turn was interrupted when DecisionsAI stopped.",
                error="The native Development turn was interrupted when DecisionsAI stopped.",
            )
    execution["direct"] = True
    execution["id"] = execution.get("execution_session_id") or execution.get("job_id")
    return execution


def development_execution_active(chat_id: int) -> bool:
    try:
        return str(development_execution_state(chat_id).get("status") or "").lower() in ACTIVE_EXECUTION_STATUSES
    except LookupError:
        return False


def _organized_work_folder(name: str) -> Path:
    # Unlinked automation/development work must not create folders in the
    # user's development tree. Keep Decisions-owned scratch work isolated in
    # its own storage area instead.
    root = Path.home() / ".decisions" / "work"
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "development").strip().lower()).strip("-")
    folder = root / (slug[:80] or "development")
    folder.mkdir(parents=True, exist_ok=True)
    return folder.resolve()


def _project_and_scope(chat_id: int) -> tuple[Any, dict[str, Any], int | None, int | None]:
    project_snapshot = None
    with get_session() as db:
        root = db.get(Chat, int(chat_id))
        if root is None or root.parent_id is not None or not development_thread_metadata(root):
            raise LookupError("Development thread not found.")
        metadata = dict(development_thread_metadata(root))
        ticket_id = int(metadata["ticket_id"]) if metadata.get("ticket_id") is not None else None
        board_id = None
        if ticket_id is not None:
            from distr.core.db.kanban import KanbanLane, KanbanTicket

            ticket = db.get(KanbanTicket, ticket_id)
            lane = db.get(KanbanLane, int(ticket.lane_id)) if ticket and ticket.lane_id else None
            board_id = int(lane.board_id) if lane else None
        project = db.get(Project, int(root.project_id)) if root.project_id is not None else None
        title = root.title or "Development"
        if project is not None:
            configured_folder = str(project.folder_location or "").strip()
            folder = Path(configured_folder).expanduser() if configured_folder else _organized_work_folder(project.name or title)
            if configured_folder and not folder.is_dir():
                raise ValueError("The linked project folder is unavailable.")
            # Copy every value while the SQLAlchemy row is still attached.
            # get_session commits on exit and expires ORM attributes.
            project_snapshot = SimpleNamespace(
                id=int(project.id),
                name=project.name or title,
                folder_location=str(folder.resolve()),
                coding_backend=project.coding_backend or "pi",
                coding_backend_model=project.coding_backend_model or "",
            )
    if project_snapshot is not None:
        return project_snapshot, metadata, ticket_id, board_id

    folder = _organized_work_folder(title)
    return (
        SimpleNamespace(
            id=-int(chat_id),
            name=title,
            folder_location=str(folder.resolve()),
            coding_backend="pi",
            coding_backend_model="",
        ),
        metadata,
        ticket_id,
        board_id,
    )


def _route(project: Any, metadata: dict[str, Any], assessment: dict[str, Any]) -> tuple[str, str, dict[str, Any], str]:
    from distr.core.kanban.ticket_policy import resolve_ticket_cli_route
    from distr.core.project_cli_backends import normalize_backend_id

    model_route = metadata.get("model_route") if isinstance(metadata.get("model_route"), dict) else {}
    mode = str(model_route.get("route_mode") or "auto").strip().lower()
    complexity = str(assessment.get("complexity") or "medium").strip().lower()
    if complexity not in {"low", "medium", "high"}:
        complexity = "medium"
    adapter_options = {
        "reasoning_effort": str(model_route.get("reasoning_effort") or "medium"),
        "service_tier": str(model_route.get("service_tier") or "standard"),
        "required_capabilities": ["tools", "files"],
    }
    pinned_backend = normalize_backend_id(model_route.get("backend") or "") if mode == "manual" else ""
    local_sel = resolve_local_harness_selection(model_route.get("backend")) if mode == "manual" else None
    if mode == "manual" and local_sel:
        pinned_backend = normalize_backend_id(local_sel["backend"])
        model = str(local_sel["model"])
        available = local_harness_model_is_available(model)
        adapter_options = {
            **adapter_options,
            "model_provider": local_sel["provider"],
            "local_harness_preset": local_sel["local_harness_preset"],
            "keep_alive": "5m",
            "unload_when_done": True,
            "local_harness_available": available,
        }
        return pinned_backend, model, adapter_options, complexity
    if mode == "manual":
        if pinned_backend not in {"pi", "cursor", "codex", "claude_code"}:
            pinned_backend = "pi"
        provider = str(model_route.get("provider") or "").strip().lower()
        model = str(model_route.get("model_name") or "auto").strip() or "auto"
        if not provider:
            if pinned_backend == "codex":
                provider = "openai"
            elif pinned_backend == "claude_code":
                provider = "anthropic"
            elif pinned_backend == "pi":
                try:
                    from distr.core.pi_preflight import resolve_coding_cli_config

                    provider, _, _ = resolve_coding_cli_config(int(project.id) if int(project.id) > 0 else None)
                    provider = str(provider or "ollama").strip().lower()
                except Exception:
                    provider = "ollama"
        if provider:
            adapter_options["model_provider"] = provider
        return pinned_backend, model, adapter_options, complexity

    assessed = assessment.get("route") if isinstance(assessment.get("route"), dict) else {}
    resolved = resolve_ticket_cli_route(project, complexity, board=None)
    backend = str(
        assessed.get("backend") or resolved.get("backend") or project.coding_backend or "pi"
    ).strip().lower()
    backend = normalize_backend_id(backend)
    model = str(
        assessed.get("model") or resolved.get("model") or project.coding_backend_model or "auto"
    ).strip() or "auto"
    if assessed.get("model_provider"):
        adapter_options["model_provider"] = str(assessed["model_provider"]).strip().lower()
    elif resolved.get("model_provider"):
        adapter_options["model_provider"] = str(resolved["model_provider"]).strip().lower()
    elif backend in {"codex", "openai"}:
        adapter_options["model_provider"] = "openai"
    elif backend in {"claude", "claude_code", "anthropic"}:
        adapter_options["model_provider"] = "anthropic"
    elif backend == "pi":
        try:
            from distr.core.pi_preflight import resolve_coding_cli_config

            provider, _, _ = resolve_coding_cli_config(int(project.id) if int(project.id) > 0 else None)
            adapter_options["model_provider"] = str(provider or "ollama").strip().lower()
        except Exception:
            adapter_options["model_provider"] = "openrouter" if model.lower().endswith(":free") else "ollama"
    fallback_backend = str(
        assessed.get("fallback_backend") or resolved.get("fallback_backend") or ""
    ).strip().lower()
    fallback_model = str(assessed.get("fallback_model") or resolved.get("fallback_model") or "").strip()
    if fallback_backend and fallback_model:
        adapter_options["fallback_backend"] = fallback_backend
        adapter_options["fallback_model"] = fallback_model
        fallback_provider = str(
            assessed.get("fallback_model_provider") or resolved.get("fallback_model_provider") or ""
        ).strip().lower()
        if not fallback_provider:
            if fallback_backend in {"codex", "openai"}:
                fallback_provider = "openai"
            elif fallback_backend in {"claude", "claude_code", "anthropic"}:
                fallback_provider = "anthropic"
            elif fallback_backend == "pi":
                try:
                    from distr.core.pi_preflight import infer_project_model_provider, resolve_coding_cli_config

                    configured_provider, _, _ = resolve_coding_cli_config(
                        int(project.id) if int(project.id) > 0 else None
                    )
                    fallback_provider = infer_project_model_provider(configured_provider, fallback_model)
                except Exception:
                    fallback_provider = "openrouter" if fallback_model.lower().endswith(":free") else "ollama"
        if fallback_provider:
            adapter_options["fallback_model_provider"] = fallback_provider
    if backend == "codex":
        for key in ("codex_reasoning_effort", "codex_service_tier"):
            value = assessed.get(key) or resolved.get(key)
            if value:
                adapter_options[key] = value
    return backend, model, adapter_options, complexity


def _metadata_with_runtime_identity(
    metadata: dict[str, Any],
    *,
    backend: str,
    model: str,
    adapter_options: dict[str, Any],
    runtime_id: str = "",
) -> dict[str, Any]:
    """Project the resolved route into the context visible to the agent."""
    model_route = metadata.get("model_route") if isinstance(metadata.get("model_route"), dict) else {}
    return {
        **metadata,
        "active_runtime_identity": {
            "agent": "Decisions Development agent",
            "provider": str(adapter_options.get("model_provider") or "").strip().lower(),
            "model": str(model or "auto").strip() or "auto",
            "turn_runtime": str(runtime_id or "runtime-selected").strip().lower(),
            "routing_backend": str(backend or "").strip().lower(),
            "route_mode": str(model_route.get("route_mode") or "auto").strip().lower(),
        },
    }


def _runtime_identity_text(metadata: dict[str, Any]) -> str:
    identity = metadata.get("active_runtime_identity")
    if not isinstance(identity, dict):
        return ""
    rows = [
        f"Agent: {identity.get('agent') or 'Decisions Development agent'}",
        f"Provider: {identity.get('provider') or 'runtime-selected'}",
        f"Model: {identity.get('model') or 'runtime-selected'}",
        f"Turn runtime: {identity.get('turn_runtime') or 'runtime-selected'}",
        f"Configured routing backend: {identity.get('routing_backend') or 'runtime-selected'}",
        f"Route mode: {identity.get('route_mode') or 'auto'}",
    ]
    return (
        "Active runtime identity for this turn:\n"
        + "\n".join(rows)
        + "\nWhen asked about your provider, model, or execution identity, answer from this block. "
        "Do not infer it from project notes or earlier messages. The turn runtime and configured routing backend are not the model provider. "
        "Do not expand, reinterpret, or invent descriptions for these identifiers."
    )


def _agent_instruction(chat_id: int, prompt: str, metadata: dict[str, Any], *, autonomy_level: str) -> str:
    history = ChatService.get_chat_history(int(chat_id))
    if history and history[-1].get("role") == "user" and str(history[-1].get("content") or "").strip() == prompt.strip():
        history = history[:-1]
    transcript = []
    remaining = 24000
    for item in reversed(history[-24:]):
        text = str(item.get("content") or "").strip()
        if not text or remaining <= 0:
            continue
        text = text[-min(len(text), remaining, 4000):]
        remaining -= len(text)
        transcript.append(f"{str(item.get('role') or 'context').title()}: {text}")
    transcript.reverse()
    scope = [
        f"Board: {metadata.get('board_key')}" if metadata.get("board_key") else "",
        f"Thread ticket: {metadata.get('board_ticket_key') or metadata.get('ticket_id')}" if metadata.get("board_ticket_key") or metadata.get("ticket_id") else "",
        (
            f"Automation control: {metadata.get('source_ref')}. This thread owns that scheduled task. "
            "Treat requests referring to this schedule or this automation as targeting that exact automation, "
            "using the server-bound automation control when available to inspect, edit, pause, resume, run, or delete it."
            if str(metadata.get("source_type") or "").lower() == "automation" and metadata.get("source_ref")
            else ""
        ),
    ]
    mode_instruction = (
        "Work in plan-only mode. Inspect and reason with tools, but do not modify project files."
        if autonomy_level == "plan"
        else "Work as the active IDE coding agent. Inspect, edit, run checks, and continue until this request is genuinely handled or a user decision is required."
    )
    selected_skills = [str(skill_id).strip() for skill_id in (metadata.get("turn_skill_ids") or []) if str(skill_id).strip()]
    skill_instruction = (
        "Skills selected by the user for this turn: "
        + ", ".join(selected_skills)
        + ". Read each exact skill with read_harness_skill before acting, then apply its instructions. Do not search or list the skill catalog first."
        if selected_skills
        else ""
    )
    return "\n\n".join(
        part
        for part in (
            "[DECISIONS DEVELOPMENT THREAD]\n"
            f"{mode_instruction}\n"
            "Respond conversationally and use the project tools directly, as in a normal coding IDE. "
            "This Development thread is the ticket and the current work item. Work on it directly. "
            "Do not create another ticket, issue, or ticket file unless the user explicitly asks for a separate work item. "
            "Use direct execution for compact work. When deterministic instruction analysis selects a workflow, keep this thread as the owner and run the workflow orchestrator as its sub-agent. "
            "A direct modification request is authorization to implement it. Do not finish by offering to make the requested change or asking which obvious project target to use when repository and runtime evidence identify it. When cosmetic details are omitted, make the smallest conventional implementation and report what you chose.",
            _runtime_identity_text(metadata),
            skill_instruction,
            "\n".join(item for item in scope if item),
            "Conversation context since the latest context checkpoint:\n" + "\n\n".join(transcript) if transcript else "",
            "Current user instruction:\n" + prompt,
            "Attached files for this turn:\n" + "\n".join(
                f"- {item.get('name') or Path(str(item.get('path') or '')).name} ({item.get('mime_type') or 'application/octet-stream'}): {item.get('path')}"
                for item in metadata.get("turn_attachments") or []
            ) if metadata.get("turn_attachments") else "",
        )
        if part
    )


def _linked_ticket_attachments(ticket_id: int | None) -> list[dict[str, Any]]:
    if ticket_id is None:
        return []
    from distr.core.db.kanban import KanbanTicket

    with get_session() as db:
        ticket = db.get(KanbanTicket, int(ticket_id))
        if ticket is None:
            return []
        return [
            {
                "name": str(record.filename or Path(str(record.file_path or "attachment")).name),
                "path": str(record.file_path or ""),
                "mime_type": mimetypes.guess_type(str(record.filename or record.file_path or ""))[0]
                or "application/octet-stream",
                "size": Path(str(record.file_path or "")).expanduser().stat().st_size
                if Path(str(record.file_path or "")).expanduser().is_file()
                else 0,
            }
            for record in ticket.files
            if str(record.file_path or "").strip()
        ]


def _safe_turn_attachments(
    raw_items: list[dict[str, Any]] | None,
    *,
    project_folder: str = "",
) -> list[dict[str, Any]]:
    roots = [(Path.home() / ".decisions" / "workspaces" / "projects").resolve(strict=False)]
    if str(project_folder or "").strip():
        roots.append(Path(project_folder).expanduser().resolve(strict=False))
    attachments: list[dict[str, Any]] = []
    seen: set[Path] = set()
    # A message snapshot can legitimately contain a large collection of media.
    # Keep one bounded payload, but do not silently truncate normal ticket sets.
    for raw in (raw_items or [])[:250]:
        if not isinstance(raw, dict) or not raw.get("path"):
            continue
        path = Path(str(raw["path"])).expanduser().resolve(strict=False)
        if path in seen or not path.is_file() or not any(root == path or root in path.parents for root in roots):
            continue
        seen.add(path)
        attachments.append(
            {
                "name": str(raw.get("name") or path.name)[:255],
                "path": str(path),
                "mime_type": str(raw.get("mime_type") or "application/octet-stream")[:120],
                "size": int(raw.get("size") or path.stat().st_size),
                "trust": str(raw.get("trust") or "")[:80],
            }
        )
    return attachments


def _write_response(chat_id: int, turn_id: int | None, response: str) -> None:
    if turn_id is None:
        ChatService.append_assistant_notice(int(chat_id), response)
        return
    with get_session() as db:
        turn = db.get(Chat, int(turn_id))
        root = db.get(Chat, int(chat_id))
        if turn is None or root is None or int(turn.parent_id or 0) != int(chat_id):
            return
        turn.response = response
        turn.modified_date = utc_now_naive()
        root.modified_date = utc_now_naive()
        db.commit()


def _start_next_queued_instruction(chat_id: int) -> None:
    from distr.core.db.workflow import DevelopmentCommand

    with get_session() as db:
        command = (
            db.query(DevelopmentCommand)
            .filter(
                DevelopmentCommand.chat_id == int(chat_id),
                DevelopmentCommand.status == "queued",
            )
            .order_by(DevelopmentCommand.position.asc(), DevelopmentCommand.id.asc())
            .first()
        )
        if command is None:
            return
        content = str(command.content or "").strip()
        try:
            command_metadata = json.loads(command.metadata_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            command_metadata = {}
        skill_ids = command_metadata.get("skill_ids") if isinstance(command_metadata, dict) else []
        command.status = "delivered"
        command.delivered_at = utc_now_naive()
        command.result_summary = "Started after the previous agent turn completed."
        db.commit()
    if not content:
        return
    ChatService.add_user_message(int(chat_id), content)
    from distr.core.workflow.development_control import resume_thread_time

    resume_thread_time(int(chat_id))
    dispatch_development_prompt(
        int(chat_id),
        content,
        skill_ids=skill_ids if isinstance(skill_ids, list) else [],
        dispatch_async=True,
    )


async def _run_execution(
    *,
    chat_id: int,
    turn_id: int | None,
    job_id: str,
    project: Any,
    metadata: dict[str, Any],
    ticket_id: int | None,
    board_id: int | None,
    prompt: str,
    assessment: dict[str, Any],
    attachments: list[dict[str, Any]],
    skill_ids: list[str],
    use_playwright: bool,
    autonomy_level: str,
    tool_event_id: str | None,
) -> None:
    from distr.core.chat_turns import complete_turn, finish_tool, start_tool, terminal_turn, update_event
    from distr.core.turn_runtime import (
        ModelRoute,
        TurnCancelled,
        TurnEvent,
        TurnRequest,
        TurnScope,
        build_development_context,
        execute_turn,
        select_turn_runtime_id,
    )

    backend, model, adapter_options, complexity = _route(project, metadata, assessment)
    runtime_metadata = _metadata_with_runtime_identity(
        metadata,
        backend=backend,
        model=model,
        adapter_options=adapter_options,
    )
    runtime_metadata["turn_attachments"] = list(attachments)
    runtime_metadata["turn_skill_ids"] = list(skill_ids)
    if autonomy_level == "plan":
        adapter_options["read_only_expected"] = True
    native_tool_events: dict[str, str] = {}

    def on_event(event: TurnEvent) -> None:
        summary = str(event.summary or "Working")[:1000]
        call_id = str(event.details.get("call_id") or "")
        if event.output_delta:
            _append_streamed_output(chat_id, job_id=job_id, delta=event.output_delta)
        _update_execution(
            chat_id,
            job_id=job_id,
            status="running",
            runtime_id=event.runtime_id,
            execution_session_id=event.execution_session_id,
            activity=summary,
            activity_status=event.status.value,
        )
        if event.kind.value == "tool_started" and event.tool_name:
            native_event_id, _, _ = start_tool(
                int(chat_id),
                event.tool_name,
                turn_id=turn_id,
                title=str(event.details.get("title") or event.tool_name.replace("_", " ").title()),
                summary=summary,
                metadata={"source": "native_development", **event.details},
            )
            if native_event_id and call_id:
                native_tool_events[call_id] = native_event_id
        elif event.kind.value == "tool_completed" and call_id in native_tool_events:
            native_status = str(event.details.get("status") or "success")
            finish_tool(
                native_tool_events.pop(call_id),
                success=native_status != "error",
                summary=summary,
                metadata=event.details,
            )
        elif event.kind.value == "tool_completed" and event.tool_name:
            native_status = str(event.details.get("status") or "success")
            native_event_id, _, _ = start_tool(
                int(chat_id),
                event.tool_name,
                turn_id=turn_id,
                title=str(event.details.get("title") or event.tool_name.replace("_", " ").title()),
                summary=summary,
                metadata={"source": "native_development", **event.details},
            )
            if native_event_id:
                finish_tool(
                    native_event_id,
                    success=native_status != "error",
                    summary=summary,
                    metadata=event.details,
                )
        elif event.kind.value == "context_compacted":
            native_event_id, _, _ = start_tool(
                int(chat_id),
                "context_compaction",
                turn_id=turn_id,
                title="Context compacted",
                summary=summary,
                metadata={"source": "native_development", **event.details},
            )
            if native_event_id:
                finish_tool(native_event_id, success=True, summary=summary, metadata=event.details)
        if tool_event_id:
            update_event(tool_event_id, status="running", summary=summary)

    try:
        request = TurnRequest(
            instruction=_agent_instruction(chat_id, prompt, runtime_metadata, autonomy_level=autonomy_level),
            project=project,
            route=ModelRoute(
                provider=str(adapter_options.get("model_provider") or ""),
                model=model,
                backend_id=backend,
                adapter_options=adapter_options,
            ),
            scope=TurnScope(
                chat_id=int(chat_id),
                project_id=int(project.id),
                board_id=board_id,
                ticket_id=ticket_id,
                turn_id=turn_id,
                project_folder=str(project.folder_location or ""),
            ),
            autonomy_level=autonomy_level,
            complexity=complexity,
            required_capabilities=("tools", "files", *(("images",) if any(str(item.get("mime_type") or "").startswith("image/") for item in attachments) else ())),
            context_messages=build_development_context(
                int(chat_id),
                prompt,
                runtime_metadata,
                project_folder=str(project.folder_location or ""),
                autonomy_level=autonomy_level,
                attachments=attachments,
            ),
            metadata={
                "source": "development",
                "job_id": job_id,
                "attachments": attachments,
                "skill_ids": skill_ids,
                "automation_id": (
                    str(runtime_metadata.get("source_ref") or "")
                    if str(runtime_metadata.get("source_type") or "").lower() == "automation"
                    else ""
                ),
                "untrusted_external_input": assessment.get("operational_state") == "external_untrusted",
                "use_playwright": bool(use_playwright),
                "change_expected": _prompt_expects_project_change(prompt, autonomy_level=autonomy_level),
            },
        )
        runtime_id = select_turn_runtime_id(
            request,
            runtime_id="cli_harness" if backend in {"pi", "cursor", "codex", "claude_code"} else "",
        )
        runtime_metadata = _metadata_with_runtime_identity(
            metadata,
            backend=backend,
            model=model,
            adapter_options=adapter_options,
            runtime_id=runtime_id,
        )
        runtime_metadata["turn_attachments"] = list(attachments)
        runtime_metadata["turn_skill_ids"] = list(skill_ids)
        request = replace(
            request,
            instruction=_agent_instruction(chat_id, prompt, runtime_metadata, autonomy_level=autonomy_level),
            context_messages=build_development_context(
                int(chat_id),
                prompt,
                runtime_metadata,
                project_folder=str(project.folder_location or ""),
                autonomy_level=autonomy_level,
                attachments=attachments,
            ),
            metadata={**request.metadata, "active_runtime_identity": runtime_metadata["active_runtime_identity"]},
        )
        _update_execution(
            chat_id,
            job_id=job_id,
            status="initializing",
            backend_id=backend,
            model=model,
            project_id=int(project.id),
            board_id=board_id,
            turn_id=turn_id,
            runtime_id=runtime_id,
        )
        local_preset = str((adapter_options or {}).get("local_harness_preset") or "").strip()
        local_tag = parse_local_ollama_worker(local_preset) or (
            str(model or "").strip() if local_preset else ""
        )
        if local_preset and local_tag:
            # Fail closed when a local Ollama worker was pinned but is missing/unreachable.
            # UI builds the picker from live ollama list; this is the run-time backstop (no pull).
            available = (adapter_options or {}).get("local_harness_available")
            if available is None:
                available = local_harness_model_is_available(local_tag)
            if not available:
                tag = local_tag
                message = (
                    f"Local Ollama worker `{local_ollama_worker_label(tag)}` is not installed or Ollama is unreachable "
                    f"(expected tag `{tag}`). Install the model locally or pick another worker. "
                    "Decisions will not pull models automatically."
                )
                _write_response(chat_id, turn_id, message)
                _update_execution(
                    chat_id,
                    job_id=job_id,
                    status="failed",
                    backend_id=backend,
                    model=model,
                    runtime_id=runtime_id,
                    completed_at=utc_now_naive().isoformat(),
                    error=message,
                    summary=message[:2000],
                    activity_status="failed",
                    outcome={
                        "backend_id": backend,
                        "runtime_id": runtime_id,
                        "success": False,
                        "tool_counts": {},
                        "git_change_count": 0,
                        "summary": message[:2000],
                    },
                )
                if tool_event_id:
                    finish_tool(tool_event_id, success=False, summary=message[:1000], detail=message)
                terminal_turn(chat_id, "turn_failed", turn_id=turn_id, summary=message)
                return
        if backend in CLI_BACKENDS:
            from distr.core.project_cli_backends.catalog_probe import probe_cli_backend

            probe = probe_cli_backend(backend)
            ready = bool((probe.get("status") or {}).get("ready"))
            if not ready:
                reason = str(
                    probe.get("next_step")
                    or (probe.get("status") or {}).get("message")
                    or f"{backend} is not ready."
                ).strip()
                message = f"Pinned harness `{backend}` is not ready: {reason}"
                _write_response(chat_id, turn_id, message)
                _update_execution(
                    chat_id,
                    job_id=job_id,
                    status="failed",
                    backend_id=backend,
                    runtime_id=runtime_id,
                    completed_at=utc_now_naive().isoformat(),
                    error=message,
                    summary=message[:2000],
                    activity_status="failed",
                    outcome={
                        "backend_id": backend,
                        "runtime_id": runtime_id,
                        "success": False,
                        "tool_counts": {},
                        "git_change_count": 0,
                        "summary": message[:2000],
                    },
                )
                if tool_event_id:
                    finish_tool(tool_event_id, success=False, summary=message[:1000], detail=message)
                terminal_turn(chat_id, "turn_failed", turn_id=turn_id, summary=message)
                return
        result = await execute_turn(
            request,
            on_event=on_event,
            runtime_id=runtime_id,
        )
        current = development_execution_state(chat_id)
        if current.get("job_id") != job_id or current.get("status") == "cancelled":
            return
        success = bool(result.success)
        waiting = bool(result.waits_for_human and success)
        output = str(result.output or "").strip()
        error = str(result.error or "").strip()
        response = output or error or ("Work completed." if success else "The development agent did not return a result.")
        status = "waiting" if waiting else "completed" if success else "failed"
        changes = _turn_change_manifest(
            str(project.folder_location or ""),
            dict(current.get("git_status_before") or {}),
            before_untracked={
                str(path): str(digest)
                for path, digest in dict(current.get("git_untracked_before") or {}).items()
                if path and digest
            },
        )
        git_change_count = len(changes.get("files") or [])
        change_expected = bool(request.metadata.get("change_expected"))
        # Only flag no-ops when we could observe a real git baseline in the project folder.
        git_baseline = _git_project_root(str(project.folder_location or "")) is not None
        noop = bool(
            change_expected
            and success
            and not waiting
            and git_baseline
            and git_change_count == 0
            and not _output_justifies_noop(output)
        )
        if noop:
            status = "completed_noop"
        outcome = _turn_outcome(
            backend_id=str(result.backend_id or backend),
            runtime_id=str(result.runtime_id or runtime_id),
            success=success,
            result=result,
            changes=changes,
            summary=response,
            noop=noop,
        )
        _write_response(chat_id, turn_id, response)
        _update_execution(
            chat_id,
            job_id=job_id,
            status=status,
            runtime_id=result.runtime_id,
            backend_id=result.backend_id,
            model=result.model,
            execution_session_id=result.execution_session_id,
            completed_at=utc_now_naive().isoformat(),
            summary=response[:2000],
            error=error,
            streamed_output="",
            activity_status=status,
            git_status_after=_git_status_snapshot(str(project.folder_location or "")),
            changes=changes,
            outcome=outcome,
        )
        if tool_event_id:
            finish_tool(tool_event_id, success=success, summary=response[:1000], detail=error)
        if success:
            complete_turn(chat_id, turn_id=turn_id, display_text=response)
        else:
            terminal_turn(chat_id, "turn_failed", turn_id=turn_id, summary=error or response)
        if (
            bool((adapter_options or {}).get("unload_when_done"))
            and str((adapter_options or {}).get("model_provider") or "").lower() == "ollama"
        ):
            _unload_ollama_model(model)
        if not waiting:
            _start_next_queued_instruction(chat_id)
    except TurnCancelled:
        return
    except Exception as exc:
        current = development_execution_state(chat_id)
        if current.get("job_id") != job_id or current.get("status") == "cancelled":
            return
        message = str(exc) or "The development agent failed to start."
        _write_response(chat_id, turn_id, message)
        _update_execution(
            chat_id,
            job_id=job_id,
            status="failed",
            completed_at=utc_now_naive().isoformat(),
            error=message,
            outcome={
                "backend_id": backend,
                "runtime_id": "",
                "success": False,
                "tool_counts": {},
                "git_change_count": 0,
                "summary": message[:2000],
            },
        )
        if tool_event_id:
            finish_tool(tool_event_id, success=False, summary=message, detail=message)
        terminal_turn(chat_id, "turn_failed", turn_id=turn_id, summary=message)


def _owned_automation_schedule_from_prompt(prompt: str, current: dict[str, Any]) -> dict[str, Any] | None:
    lowered = str(prompt or "").strip().lower()
    if not re.search(r"\b(change|set|make|schedule|run)\b", lowered):
        return None
    time_value = str(current.get("time") or "09:00")
    match = re.search(r"\b(?:at|to)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", lowered)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        suffix = str(match.group(3) or "")
        if suffix == "pm" and hour < 12:
            hour += 12
        elif suffix == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            raise ValueError("Invalid schedule time.")
        time_value = f"{hour:02d}:{minute:02d}"
    timezone_name = str(current.get("timezone") or "")
    interval = re.search(r"\bevery\s+(\d+)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", lowered)
    if interval:
        value = int(interval.group(1))
        unit = interval.group(2)
        if unit.startswith(("hour", "hr")):
            value *= 60
            unit = "minutes"
        else:
            unit = "seconds" if unit.startswith("sec") else "minutes"
        return {"kind": "interval", "interval": value, "interval_unit": unit, "timezone": timezone_name}
    one_time_day = re.search(r"\b(today|tomorrow)\b", lowered)
    explicit_date = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", lowered)
    if "once" in lowered or one_time_day or explicit_date:
        if explicit_date:
            run_date = datetime.strptime(explicit_date.group(1), "%Y-%m-%d").date()
        elif one_time_day:
            run_date = (datetime.now() + timedelta(days=1 if one_time_day.group(1) == "tomorrow" else 0)).date()
        else:
            current_run_at = str(current.get("run_at") or "")
            run_date = datetime.fromisoformat(current_run_at.replace("Z", "+00:00")).date() if current_run_at else datetime.now().date()
        return {"kind": "once", "run_at": f"{run_date.isoformat()}T{time_value}", "timezone": timezone_name}
    if "weekday" in lowered or "monday to friday" in lowered:
        return {"kind": "weekly", "time": time_value, "days": "1,2,3,4,5", "timezone": timezone_name}
    day_map = {
        "monday": "1",
        "tuesday": "2",
        "wednesday": "3",
        "thursday": "4",
        "friday": "5",
        "saturday": "6",
        "sunday": "0",
    }
    named_day = next((number for name, number in day_map.items() if name in lowered), None)
    if named_day:
        return {"kind": "weekly", "time": time_value, "days": named_day, "timezone": timezone_name}
    if "hourly" in lowered or "every hour" in lowered:
        return {"kind": "hourly", "timezone": timezone_name}
    if "monthly" in lowered or "every month" in lowered:
        month_day = re.search(r"\b(?:day|on the)\s+(\d{1,2})(?:st|nd|rd|th)?\b", lowered)
        days = month_day.group(1) if month_day else str(current.get("days") or "1")
        return {"kind": "monthly", "time": time_value, "days": days, "timezone": timezone_name}
    if "daily" in lowered or "every day" in lowered:
        return {"kind": "daily", "time": time_value, "timezone": timezone_name}
    if match:
        kind = str(current.get("kind") or "daily")
        if kind == "once":
            current_run_at = str(current.get("run_at") or "")
            run_date = current_run_at.split("T", 1)[0] if "T" in current_run_at else datetime.now().date().isoformat()
            return {**current, "kind": "once", "run_at": f"{run_date}T{time_value}", "timezone": timezone_name}
        return {**current, "kind": kind, "time": time_value, "timezone": timezone_name}
    return None


def _handle_owned_automation_control(
    chat_id: int,
    prompt: str,
    metadata: dict[str, Any],
    *,
    assessment: dict[str, Any],
) -> dict[str, Any] | None:
    if assessment.get("source") == "automation_import_configuration":
        return None
    if str(metadata.get("source_type") or "").lower() != "automation":
        return None
    automation_id = str(metadata.get("source_ref") or "").strip()
    if not automation_id:
        return None
    from distr.core.automation.store import delete_automation, get_automation, update_automation
    from distr.core.chat_turns import latest_active_turn_id

    automation = get_automation(automation_id)
    if not automation:
        return None
    lowered = str(prompt or "").strip().lower()
    current_turn_id = int(latest_active_turn_id(chat_id) or 0)
    with get_session() as db:
        root = db.get(Chat, int(chat_id))
        params = _chat_params(root.params) if root is not None else {}
        pending = params.get("automation_delete_confirmation")
    pending = pending if isinstance(pending, dict) else {}
    try:
        pending_expires_at = datetime.fromisoformat(str(pending.get("expires_at") or ""))
    except ValueError:
        pending_expires_at = None
    pending_valid = bool(
        pending.get("automation_id") == automation_id
        and pending_expires_at is not None
        and pending_expires_at > utc_now_naive()
    )

    def clear_pending_confirmation() -> None:
        with get_session() as db:
            selected = db.get(Chat, int(chat_id))
            if selected is None:
                return
            selected_params = _chat_params(selected.params)
            selected_params.pop("automation_delete_confirmation", None)
            selected.params = json.dumps(selected_params, ensure_ascii=False, default=str)
            db.commit()

    if pending and not pending_valid:
        clear_pending_confirmation()
        pending = {}
    confirms_pending = bool(
        pending_valid
        and current_turn_id > int(pending.get("requested_turn_id") or 0)
        and (
            lowered in {"yes", "confirm", "confirmed", "confirm delete", "yes delete it"}
            or re.search(r"\b(confirm|yes)\b.*\b(delete|remove)\b", lowered)
        )
    )
    if confirms_pending:
        deleted = delete_automation(automation_id)
        return {
            "status": "completed" if deleted else "failed",
            "direct": True,
            "execution_mode": "automation_control",
            "automation_control": "delete",
            "automation_id": automation_id,
            "deleted": deleted,
        }
    if pending_valid and current_turn_id > int(pending.get("requested_turn_id") or 0):
        rejected = bool(
            lowered in {"no", "cancel", "do not delete", "don't delete", "keep it"}
            or re.search(r"\b(no|cancel|keep)\b.*\b(delete|remove|schedule|automation|it)\b", lowered)
        )
        clear_pending_confirmation()
        if rejected:
            ChatService.append_assistant_notice(chat_id, "Deletion cancelled. This scheduled task is unchanged.")
            return {
                "status": "completed",
                "direct": True,
                "execution_mode": "automation_control",
                "automation_control": "delete_cancelled",
                "automation_id": automation_id,
            }
    target_words = bool(re.search(r"\b(schedule|automation|scheduled task)\b", lowered))
    if target_words and re.search(r"\b(delete|remove)\b", lowered):
        with get_session() as db:
            root = db.get(Chat, int(chat_id))
            if root is None:
                raise LookupError("Automation thread not found.")
            params = _chat_params(root.params)
            params["automation_delete_confirmation"] = {
                "automation_id": automation_id,
                "requested_turn_id": current_turn_id,
                "expires_at": (utc_now_naive() + timedelta(minutes=5)).isoformat(),
            }
            root.params = json.dumps(params, ensure_ascii=False, default=str)
            db.commit()
        message = "Please confirm in a new message that you want to delete this scheduled task and its thread."
        ChatService.append_assistant_notice(chat_id, message)
        return {
            "status": "waiting",
            "direct": True,
            "execution_mode": "automation_control",
            "automation_control": "delete_confirmation",
            "automation_id": automation_id,
        }
    if target_words and "pause" in lowered:
        updated = update_automation(automation_id, status="paused")
        ChatService.append_assistant_notice(chat_id, "This scheduled task is paused.")
        return {"status": "completed", "direct": True, "execution_mode": "automation_control", "automation": updated}
    if target_words and re.search(r"\b(resume|unpause)\b", lowered):
        updated = update_automation(automation_id, status="active")
        ChatService.append_assistant_notice(chat_id, "This scheduled task is active again.")
        return {"status": "completed", "direct": True, "execution_mode": "automation_control", "automation": updated}
    if re.search(r"\brun\s+(?:(?:it|this\s+(?:schedule|automation|task))\s+)?now\b", lowered):
        from distr.core.automation_orchestrator import dispatch_automation_to_current_chat

        return dispatch_automation_to_current_chat(automation, manual=True)
    schedule = _owned_automation_schedule_from_prompt(prompt, automation.get("schedule") or {})
    if schedule is not None:
        updated = update_automation(automation_id, schedule=schedule)
        ChatService.append_assistant_notice(chat_id, "The schedule has been updated.")
        return {"status": "completed", "direct": True, "execution_mode": "automation_control", "automation": updated}
    return None


def dispatch_development_prompt(
    chat_id: int,
    prompt: str,
    *,
    routing_assessment: dict[str, Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    skill_ids: list[str] | None = None,
    use_playwright: bool = False,
    autonomy_override: str | None = None,
    dispatch_async: bool = True,
) -> dict[str, Any]:
    clean = str(prompt or "").strip()
    if not clean:
        raise ValueError("A development instruction is required.")
    project, metadata, ticket_id, board_id = _project_and_scope(int(chat_id))
    if metadata.get("source_type") == "plan_workspace":
        raise ValueError("Use the Plan conversation for planning edits. Plan threads cannot dispatch application code changes.")
    with get_session() as db:
        root = db.get(Chat, int(chat_id))
        autonomy_level = str(autonomy_override or root.autonomy_level or "full").strip().lower()
    if autonomy_level not in {"full", "plan", "approval"}:
        raise ValueError("Unsupported Development autonomy level.")
    from distr.core.chat_turns import latest_active_turn_id, start_tool

    turn_id = latest_active_turn_id(int(chat_id))
    job_id = uuid.uuid4().hex
    assessment = dict(routing_assessment or {})
    control_result = _handle_owned_automation_control(
        int(chat_id),
        clean,
        metadata,
        assessment=assessment,
    )
    if control_result is not None:
        return control_result
    from distr.core.workflow.execution_mode import choose_development_execution_mode

    execution_mode = choose_development_execution_mode(clean, assessment=assessment)
    if (
        execution_mode["mode"] == "workflow"
        and autonomy_level not in {"plan", "approval"}
    ):
        from distr.core.workflow.planning import plan_workflow
        from distr.core.workflow.work_dispatch import dispatch_work_item

        existing_workflow_id = int(metadata.get("workflow_id") or 0) or None
        workflow_id = existing_workflow_id
        if workflow_id is None:
            try:
                planned_id = plan_workflow(clean, chat_id=int(chat_id), name=None)
            except Exception as exc:
                message = str(exc).strip() or "Could not plan a development workflow."
                ChatService.append_assistant_notice(
                    int(chat_id),
                    f"{message} Falling back to the canonical Development workflow.",
                )
                planned_id = None
            if planned_id:
                try:
                    from distr.core.workflow.development_threads import mark_development_thread

                    mark_development_thread(
                        int(chat_id),
                        workflow_id=int(planned_id),
                        ticket_id=ticket_id,
                        board_key=metadata.get("board_key"),
                        board_provider=metadata.get("board_provider"),
                        board_ticket_key=metadata.get("board_ticket_key"),
                        board_ticket_title=metadata.get("board_ticket_title"),
                        board_ticket_lane=metadata.get("board_ticket_lane"),
                    )
                    workflow_id = int(planned_id)
                except Exception as exc:
                    ChatService.append_assistant_notice(
                        int(chat_id),
                        f"Could not bind the planned workflow ({exc}). Falling back to the canonical Development workflow.",
                    )
                    workflow_id = None
        if workflow_id is None:
            from distr.core.workflow.developer_workflow import resolve_development_workflow

            workflow_id = resolve_development_workflow(
                project_id=int(project.id) if int(project.id) > 0 else None,
                ticket_id=ticket_id,
                board_key=metadata.get("board_key"),
            )
        model_route = metadata.get("model_route") if isinstance(metadata.get("model_route"), dict) else {}
        pinned_backend = str(model_route.get("backend") or "").strip().lower()
        local_sel = resolve_local_harness_selection(pinned_backend)
        if (
            str(model_route.get("route_mode") or "").strip().lower() == "manual"
            and pinned_backend not in CLI_BACKENDS
            and local_sel is None
        ):
            pinned_backend = "pi"
        run_metadata = {
            "routing_assessment": assessment,
            "execution_mode_decision": execution_mode,
            "skill_ids": list(skill_ids or []),
            "use_playwright": bool(use_playwright),
            "model_route": model_route,
        }
        if pinned_backend in CLI_BACKENDS or local_sel is not None:
            run_metadata["execution_route"] = {
                "backend": (local_sel["backend"] if local_sel else pinned_backend),
                "model": (
                    str(local_sel["model"])
                    if local_sel
                    else str(model_route.get("model_name") or "auto")
                ),
                "model_provider": (
                    str(local_sel["provider"])
                    if local_sel
                    else str(model_route.get("provider") or "")
                ),
                "source": "thread_model_route",
                "local_harness_preset": local_sel["local_harness_preset"] if local_sel else "",
            }
        result = dispatch_work_item(
            workflow_id=int(workflow_id),
            chat_id=int(chat_id),
            context=clean,
            project_id=int(project.id) if int(project.id) > 0 else None,
            board_id=board_id,
            ticket_id=ticket_id,
            source_type="development_thread_orchestrator",
            run_metadata=run_metadata,
            dispatch_async=dispatch_async,
        )
        return {
            **result,
            "direct": False,
            "execution_mode": "workflow",
            "orchestrator_agent": "workflow_orchestrator",
            "workflow_generated": existing_workflow_id is None,
        }
    persistent_attachments = _linked_ticket_attachments(ticket_id)
    turn_attachments = _safe_turn_attachments(
        [*(attachments or []), *persistent_attachments],
        project_folder=str(project.folder_location or ""),
    )
    from distr.core.skills.catalog import filter_known_skill_ids, infer_skills_for_ticket

    turn_skill_ids = filter_known_skill_ids(list(skill_ids or []))[:12]
    if not turn_skill_ids:
        # Fill gaps: route skills inferred from the ticket/prompt into CLI harness context.
        inferred = infer_skills_for_ticket(
            "\n".join(part for part in (clean, str((metadata or {}).get("ticket_title") or "")) if part),
            limit=5,
        )
        turn_skill_ids = filter_known_skill_ids(list(inferred or []))[:12]
    backend, model, _, _ = _route(project, metadata, assessment)
    git_status_before = _git_status_snapshot(str(project.folder_location or ""))
    git_untracked_before = _git_untracked_fingerprints(
        str(project.folder_location or ""),
        git_status_before,
    )
    execution = _update_execution(
        int(chat_id),
        job_id=job_id,
        status="initializing",
        backend_id=backend,
        model=model,
        project_id=int(project.id),
        board_id=board_id,
        turn_id=turn_id,
        started_at=utc_now_naive().isoformat(),
        error="",
        summary="",
        streamed_output="",
        activity_status="thinking",
        runtime_id="cli_harness",
        git_status_before=git_status_before,
        git_untracked_before=git_untracked_before,
    )
    tool_event_id, _, _ = start_tool(
        int(chat_id),
        "development_agent",
        turn_id=turn_id,
        title="Working",
        summary="Starting the development agent.",
        metadata={"source": "development", "job_id": job_id},
    )
    kwargs = {
        "chat_id": int(chat_id),
        "turn_id": turn_id,
        "job_id": job_id,
        "project": project,
        "metadata": metadata,
        "ticket_id": ticket_id,
        "board_id": board_id,
        "prompt": clean,
        "assessment": assessment,
        "attachments": turn_attachments,
        "skill_ids": turn_skill_ids,
        "use_playwright": bool(use_playwright),
        "autonomy_level": autonomy_level,
        "tool_event_id": tool_event_id,
    }
    if dispatch_async:
        threading.Thread(
            target=lambda: asyncio.run(_run_execution(**kwargs)),
            name=f"development-agent-{chat_id}",
            daemon=True,
        ).start()
    else:
        asyncio.run(_run_execution(**kwargs))
        execution = development_execution_state(int(chat_id))
    return {**execution, "direct": True, "id": execution.get("execution_session_id") or job_id}


def stop_development_execution(chat_id: int) -> dict[str, Any]:
    state = development_execution_state(int(chat_id))
    cancelled_workflow = False
    try:
        from distr.core.db.workflow import AutoWorkflowRun
        from distr.core.workflow.dispatcher import cancel_run

        with get_session() as db:
            active_runs = (
                db.query(AutoWorkflowRun)
                .filter(
                    AutoWorkflowRun.chat_id == int(chat_id),
                    AutoWorkflowRun.status.in_(["running", "waiting"]),
                )
                .order_by(AutoWorkflowRun.id.desc())
                .all()
            )
            run_ids = [int(row.id) for row in active_runs]
        for run_id in run_ids:
            cancelled_workflow = cancel_run(run_id) or cancelled_workflow
    except Exception:
        pass

    if str(state.get("status") or "").lower() not in ACTIVE_EXECUTION_STATUSES:
        if cancelled_workflow:
            try:
                from distr.core.workflow.development_control import pause_thread_time

                pause_thread_time(int(chat_id))
            except Exception:
                pass
            return {**state, "stopped": True, "workflow_cancelled": True}
        return {**state, "stopped": False}
    from distr.core.chat_turns import terminal_turn
    from distr.core.project_cli_backends.registry import terminate_backend_process
    from distr.core.turn_runtime import cancel_active_turn

    cooperative_stop = cancel_active_turn(int(chat_id))
    process_stop = terminate_backend_process(
        int(state.get("project_id") or -int(chat_id)),
        str(state.get("backend_id") or "pi"),
        board_id=int(state["board_id"]) if state.get("board_id") is not None else None,
        execution_id=int(chat_id),
        execution_kind="development",
    )
    session_id = state.get("execution_session_id")
    if session_id:
        try:
            from distr.core.kanban.project_execution import complete_execution_session

            complete_execution_session(
                int(session_id),
                success=False,
                error="Stopped by the user.",
                status="cancelled",
            )
        except Exception:
            pass
    updated = _update_execution(
        int(chat_id),
        job_id=str(state.get("job_id") or "") or None,
        status="cancelled",
        completed_at=utc_now_naive().isoformat(),
        summary="Stopped by the user.",
    )
    terminal_turn(
        int(chat_id),
        "turn_cancelled",
        turn_id=int(state["turn_id"]) if state.get("turn_id") else None,
        summary="Stopped by the user.",
    )
    try:
        from distr.core.workflow.development_control import pause_thread_time

        pause_thread_time(int(chat_id))
    except Exception:
        pass
    return {
        **updated,
        "direct": True,
        "stopped": cooperative_stop or process_stop or cancelled_workflow,
        "workflow_cancelled": cancelled_workflow,
    }
