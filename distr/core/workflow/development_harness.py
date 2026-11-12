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

from distr.core.chat import ChatService, _append_user_turn, _chat_params, record_chat_audit_event
from distr.core.db import Chat, get_session
from distr.core.db.projects import Project
from distr.core.db.time import utc_now_naive
from distr.core.workflow.development_threads import development_board, development_scope_identity, development_thread_metadata

logger = logging.getLogger(__name__)

ACTIVE_EXECUTION_STATUSES = {"initializing", "queued", "running", "waiting"}
_FOLLOWUP_STARTUP_PROCESS = uuid.uuid4().hex
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


_LOCAL_MODEL_PROVIDERS = {"ollama", "lmstudio", "lm_studio", "llamacpp", "llama_cpp"}


def hosted_cli_model_or_auto(backend: str, provider: str, model: str) -> str:
    """Drop a local/Ollama model id that cannot run on a hosted CLI backend.

    Codex and Claude Code talk to OpenAI/Anthropic; a stale local pin such as
    ``qwen3.8:27b`` (Ollama ``name:tag``) with provider ``openai`` can never
    start (run 150). Fall back to the CLI's own ``auto`` model instead.
    """
    clean_model = str(model or "").strip()
    if str(backend or "").strip().lower() not in {"codex", "claude_code"}:
        return clean_model
    if str(provider or "").strip().lower() in _LOCAL_MODEL_PROVIDERS:
        return clean_model
    if ":" in clean_model and "/" not in clean_model:
        return "auto"
    return clean_model


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


_RUN_CONTINUITY_PARAGRAPH = re.compile(r"(?:^|\n)[ \t]*run continuity:.*?(?=\n[ \t]*\n|\Z)", re.IGNORECASE | re.DOTALL)


def _prompt_expects_project_change(prompt: str, *, autonomy_level: str) -> bool:
    """Conservatively identify direct implementation requests.

    This is a runtime completion guard, not an intent router. It prevents an
    agent from treating an offer to do the work as successful completion.
    """
    if str(autonomy_level or "").strip().lower() == "plan":
        return False
    # 2026-11-12 merrypak-1114: automation boilerplate ("Run continuity: read and update
    # the retained Decisions automation memory ...") names bookkeeping outside the
    # project, not project work. Its "update" made every scheduled run change-expected,
    # so a clean run with no recorded test receipt was blocked as "Verification incomplete".
    task = _RUN_CONTINUITY_PARAGRAPH.sub("\n\n", str(prompt or "").split('\n\nRetained proposal conversation evidence', 1)[0])
    clean = re.sub(r"\s+", " ", task).strip().lower()
    if not clean or _audit_only_request(clean):
        return False
    if re.match(r"^(?:what|why|how|where|when|who|explain|describe|tell me)\b", clean):
        return False
    # 2026-11-12 fluid1: "without editing any files, ..." is a question, not an implementation request.
    if re.search(r"\b(?:without|no)\s+(?:editing|changing|modifying|touching)\b", clean):
        return False
    return bool(
        re.search(
            r"\b(?:add|build|change|create|delete|edit|fix|give\s+(?:this|the|my)\b|implement|make|move|refactor|remove|rename|replace|set|update|write)\b",
            clean,
        )
    )


def _audit_only_request(prompt: str) -> bool:
    """Scope the current instruction, never an old request in source/history."""
    text = re.sub(r"\s+", " ", str(prompt or "").split('\n\nRetained proposal conversation evidence', 1)[0]).strip().lower()
    if re.search(r'\b(?:audit[ -]only|review[ -]only|strictly read[ -]only)\b', text):
        return True
    if re.match(r"^(?:(?:please|can you|could you|ok)[, ]+)*(?:fix|implement|build|add|change|deploy)\b", text):
        return False
    if re.search(r"\b(?:audit[ -]only|review[ -]only|read[ -]only|do not (?:make fixes|edit|modify|change)|don't (?:make fixes|edit|modify|change))\b", text):
        return True
    if re.search(r"\b(?:and|then)\s+(?:fix|implement|change|deploy|publish)\b", text):
        return False
    return bool(re.match(r"^(?:(?:please|can you|could you|ok)[, ]+)*(?:audit|review|inspect|assess|check whether|check if)\b", text))


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


def _git_dirty_fingerprints(folder: str, status: dict[str, str]) -> dict[str, str]:
    root = _git_project_root(folder)
    if root is None:
        return {}
    fingerprints = {}
    for relative in status:
        candidate = (root / relative).resolve()
        if candidate.is_relative_to(root):
            fingerprints[relative] = _file_fingerprint(candidate)
    return fingerprints


def _git_head(folder: str) -> str:
    result = subprocess.run(['git', '-C', folder, 'rev-parse', '--verify', 'HEAD'],
                            capture_output=True, text=True, check=False, timeout=10)
    return result.stdout.strip() if result.returncode == 0 else ''


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
    before_fingerprints: dict[str, str] | None = None,
    before_head: str = '',
) -> dict[str, Any]:
    root = _git_project_root(folder)
    if root is None:
        return {"files": [], "additions": 0, "deletions": 0, "reversible": False}
    after = _git_status_snapshot(str(root))
    committed = set()
    if before_head and before_head != _git_head(str(root)):
        result = subprocess.run(['git', '-C', str(root), 'diff', '--name-only', '-z', before_head, 'HEAD'],
                                capture_output=True, text=True, check=True, timeout=20)
        committed.update(filter(None, result.stdout.split('\0')))
    before_fps = {
        str(path): str(digest)
        for path, digest in dict(before_untracked or {}).items()
        if path and digest
    }
    after_fps = _git_untracked_fingerprints(str(root), after)

    def content_changed(path: str) -> bool:
        previous = (before_fingerprints or {}).get(path)
        candidate = (root / path).resolve()
        return bool(previous and candidate.is_relative_to(root) and previous != _file_fingerprint(candidate))

    def _untracked_content_changed(path: str) -> bool:
        return (
            before.get(path) == "??"
            and after.get(path) == "??"
            and bool(before_fps.get(path))
            and before_fps.get(path) != after_fps.get(path)
        )

    paths = sorted(
        path
        for path in set(before) | set(after) | committed
        if path in committed or before.get(path) != after.get(path) or _untracked_content_changed(path) or content_changed(path)
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
        status = after.get(relative) or ('committed' if relative in committed else " D" if not candidate.exists() else " M")
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
                ["git", "-C", str(root), "diff", "--numstat", before_head if relative in committed else "HEAD", "--", relative],
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
        if relative in before and before_fingerprints:
            # HEAD includes the user's pre-existing edits. Do not present
            # those line totals as changes made by this turn.
            additions = deletions = 0
        total_additions += additions
        total_deletions += deletions
        files.append(
            {
                "path": relative,
                "status": status,
                "additions": additions,
                "deletions": deletions,
                "after_fingerprint": _file_fingerprint(candidate),
                "line_counts_available": not (relative in before and bool(before_fingerprints)),
                "reversible": relative not in committed and relative not in before and status in {"??", " M", "M ", "MM", " D", "D "},
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


TURN_ROUTE_LIMIT = 200


def _turn_route_entry(receipt: dict[str, Any]) -> dict[str, Any]:
    """Compact, display-only record of the route a single user turn ran on."""
    selected = receipt.get("selected") if isinstance(receipt.get("selected"), dict) else {}
    configured = receipt.get("configured") if isinstance(receipt.get("configured"), dict) else {}
    return {
        "route_mode": str(configured.get("route_mode") or "auto").strip().lower(),
        "complexity": str(receipt.get("complexity") or "").strip().lower(),
        "backend": str(selected.get("backend") or "").strip().lower(),
        "provider": str(selected.get("provider") or "").strip().lower(),
        "model": str(selected.get("model") or "").strip(),
        "skill_ids": [str(item) for item in (receipt.get("turn_skill_ids") or []) if str(item).strip()][:12],
        "skills_auto": bool(receipt.get("turn_skills_auto")),
        "recorded_at": utc_now_naive().isoformat(),
    }


def _remember_turn_route(db, chat_id: int, development: dict[str, Any], receipt: Any, *, turn_id: int | None = None) -> None:
    """Store the route on development.turn_routes keyed by its user row.

    Display-only: routing never reads this. Called inside an open session before commit.
    """
    if not isinstance(receipt, dict) or not isinstance(receipt.get("selected"), dict):
        return
    query = db.query(Chat).filter(Chat.parent_id == int(chat_id), Chat.input.isnot(None))
    user_row = query.filter(Chat.id == turn_id).first() if turn_id is not None else query.order_by(Chat.id.desc()).first()
    if user_row is None:
        return
    routes = dict(development.get("turn_routes") or {})
    key = str(int(user_row.id))
    if key in routes:
        return  # first decision for a turn wins; later execution updates must not rewrite history
    routes[key] = _turn_route_entry(receipt)
    if len(routes) > TURN_ROUTE_LIMIT:
        for old in sorted(routes, key=lambda value: int(value) if str(value).isdigit() else 0)[: len(routes) - TURN_ROUTE_LIMIT]:
            routes.pop(old, None)
    development["turn_routes"] = routes


def record_turn_route(chat_id: int, receipt: dict[str, Any]) -> None:
    """Persist the per-turn route for paths that do not go through _update_execution (workflow dispatch)."""
    try:
        with get_session() as db:
            root = db.get(Chat, int(chat_id))
            if root is None or root.parent_id is not None:
                return
            params = _chat_params(root.params)
            development = dict(params.get("development") or {})
            before = dict(development.get("turn_routes") or {})
            _remember_turn_route(db, int(chat_id), development, receipt)
            if development.get("turn_routes", {}) == before:
                return
            params["development"] = development
            root.params = json.dumps(params, ensure_ascii=False, default=str)
            db.commit()
    except Exception:
        logger.debug("Could not record the per-turn route for chat %s", chat_id, exc_info=True)


class DevelopmentExecutionBusy(ValueError):
    """The current thread state prevents the direct run from starting."""


class DevelopmentScopeChanged(DevelopmentExecutionBusy):
    """The work no longer belongs to the scope captured by this thread."""


def require_direct_execution_available(execution: dict[str, Any]) -> None:
    status = str(execution.get("status") or "").lower()
    # A finished turn may be waiting for a human reply.
    completed_wait = status == "waiting" and execution.get("completed_at")
    if status in ACTIVE_EXECUTION_STATUSES and not completed_wait:
        raise DevelopmentExecutionBusy("Work is already running in this Development thread.")


def _update_execution(chat_id: int, *, job_id: str | None = None, expected_scope: tuple | None = None,
                      reconcile_from: tuple | None = None,
                      resume_job_id: str | None = None,
                      new_prompt: str | None = None, new_prompt_metadata: dict | None = None,
                      expected_autonomy: str | None = None,
                      queued_followup: dict | None = None,
                      telegram_proposal_approval: tuple[str, int] | None = None,
                      scope_lost: bool = False, startup_scope: tuple | None = None,
                      runtime_scope: tuple | None = None, output_delta: str = "",
                      turn_response: str | None = None,
                      start_followup: bool = False, **changes: Any) -> dict[str, Any]:
    with get_session() as db:
        # Execution updates replace the root parameters, which also hold delivery receipts.
        if db.get_bind().dialect.name == 'sqlite':
            db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        root = db.get(Chat, int(chat_id))
        if root is None or root.parent_id is not None:
            raise LookupError("Development thread not found.")
        if expected_scope is not None:
            _require_direct_dispatch_scope(db, root, expected_scope, changes.get('board_id'))
        params = _chat_params(root.params)
        development = dict(params.get("development") or {})
        execution = dict(development.get("execution") or {})
        if start_followup and not execution.get("followup_start"):
            return {}
        if scope_lost or startup_scope is not None or runtime_scope is not None:
            if execution.get("job_id") != job_id or execution.get("status") not in ACTIVE_EXECUTION_STATUSES:
                return execution
            if startup_scope is not None:
                _require_direct_dispatch_scope(db, root, startup_scope, execution.get("board_id"))
            if runtime_scope is not None:
                try:
                    _require_direct_dispatch_scope(db, root, runtime_scope, execution.get("board_id"))
                except DevelopmentScopeChanged as exc:
                    raise DevelopmentScopeChanged(_DIRECT_RESULT_HELD) from exc
        if expected_scope is not None:
            from distr.core.incoming.proposals import require_ready_merge_handoff

            require_ready_merge_handoff(params, root.project_id, db=db, verify_attachments=True)
            if development.get("proactive_dispatch"):
                raise DevelopmentExecutionBusy("Approved proactive work is already running in this Development thread.")
            require_direct_execution_available(execution)
            if resume_job_id and (execution.get("job_id") != resume_job_id or not execution.get("interrupted")):
                raise DevelopmentExecutionBusy("The interrupted turn changed before it could be resumed.")
            if new_prompt is not None:
                autonomy = str(root.autonomy_level or "full").strip().lower()
                manual_token = (queued_followup or {}).get("manual_token")
                if (autonomy != expected_autonomy or autonomy == "approval"
                        or (development.get("workflow_id") and not manual_token)
                        or str(development.get("source_type") or "").lower() == "automation"):
                    raise DevelopmentExecutionBusy(
                        "The Development thread settings changed. Review the thread before sending again."
                    )
                if queued_followup is not None:
                    if manual_token:
                        from .queued_submissions import _manual_start_matches, _require_manual_source
                        from distr.core.db.workflow import AutoWorkflowRun, DevelopmentCommand

                        command_id = queued_followup["command_id"]
                        command = db.get(DevelopmentCommand, command_id)
                        if not _manual_start_matches(command, root, manual_token):
                            raise DevelopmentExecutionBusy("The queued item changed before startup.")
                        _require_manual_source(db, command, _json(command.metadata_json)["manual_start"])
                        if db.query(AutoWorkflowRun.id).filter_by(chat_id=root.id).filter(
                            AutoWorkflowRun.status.in_(["initializing", "queued", "running", "waiting", "paused"]),
                        ).first():
                            raise DevelopmentExecutionBusy("A workflow is still running. The item remains queued.")
                    else:
                        accept = _accept_next_followup if queued_followup.get("next_followup") else _accept_proactive_followup
                        command_id = accept(db, root, development, queued_followup)
                    new_prompt_metadata = {**(new_prompt_metadata or {}), "queued_command_id": command_id}
                    changes["followup_start"] = {"command_id": command_id, "process": _FOLLOWUP_STARTUP_PROCESS}
                    if manual_token:
                        changes["followup_start"]["manual_token"] = manual_token
                changes["turn_id"] = _append_user_turn(db, root, params, new_prompt, new_prompt_metadata)
        if reconcile_from is not None:
            scope, saved_execution = reconcile_from
            if execution != saved_execution or _development_execution_scope(db, root, development) != scope:
                return execution
            if not scope_lost and not _direct_record_owns_scope(db, root, development, execution):
                return execution
        existing_job_id = execution.get("job_id")
        starts_new_turn = bool(
            job_id
            and existing_job_id not in {None, job_id}
            and str(changes.get("status") or "").lower() in {"initializing", "queued"}
            and changes.get("started_at")
        )
        if job_id and existing_job_id not in {None, job_id} and not starts_new_turn:
            return execution
        if starts_new_turn or expected_scope is not None:
            # 2026-11-12 fluid1: keep the previous turn's edited-files list (display only) so the
            # thread can keep showing it while the next prompt runs, like Codex. Undo still only
            # targets the current turn's own "changes".
            prior_changes = execution.get("changes") if isinstance(execution.get("changes"), dict) else {}
            if not (prior_changes.get("files") and not prior_changes.get("undone")):
                prior_changes = execution.get("last_changes") if isinstance(execution.get("last_changes"), dict) else {}
            carried = (
                {key: prior_changes.get(key) for key in ("files", "additions", "deletions", "undone")}
                if starts_new_turn and prior_changes.get("files") and not prior_changes.get("undone") else None
            )
            execution = {"last_changes": carried} if carried else {}
        execution.update({key: value for key, value in changes.items() if value is not None})
        if output_delta:
            execution["streamed_output"] = (str(execution.get("streamed_output") or "") + output_delta)[-24_000:]
        if job_id:
            execution["job_id"] = job_id
        if turn_response is not None and execution.get("turn_id") is not None:
            turn = db.get(Chat, execution["turn_id"])
            if turn is not None and turn.parent_id == root.id:
                turn.response = turn_response
                turn.modified_date = utc_now_naive()
        if start_followup or changes.get("status") in {"failed", "cancelled", "blocked"}:
            _settle_followup_start(db, root, execution, started=start_followup)
        if changes.get("route_receipt") is not None:
            try:
                _remember_turn_route(db, int(chat_id), development, changes.get("route_receipt"), turn_id=execution.get("turn_id"))
            except Exception:
                logger.debug("Could not record the per-turn route for chat %s", chat_id, exc_info=True)
        development["execution"] = execution
        params["development"] = development
        if telegram_proposal_approval is not None:
            from distr.core.incoming.telegram_updates import _recipient_id, retain_previous_proposal_approval

            proposal_key, recipient = telegram_proposal_approval
            recipient = _recipient_id(recipient)
            handoff = params.get("incoming_proposal_handoff")
            if (not isinstance(handoff, dict) or handoff.get("key") != proposal_key
                    or not handoff.get("start_pending") or not job_id or recipient is None):
                raise ValueError("The Telegram approval no longer matches this proposal. Review it again.")
            prior = handoff.get("telegram_approval")
            if prior is not None and (not isinstance(prior, dict) or prior.get("chat_id") != recipient):
                raise ValueError("This proposal is bound to a different Telegram approval chat.")
            retain_previous_proposal_approval(handoff, prior, job_id)
            handoff["telegram_approval"] = {"chat_id": recipient, "job_id": job_id, "project_id": root.project_id}
        publish_status = bool(changes.get("status") and not scope_lost)
        if publish_status and not any(scope is not None for scope in (expected_scope, startup_scope, runtime_scope)):
            publish_status = _direct_record_owns_scope(db, root, development, execution)
        publish_proposal_outcome = publish_status and "incoming_proposal_handoff" in params
        if publish_proposal_outcome:
            from distr.core.incoming.telegram_updates import retain_local_proposal_outcome

            retain_local_proposal_outcome(params, execution)
        root.params = json.dumps(params, ensure_ascii=False, default=str)
        root.modified_date = utc_now_naive()
        from distr.core.db.kanban import KanbanTicket
        from distr.core.db.workflow import DevelopmentWorkItem

        work_item = db.query(DevelopmentWorkItem).filter(DevelopmentWorkItem.chat_id == int(chat_id)).first()
        ticket = db.get(KanbanTicket, int(work_item.local_ticket_id)) if work_item and work_item.local_ticket_id else None
        next_status = str(execution.get("status") or "").strip().lower()
        if publish_status and ticket is not None and next_status in {
            "initializing", "queued", "running", "waiting", "paused",
            "completed", "completed_noop", "failed", "blocked", "cancelled",
        }:
            ticket.workflow_status = "completed" if next_status == "completed_noop" else next_status
        db.commit()
    if publish_proposal_outcome:
        try:
            from distr.core.incoming.telegram_updates import queue_local_proposal_outcome

            queue_local_proposal_outcome(chat_id, params)
        except Exception:
            logger.warning("Could not queue the Telegram proposal outcome for thread %s. It will retry.", chat_id,
                           exc_info=True)
    _notify()
    return execution


def _execution_record_id(value: Any) -> int | None:
    if type(value) is int:
        return value if 0 < value < 2**63 else None
    if isinstance(value, str) and 0 < len(value) <= 19 and value.isascii() and value.isdecimal():
        number = int(value)
        if 0 < number < 2**63 and str(number) == value:
            return number
    return None


def _development_execution_scope(db, root, metadata):
    from distr.core.db.workflow import DevelopmentWorkItem

    item = db.query(DevelopmentWorkItem).filter_by(chat_id=root.id).first()
    if (item is not None and item.project_id != root.project_id) or (item is None and not metadata):
        return None
    ticket = item.local_ticket_id if item is not None else metadata.get("ticket_id")
    ticket_id = _execution_record_id(ticket)
    if ticket is not None and ticket_id is None:
        return None
    return root.project_id if root.project_id is not None else -root.id, ticket_id


def _session_matches_development(row, scope, *, chat_id, turn_id):
    if row is None or scope is None or (row.project_id, row.ticket_id) != scope:
        return False
    try:
        packet = json.loads(row.input_packet or "{}")
    except (TypeError, ValueError):
        return False
    if not isinstance(packet, dict):
        return False
    if packet.get("chat_id") is not None and _execution_record_id(packet["chat_id"]) != chat_id:
        return False
    if packet.get("turn_id") is not None and turn_id is not None:
        expected = _execution_record_id(turn_id)
        return expected is not None and _execution_record_id(packet["turn_id"]) == expected
    return True


def _unreported_cli_session(db, scope, chat_id, execution):
    if (scope is None or execution.get("runtime_id") != "cli_harness" or execution.get("completed_at")
            or str(execution.get("status") or "").lower() not in ACTIVE_EXECUTION_STATUSES):
        return None
    turn_id = _execution_record_id(execution.get("turn_id"))
    board_id = _execution_record_id(execution.get("board_id"))
    if turn_id is None or (execution.get("board_id") is not None and board_id is None):
        return None

    from sqlalchemy import case, func
    from distr.core.db.kanban import ProjectExecutionSession
    from distr.core.project_cli_backends.registry import backend_process_registered

    project_id, ticket_id = scope
    packet = case(
        (func.json_valid(ProjectExecutionSession.input_packet) == 1, ProjectExecutionSession.input_packet),
        else_="{}",
    )
    candidates = db.query(ProjectExecutionSession).filter(
        ProjectExecutionSession.project_id == project_id,
        ProjectExecutionSession.ticket_id.is_not_distinct_from(ticket_id),
        ProjectExecutionSession.origin == "development",
        ProjectExecutionSession.route_type == "project_cli",
        ProjectExecutionSession.workflow_id.is_(None),
        ProjectExecutionSession.run_id.is_(None),
        ProjectExecutionSession.step_id.is_(None),
        ProjectExecutionSession.status.in_(ACTIVE_EXECUTION_STATUSES),
        ProjectExecutionSession.completed_at.is_(None),
        func.json_type(packet, "$.chat_id") == "integer",
        func.json_extract(packet, "$.chat_id") == chat_id,
        func.json_type(packet, "$.turn_id") == "integer",
        func.json_extract(packet, "$.turn_id") == turn_id,
        func.json_type(packet, "$.board_id") == ("null" if board_id is None else "integer"),
        func.json_extract(packet, "$.board_id").is_not_distinct_from(board_id),
    )
    # The session row precedes the best-effort startup event. A quiet CLI can
    # still be stopped when exactly one scoped session owns a live process.
    active = [
        row for row in candidates
        if backend_process_registered(
            project_id, row.route_backend, board_id=board_id,
            execution_id=row.id, execution_kind="development_session",
        )
    ]
    return active[0] if len(active) == 1 else None


def _scoped_development_runs(db, chat_id, scope):
    from sqlalchemy import and_, case, cast, func, or_, String
    from distr.core.db.workflow import AutoWorkflowRun

    query = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.chat_id == chat_id)
    if scope is None:
        return query.filter(False)
    project_id, ticket_id = scope
    data = case((func.json_valid(AutoWorkflowRun.run_data) == 1, AutoWorkflowRun.run_data), else_="{}")
    run_project = func.json_extract(data, "$.project_id")
    return query.filter(
        AutoWorkflowRun.ticket_id.is_not_distinct_from(ticket_id),
        or_(run_project.is_(None), and_(
            func.json_type(data, "$.project_id").in_(["integer", "text"]),
            cast(run_project, String) == str(project_id),
        )),
    )


def _client_reply_approval(db, run, request_id):
    from distr.core.db import DevApprovalRequest
    from distr.core.workflow.dev_approvals import args_hash

    if not isinstance(request_id, str) or not request_id:
        return None, {}
    approval = db.query(DevApprovalRequest).filter_by(
        request_id=request_id, tool="reply_delivery", chat_id=run.chat_id,
    ).one_or_none()
    if approval is None:
        return None, {}
    details = _json(approval.details_json)
    details.pop("delivery_outcome", None)
    context = details.get("workflow")
    if (isinstance(context, dict) and context.get("run_id") == run.id
            and context.get("workflow_id") == run.workflow_id and context.get("chat_id") == run.chat_id
            and context.get("turn_id") == approval.turn_id and details.get("ticket_id") == run.ticket_id
            and details.get("channel") == approval.action and args_hash(details) == approval.args_hash):
        return approval, details
    return None, {}


def development_client_reply_review(chat_id, run_id):
    """Read the currently selected run's exact pending approval."""
    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.workflow.reply_channel import resolve_reply_target
    from distr.core.workflow.reply_delivery import _delivery_envelope, _validate_workflow_context

    unavailable = "The client reply review is no longer available. Check the current run."
    with get_session() as db:
        root = db.get(Chat, chat_id)
        if root is None or root.parent_id is not None:
            raise LookupError(unavailable)
        metadata = development_thread_metadata(root)
        if metadata.get("execution") or metadata.get("proactive_dispatch"):
            raise LookupError(unavailable)
        scope = _development_execution_scope(db, root, metadata)
        run = _scoped_development_runs(db, chat_id, scope).order_by(AutoWorkflowRun.id.desc()).first()
        if run is None or run.id != run_id or run.status != "completed":
            raise LookupError(unavailable)
        outcome = _json(run.run_data).get("reply_delivery")
        if not isinstance(outcome, dict) or outcome.get("pending_approval") is not True or outcome.get("telegram_flow_key"):
            raise LookupError(unavailable)
        approval, details = _client_reply_approval(db, run, outcome.get("approval_request_id"))
        if approval is None or approval.status != "pending" or approval.expires_at < utc_now_naive():
            raise LookupError(unavailable)
        try:
            envelope, body = details["delivery"], details["body"]
            if not isinstance(body, str) or not body.strip():
                raise ValueError(unavailable)
            _validate_workflow_context(db, details["workflow"], envelope, run.ticket_id)
            ticket = db.get(KanbanTicket, run.ticket_id)
            target = resolve_reply_target(ticket) if ticket is not None else None
            if (target is None or not target.can_send
                    or _delivery_envelope(db, ticket, target, envelope["subject"], envelope.get("resolved_target")) != envelope):
                raise ValueError(unavailable)
            prepared = envelope.get("resolved_target") or {}
            return {
                "run_id": run.id, "channel": details["channel"], "body": body,
                "from": prepared.get("from"), "to": prepared.get("to") or [envelope["recipient"]],
                "cc": prepared.get("cc") or [], "subject": envelope["subject"],
            }
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise LookupError(unavailable) from exc


def _client_reply_status(db, run, outcome):
    """Project the closing reply state without returning its body or destination."""
    if run.status != "completed" or not isinstance(outcome, dict):
        return None
    if outcome.get("delivery_key"):
        from distr.core.workflow.telegram_reply_receipts import run_status

        status = run_status(db, run, outcome["delivery_key"])
        if status:
            return status
    if outcome.get("delivered") is True:
        return "sent"
    if outcome.get("unconfirmed") is True:
        return "unconfirmed"
    if outcome.get("pending_approval") is True:
        flow_key = outcome.get("telegram_flow_key")
        if flow_key:
            from distr.core.initiative.outcome_flow import get_flow

            flow = get_flow(flow_key) if isinstance(flow_key, str) else None
            if not isinstance(flow, dict) or not isinstance(flow.get("status"), str):
                return "review_unavailable"
            context = flow.get("approval_context")
            if (flow.get("kind") != "reply" or flow.get("ticket_id") != run.ticket_id
                    or not isinstance(context, dict) or context.get("run_id") != run.id
                    or context.get("workflow_id") != run.workflow_id or context.get("chat_id") != run.chat_id):
                return "review_unavailable"
            if flow.get("delivery_key"):
                from distr.core.workflow.telegram_reply_receipts import run_status

                status = run_status(db, run, flow["delivery_key"])
                if status:
                    return status
            return {
                "awaiting_decision": "telegram_review", "confirming": "telegram_review",
                "checking": "telegram_review", "continuing": "telegram_review",
                "sending": "sending", "unconfirmed": "unconfirmed", "sent": "sent",
                "send_failed": "not_sent",
            }.get(flow.get("status"), "review_unavailable")
        approval, _ = _client_reply_approval(db, run, outcome.get("approval_request_id"))
        if approval is not None:
            from distr.core.workflow.dev_approvals import request_view

            status = request_view(approval, receipt_session=db)["status"]
            if status == "pending" and approval.expires_at < utc_now_naive():
                return "not_sent"
            return {
                "pending": "desktop_review", "sending": "sending", "sent": "sent",
                "unconfirmed": "unconfirmed", "rejected": "not_sent", "denied": "not_sent",
                "expired": "not_sent",
            }.get(status, "review_unavailable")
        return "review_unavailable"
    if outcome.get("approval_status") in {"denied", "expired"} or any(
        outcome.get(field) is True for field in ("preflight_failed", "scope_changed", "duplicate", "unsupported", "blocked")
    ):
        return "not_sent"
    return None


def _unresolved_queue_handoff(db, run, project_id):
    if run.status != "completed" or run.board_id is None or run.ticket_id is None:
        return None
    from distr.core.chat_turns import redact_text
    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.workflow.dispatcher import _queue_already_advanced

    event = db.query(OrchestratorEvent).filter_by(
        event_type="outcome_workflow_queue_held", status="held", workflow_id=run.workflow_id,
        run_id=run.id, board_id=run.board_id, project_id=project_id,
    ).order_by(OrchestratorEvent.id.desc()).first()
    if event is None or event.ticket_id is None:
        return None
    predecessor = db.get(KanbanTicket, run.ticket_id)
    if predecessor is None or predecessor.lane is None or predecessor.lane.board_id != run.board_id:
        return None
    if _queue_already_advanced(db, run.workflow_id, run.id):
        return None
    reason = redact_text(event.summary, limit=300).strip()
    return {"ticket_id": event.ticket_id, "reason": reason} if reason else None


def development_execution_state(chat_id: int) -> dict[str, Any]:
    terminal_reconciliation: dict[str, Any] | None = None
    workflow_execution: dict[str, Any] | None = None
    session_rejected = False
    with get_session() as db:
        root = db.get(Chat, int(chat_id))
        if root is None or root.parent_id is not None:
            raise LookupError("Development thread not found.")
        metadata = development_thread_metadata(root)
        execution = dict(metadata.get("execution") or {})
        saved_execution = dict(execution)
        turn_routes = dict(metadata.get("turn_routes") or {})
        result_turn_id = _execution_record_id(metadata.get("last_proactive_result_turn_id"))
        result_marker = {"last_proactive_result_turn_id": result_turn_id} if result_turn_id else {}
        scope = _development_execution_scope(db, root, metadata)
        if metadata.get("proactive_dispatch"):
            from distr.core.orchestrator_proactive import proactive_development_execution

            proactive = proactive_development_execution(db, root, scope, metadata["proactive_dispatch"])
            if proactive is None:
                proactive = {
                    "origin": "proactive_orchestrator", "direct": True, "status": "blocked", "can_stop": False,
                    "summary": "The previous approval no longer matches this thread. Review its outcome before continuing.",
                }
            return {**proactive, **result_marker, "turn_routes": turn_routes}
        if not execution:
            from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep
            from distr.core.workflow.draft_launch import approved_workflow_stop_reason

            run = (
                _scoped_development_runs(db, int(chat_id), scope)
                .order_by(AutoWorkflowRun.id.desc())
                .first()
            )
            if run is not None:
                run_data = _json(run.run_data)
                step = db.get(AutoWorkflowStep, int(run.current_step_id)) if run.current_step_id else None
                result_packet = run_data.get("result_packet") if isinstance(run_data.get("result_packet"), dict) else {}
                audit = result_packet.get("audit") if isinstance(result_packet.get("audit"), dict) else {}
                route = run_data.get("execution_route") if isinstance(run_data.get("execution_route"), dict) else {}
                route_receipt = run_data.get("route_receipt") if isinstance(run_data.get("route_receipt"), dict) else {}
                if not route_receipt and route:
                    selected = {
                        "backend": route.get("backend"),
                        "provider": route.get("model_provider") or route.get("provider"),
                        "model": route.get("model"),
                        "reasoning_effort": route.get("codex_reasoning_effort") or route.get("reasoning_effort"),
                        "service_tier": route.get("codex_service_tier") or route.get("service_tier") or route.get("tier"),
                    }
                    route_receipt = {"selected": {key: value for key, value in selected.items() if value not in (None, "")}}
                workflow_execution = {
                    "id": int(run.id),
                    "direct": False,
                    "workflow_id": int(run.workflow_id),
                    "status": str(run.status or "queued"),
                    "current_step_id": int(run.current_step_id) if run.current_step_id else None,
                    "current_step_name": str(step.name or "") if step is not None else "",
                    "started_at": run.started_at.isoformat() if run.started_at else None,
                    "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                    "waiting_kind": str(run_data.get("waiting_kind") or ""),
                    "summary": approved_workflow_stop_reason(run.status, run_data) or str(result_packet.get("summary") or ""),
                    "verification": audit,
                    "output_packet": result_packet,
                    "execution_mode_decision": run_data.get("execution_mode_decision") or {},
                    "route_receipt": route_receipt,
                }
                if run.status == "waiting" and run.completed_at is None:
                    from distr.core.chat_turns import redact_text

                    approval = run_data.get("pending_route_approval")
                    approval = approval if isinstance(approval, dict) else {}
                    model = approval.get("name") or approval.get("model")
                    question = run_data.get("waiting_prompt")
                    if run_data.get("waiting_kind") == "provider_preflight" and model:
                        question = f"Use {model} for this step and continue the workflow."
                    workflow_execution["waiting_prompt"] = redact_text(question or "", limit=12000)
                client_reply_status = _client_reply_status(db, run, run_data.get("reply_delivery"))
                if client_reply_status:
                    workflow_execution["client_reply_status"] = client_reply_status
                queue_hold = _unresolved_queue_handoff(db, run, root.project_id)
                if queue_hold:
                    workflow_execution["queue_handoff_hold"] = queue_hold
                if (run.status == "failed" and run_data.get("source_type") == "workflow_queue"
                        and run_data.get("queue_scope_changed") is True):
                    from distr.core.chat_turns import redact_text

                    warning = redact_text(run_data.get("terminal_warning"), limit=300).strip()
                    if warning:
                        workflow_execution["queue_scope_stop"] = warning
                elif run.status == "failed" and run_data.get("source_type") == "workflow_queue":
                    from distr.core.chat_turns import redact_text

                    failure = run_data.get("ide_reported_failure")
                    if isinstance(failure, dict) and failure.get("step_id") == run.current_step_id:
                        report = redact_text(failure.get("output"), limit=300).strip()
                        workflow_execution["queue_ide_failure"] = report or "The IDE reported a failure."
        if result_turn_id and workflow_execution is None:
            from distr.core.orchestrator_proactive import proactive_waiting_review_state

            review = proactive_waiting_review_state(db, root, scope, result_turn_id)
            if review is not None:
                return {**review, **result_marker, "turn_routes": turn_routes}
        persisted_status = str(execution.get("status") or "").lower()
        if execution.get("execution_session_id") is None:
            row = _unreported_cli_session(db, scope, root.id, execution)
            if row is not None:
                execution["execution_session_id"] = row.id
        else:
            from distr.core.db.kanban import ProjectExecutionSession

            session_id = _execution_record_id(execution["execution_session_id"])
            row = db.get(ProjectExecutionSession, session_id) if session_id is not None else None
        if execution.get("execution_session_id") is not None:
            if not _session_matches_development(row, scope, chat_id=root.id, turn_id=execution.get("turn_id")):
                session_rejected = True
            elif (execution.get("status") != "cancelled" and not execution.get("interrupted")
                  and not execution.get("result_held")
                  and _direct_record_owns_scope(db, root, metadata, execution)):
                row_status = str(row.status or "").lower()
                interrupted = row_status == "interrupted"
                if interrupted:
                    row_status = "failed"
                # Provider completion precedes saving the reply and completing
                # the chat turn. Only the owning finalizer can publish success.
                finalizing = row_status == "completed" and persisted_status in ACTIVE_EXECUTION_STATUSES
                if row.route_type == "native_turn" and row_status == "queued" and persisted_status == "running":
                    row_status = "running"
                execution.update(
                    {
                        "status": persisted_status if row_status == "completed" and persisted_status else row_status or execution.get("status") or "queued",
                        "backend_id": row.route_backend or execution.get("backend_id") or "",
                        "model": row.selected_model or execution.get("model") or "auto",
                        "started_at": row.started_at.isoformat() if row.started_at else execution.get("started_at"),
                        "completed_at": None if finalizing else row.completed_at.isoformat() if row.completed_at else execution.get("completed_at"),
                        "error": row.error or execution.get("error") or "",
                    }
                )
                output = _json(row.output_packet)
                if output:
                    execution["output_packet"] = output
                if interrupted or (row_status in {"failed", "cancelled"} and row_status != persisted_status):
                    if interrupted:
                        execution["interrupted"] = True
                    terminal_reconciliation = {
                        "status": row_status,
                        "interrupted": interrupted,
                        "backend_id": row.route_backend or execution.get("backend_id") or "",
                        "model": row.selected_model or execution.get("model") or "auto",
                        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                        "error": row.error or execution.get("error") or "",
                        "activity_status": row_status,
                    }
            if (not session_rejected and execution.get("runtime_id") == "cli_harness"
                    and row.origin == "development" and row.route_type == "project_cli"):
                packet = _json(row.input_packet)
                turn_id = _execution_record_id(execution.get("turn_id"))
                if (turn_id is not None and _execution_record_id(packet.get("turn_id")) == turn_id
                        and _execution_record_id(packet.get("chat_id")) == root.id):
                    from distr.core.project_cli_backends.registry import backend_process_registered

                    board_id = _execution_record_id(packet.get("board_id"))
                    execution["cli_stop_available"] = backend_process_registered(
                        row.project_id, row.route_backend, board_id=board_id,
                        execution_id=row.id, execution_kind="development_session",
                    )
                    execution["project_id"] = row.project_id
                    execution["board_id"] = board_id
    if workflow_execution is not None:
        workflow_execution["turn_routes"] = turn_routes
        return {**workflow_execution, **result_marker}
    if terminal_reconciliation is not None:
        _update_execution(
            int(chat_id),
            job_id=str(execution.get("job_id") or "") or None,
            reconcile_from=(scope, saved_execution),
            **terminal_reconciliation,
        )
    followup_start = execution.get("followup_start")
    if isinstance(followup_start, dict) and followup_start.get("process") != _FOLLOWUP_STARTUP_PROCESS:
        message = "Decisions restarted before the follow-up reached the worker."
        execution = _update_execution(
            int(chat_id), job_id=execution.get("job_id"), reconcile_from=(scope, saved_execution),
            scope_lost=True, status="failed", completed_at=utc_now_naive().isoformat(),
            activity_status="failed", summary=message, error=message,
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
                reconcile_from=(scope, saved_execution),
                status="failed",
                interrupted=True,
                completed_at=utc_now_naive().isoformat(),
                activity_status="failed",
                summary="The native Development turn was interrupted when DecisionsAI stopped.",
                error="The native Development turn was interrupted when DecisionsAI stopped.",
            )
    if session_rejected:
        execution.pop("execution_session_id", None)
    execution["direct"] = True
    execution["id"] = execution.get("execution_session_id") or execution.get("job_id")
    execution["turn_routes"] = turn_routes
    execution.pop("followup_start", None)
    return {**execution, **result_marker}


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


_DIRECT_SCOPE_CHANGED = "The Development project, board or ticket changed before work started. Review its scope and retry."
_DIRECT_RESULT_HELD = ("The Development project, board or ticket changed while the worker was running. "
                       "Review the original project for any changes before retrying.")


def _direct_scope_board(db, root, metadata):
    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.workflow import DevelopmentWorkItem
    from distr.core.workflow.development_sidebar import board_key_for

    ticket_id = metadata.get("ticket_id")
    board = development_board(db, ticket_id=ticket_id, board_key=metadata.get("board_key"))
    if root.is_archived or (board and board.archived):
        raise DevelopmentScopeChanged(_DIRECT_SCOPE_CHANGED)
    item = db.query(DevelopmentWorkItem).filter_by(chat_id=root.id).first()
    ticket = db.get(KanbanTicket, ticket_id) if ticket_id is not None else None
    keys = (metadata.get("board_key"), item.board_key if item else None)
    board_key = board_key_for(board)
    local_scope = any(str(key or "").startswith("decisions:") for key in keys)
    if not local_scope:
        # Older unbound ticket references may inform routing but do not own the thread.
        local_scope = bool(str(board_key or "").startswith("decisions:") and (
            (item and item.local_ticket_id is not None) or (ticket and ticket.source_chat_id == root.id)
        ))
    if not local_scope or (ticket_id is None and (item is None or item.local_ticket_id is None)):
        return board
    if (not ticket or not board_key or not board_key.startswith("decisions:")
            or any(key and key != board_key for key in keys)
            or ticket.source_chat_id != root.id
            or (item is not None and (item.local_ticket_id != ticket.id or item.project_id != root.project_id))):
        raise DevelopmentScopeChanged(_DIRECT_SCOPE_CHANGED)
    effective_project = ticket.linked_project_id if ticket.linked_project_id is not None else board.default_project_id
    if effective_project != root.project_id:
        raise DevelopmentScopeChanged(_DIRECT_SCOPE_CHANGED)
    return board


def _direct_record_owns_scope(db, root, metadata, execution):
    try:
        board = _direct_scope_board(db, root, metadata)
    except (TypeError, ValueError):
        return False
    return "board_id" not in execution or execution["board_id"] == (board.id if board else None)


def _require_direct_dispatch_scope(db, root, expected_scope, board_id):
    metadata = development_thread_metadata(root)
    if development_scope_identity(root.project_id, metadata, chat_id=root.id) != expected_scope:
        raise DevelopmentScopeChanged(_DIRECT_SCOPE_CHANGED)
    board = _direct_scope_board(db, root, metadata)
    project = db.get(Project, root.project_id) if root.project_id is not None else None
    if (board.id if board else None) != board_id or (
        root.project_id is not None and (project is None or project.deleted_at is not None)
    ):
        raise DevelopmentScopeChanged(_DIRECT_SCOPE_CHANGED)


def _direct_job_can_start(chat_id, job_id, expected_scope, board_id):
    with get_session() as db:
        root = db.get(Chat, chat_id)
        if root is None or root.parent_id is not None:
            return False
        execution = development_thread_metadata(root).get("execution") or {}
        if execution.get("job_id") != job_id or execution.get("status") not in ACTIVE_EXECUTION_STATUSES:
            return False
        _require_direct_dispatch_scope(db, root, expected_scope, board_id)
        return True


def _project_and_scope(chat_id: int) -> tuple[Any, dict[str, Any], int | None, int | None]:
    project_snapshot = None
    with get_session() as db:
        root = db.get(Chat, int(chat_id))
        if root is None or root.parent_id is not None or not development_thread_metadata(root):
            raise LookupError("Development thread not found.")
        metadata = dict(development_thread_metadata(root))
        ticket_id = int(metadata["ticket_id"]) if metadata.get("ticket_id") is not None else None
        board = _direct_scope_board(db, root, metadata)
        board_id = int(board.id) if board else None
        project = db.get(Project, int(root.project_id)) if root.project_id is not None else None
        if root.project_id is not None and (project is None or project.deleted_at is not None):
            raise DevelopmentScopeChanged(_DIRECT_SCOPE_CHANGED)
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


def _route(
    project: Any, metadata: dict[str, Any], assessment: dict[str, Any], *, board_id: int | None = None,
) -> tuple[str, str, dict[str, Any], str]:
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
        model = hosted_cli_model_or_auto(pinned_backend, provider, model) or "auto"
        return pinned_backend, model, adapter_options, complexity

    studio_preview = assessment.get("route_source") == "studio_preview"
    assessed = assessment.get("route") if not studio_preview and isinstance(assessment.get("route"), dict) else {}
    if board_id is None:
        resolved = resolve_ticket_cli_route(project, complexity)
    else:
        from distr.core.db.kanban import KanbanBoard

        with get_session() as db:
            resolved = resolve_ticket_cli_route(project, complexity, board=db.get(KanbanBoard, board_id))
    backend = str(
        assessed.get("backend") or resolved.get("backend") or project.coding_backend or "pi"
    ).strip().lower()
    backend = normalize_backend_id(backend)
    model = str(
        assessed.get("model") or resolved.get("model") or project.coding_backend_model or "auto"
    ).strip() or "auto"
    signals = assessment.get("signals")
    if not assessed and isinstance(signals, list) and "vision" in signals:
        model = "auto"
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
        reasoning = assessed.get("codex_reasoning_effort") or resolved.get("codex_reasoning_effort")
        service_tier = assessed.get("codex_service_tier") or resolved.get("codex_service_tier")
        if reasoning:
            adapter_options["reasoning_effort"] = reasoning
        if service_tier:
            adapter_options["service_tier"] = service_tier
    return backend, model, adapter_options, complexity


def _route_receipt(
    metadata: dict[str, Any],
    assessment: dict[str, Any],
    *,
    backend: str,
    model: str,
    adapter_options: dict[str, Any],
    complexity: str,
    actual: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Capture requested, selected, and actual routing without changing routing policy."""
    model_route = metadata.get("model_route") if isinstance(metadata.get("model_route"), dict) else {}
    laya = assessment.get("laya") if isinstance(assessment.get("laya"), dict) else {}
    route_mode = str(model_route.get("route_mode") or "auto").strip().lower()
    provider = str(adapter_options.get("model_provider") or "").strip().lower()
    selected = {
        "backend": str(backend or "").strip().lower(),
        "provider": provider,
        "model": str(model or "auto").strip() or "auto",
        "reasoning_effort": str(adapter_options.get("reasoning_effort") or "medium"),
        "service_tier": str(adapter_options.get("service_tier") or "standard"),
        "source": "manual_pin" if route_mode == "manual" else str(assessment.get("decision_engine") or "policy"),
    }
    fallback = {
        "backend": str(adapter_options.get("fallback_backend") or "").strip().lower(),
        "provider": str(adapter_options.get("fallback_model_provider") or "").strip().lower(),
        "model": str(adapter_options.get("fallback_model") or "").strip(),
    }
    reason = str(assessment.get("reason") or "")
    studio_preview = assessment.get("route_source") == "studio_preview"
    preview_route = assessment.get("route")
    preview_backend = preview_route.get("backend") if isinstance(preview_route, dict) else None
    if route_mode == "manual" and (studio_preview or not reason):
        reason = f"This thread is pinned to {selected['backend']}."
    elif (studio_preview and preview_backend != backend) or (not reason and assessment.get("decision_engine")):
        reason = f"The current routing policy selected {selected['backend']} for {complexity} complexity."
    return {
        "complexity": complexity,
        "decision_engine": str(assessment.get("decision_engine") or "heuristic"),
        "decision_reason": reason,
        "laya": {
            "mode": str(laya.get("mode") or "off"),
            "used": bool(laya.get("used")),
            "confidence": laya.get("confidence"),
            "fallback_reason": str(laya.get("fallback_reason") or ""),
        },
        "configured": {
            "route_mode": route_mode,
            "backend": str(model_route.get("backend") or "").strip().lower(),
            "provider": str(model_route.get("provider") or "").strip().lower(),
            "model": str(model_route.get("model_name") or "").strip(),
            "reasoning_effort": str(model_route.get("reasoning_effort") or "medium"),
            "service_tier": str(model_route.get("service_tier") or "standard"),
        },
        "selected": selected,
        "actual": dict(actual or {}),
        "fallback": fallback if fallback["backend"] or fallback["model"] else {},
    }


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



def _ticket_lane_scope_line(metadata: dict[str, Any]) -> str:
    ticket_id = metadata.get("ticket_id")
    try:
        ticket_id = int(ticket_id) if ticket_id is not None else None
    except (TypeError, ValueError):
        ticket_id = None
    if ticket_id is None:
        return ""
    from distr.core.kanban.lane_history import current_ticket_lane, recent_lane_history_for_ticket

    lane = current_ticket_lane(ticket_id) or metadata.get("board_ticket_lane") or ""
    if not lane:
        return ""
    history = recent_lane_history_for_ticket(ticket_id, limit=5)
    recent = "; ".join(
        f"{(row.get('from_lane') or 'created')}→{row.get('to_lane')}"
        for row in history
        if isinstance(row, dict)
    )
    suffix = f" Recent lane history: {recent}." if recent else ""
    return f"Ticket lane: {lane}.{suffix}"


def _agent_instruction(chat_id: int, prompt: str, metadata: dict[str, Any], *, autonomy_level: str) -> str:
    from distr.core.chat_agents import agent_instructions
    from distr.core.incoming.source_context import thread_source_instructions
    from distr.core.turn_runtime.context import DEVELOPMENT_RESPONSE_GUIDANCE

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
        _ticket_lane_scope_line(metadata),
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
    if _audit_only_request(prompt) or metadata.get('work_mode') == 'audit':
        mode_instruction = (
            "Audit-only mode. Inspect the existing work and return evidence and gaps. "
            "Do not edit files, repair defects, commit, push, deploy, send messages or start another engine. "
            "Older implementation or deployment requests in the history are background, not current authorization. "
            "Stop after the audit report and wait for explicit repair authorization."
        )
    selected_skills = [str(skill_id).strip() for skill_id in (metadata.get("turn_skill_ids") or []) if str(skill_id).strip()]
    recommendation = str(metadata.get("skill_recommendation") or "").strip()
    if selected_skills and recommendation:
        skill_instruction = (
            recommendation
            + " Skills for this turn: "
            + ", ".join(selected_skills)
            + ". Read each exact skill with read_harness_skill before acting, then apply its instructions. Do not search or list the skill catalog first."
        )
    elif selected_skills:
        skill_instruction = (
            "Skills selected by the user for this turn: "
            + ", ".join(selected_skills)
            + ". Read each exact skill with read_harness_skill before acting, then apply its instructions. Do not search or list the skill catalog first."
        )
    else:
        skill_instruction = ""
    attachments = []
    for item in metadata.get("turn_attachments") or []:
        attachment = (
            f"- {item.get('name') or Path(str(item.get('path') or '')).name} "
            f"({item.get('mime_type') or 'application/octet-stream'}): {item.get('path')}"
        )
        excerpt = str(item.get("extracted_text") or item.get("transcript") or "").strip()[:1500]
        if excerpt:
            attachment += f"\n  extracted_text: {excerpt}"
        attachments.append(attachment)
    return "\n\n".join(
        part
        for part in (
            "[DECISIONS DEVELOPMENT THREAD]\n"
            f"{mode_instruction}\n"
            "Use the project tools directly, as in a normal coding IDE. "
            "This Development thread is the ticket and the current work item. Work on it directly. "
            "Do not create another ticket, issue, or ticket file unless the user explicitly asks for a separate work item. "
            "Use direct execution for compact work. When deterministic instruction analysis selects a workflow, keep this thread as the owner and run the workflow orchestrator as its sub-agent. "
            "A direct modification request is authorization to implement it. Do not finish by offering to make the requested change or asking which obvious project target to use when repository and runtime evidence identify it. When cosmetic details are omitted, make the smallest conventional implementation and report what you chose.",
            "Completion evidence: run each required test, build, lint or browser check as a separate command. "
            "Preserve failures (including pipelines); do not turn a failing check into success with tail or true. "
            "A tool finishing is not verification. Report failed, missing and skipped checks explicitly. "
            "Only claim deployed when this turn has authoritative deployment evidence. "
            "Stay within this project's scope; another site's code or shared data requires explicit authorization.",
            DEVELOPMENT_RESPONSE_GUIDANCE,
            _runtime_identity_text(metadata),
            agent_instructions(chat_id),
            thread_source_instructions(chat_id),
            skill_instruction,
            "\n".join(item for item in scope if item),
            "Conversation context since the latest context checkpoint:\n" + "\n\n".join(transcript) if transcript else "",
            "Current user instruction:\n" + prompt,
            "Attached files for this turn:\n" + "\n".join(attachments) if attachments else "",
        )
        if part
    )


def _ticket_file_extracted_text(record) -> str:
    """Return stored extracted text for a ticket file from record.json or a sibling transcript."""
    path = Path(str(getattr(record, "file_path", "") or "")).expanduser()
    if not path.is_file():
        return ""
    sidecar = path.with_name(path.name + ".record.json")
    try:
        if sidecar.is_file():
            import json
            payload = json.loads(sidecar.read_text())
            text = str(payload.get("extracted_text") or payload.get("transcript") or "").strip()
            if text:
                return text[:1500]
    except (OSError, ValueError, TypeError):
        pass
    transcript = path.with_name(path.name + ".transcript.txt")
    try:
        if transcript.is_file():
            return transcript.read_text(encoding="utf-8", errors="replace").strip()[:1500]
    except OSError:
        return ""
    return ""


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
                **(
                    {"extracted_text": extracted}
                    if (extracted := _ticket_file_extracted_text(record))
                    else {}
                ),
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
        snapshots = (Path.home() / '.decisions/workspaces/boards').resolve(strict=False)
        is_snapshot = path.is_relative_to(snapshots) and len(path.relative_to(snapshots).parts) > 2 and path.relative_to(snapshots).parts[1] == 'intake'
        if path in seen or not path.is_file() or not (is_snapshot or any(root == path or root in path.parents for root in roots)):
            continue
        seen.add(path)
        attachments.append(
            {
                "name": str(raw.get("name") or path.name)[:255],
                "path": str(path),
                "mime_type": str(raw.get("mime_type") or "application/octet-stream")[:120],
                "size": int(raw.get("size") or path.stat().st_size),
                "trust": str(raw.get("trust") or "")[:80],
                **({"transcript": str(raw["transcript"])} if raw.get("transcript") else {}),
                **({"extracted_text": str(raw["extracted_text"])} if raw.get("extracted_text") else {}),
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


def _queue_head(db, root: Chat):
    from distr.core.workflow.development_control import _ordered_pending_commands

    return _ordered_pending_commands(db, root).first()


def _accept_proactive_followup(db, root, development, followup) -> int:
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.workflow.development_threads import _scope_execution_active

    ticket = db.get(KanbanTicket, followup["ticket_id"])
    lane = db.get(KanbanLane, ticket.lane_id) if ticket else None
    board = db.get(KanbanBoard, lane.board_id) if lane else None
    if ticket is None or board is None:
        raise DevelopmentExecutionBusy("The approved ticket is no longer available. Its follow-up remains queued.")
    project = ticket.linked_project_id if ticket.linked_project_id is not None else board.default_project_id
    linked_project = db.get(Project, project) if project is not None else None
    if (root.is_archived or root.project_id != followup["project_id"] or project != root.project_id
            or linked_project is None or linked_project.deleted_at is not None
            or board.archived or board.id != followup["board_id"]
            or ticket.source_chat_id != root.id or ticket.linked_workflow_id is not None
            or development.get("ticket_id") != ticket.id):
        raise DevelopmentExecutionBusy("The approved ticket changed scope. Its follow-up remains queued.")
    if _scope_execution_active(db, root, development):
        raise DevelopmentExecutionBusy("The thread already has active work. Its follow-up remains queued.")
    previous = db.query(Chat).filter(
        Chat.parent_id == root.id, Chat.input.isnot(None), Chat.input != "",
    ).order_by(Chat.id.desc()).first()
    command = _queue_head(db, root)
    if (previous is None or previous.id != followup["turn_id"] or not previous.response
            or command is None or command.id != followup["command_id"] or command.status != "queued"
            or command.command_type != "followup" or command.workflow_id is not None or command.run_id is not None
            or command.modified_at != followup["modified_at"] or command.created_at > previous.modified_date
            or command.modified_at > previous.modified_date):
        raise DevelopmentExecutionBusy("The follow-up or its preceding turn changed. The follow-up remains queued.")
    _require_saved_followup_scope(command, root)
    command.status = "dispatching"
    command.result_summary = "Starting after the approved work completed."
    return command.id


def _require_saved_followup_scope(command, root) -> None:
    from .development_control import command_scope_status

    status = command_scope_status(command, root)
    if status == "earlier":
        raise DevelopmentExecutionBusy("The queued item belongs to an earlier project or board. It remains queued.")
    if status == "unknown":
        raise DevelopmentExecutionBusy("The queued item's original project could not be verified. Queue it again from the intended thread.")


def _accept_next_followup(db, root, development, followup) -> int:
    from distr.core.workflow.development_threads import _scope_execution_active

    command = _queue_head(db, root)
    if (command is None or command.id != followup["command_id"] or command.status != "queued"
            or command.command_type != "followup" or command.workflow_id is not None or command.run_id is not None
            or command.modified_at.isoformat() != followup["modified_at"]):
        raise DevelopmentExecutionBusy("The next queued item changed. Refresh the queue.")
    project = db.get(Project, root.project_id) if root.project_id is not None else None
    if root.is_archived or (root.project_id is not None and (project is None or project.deleted_at is not None)):
        raise DevelopmentExecutionBusy("This thread is unavailable for execution. The item remains queued.")
    _require_saved_followup_scope(command, root)
    execution = development.get("execution") or {}
    scope_metadata = development
    if execution.get("status") == "waiting" and execution.get("completed_at"):
        scope_metadata = {**development, "execution": {**execution, "status": "completed"}}
    if _scope_execution_active(db, root, scope_metadata):
        raise DevelopmentExecutionBusy("The thread already has active work. The item remains queued.")
    command.status = "dispatching"
    command.result_summary = "Starting in this Development thread."
    return command.id


def _settle_followup_start(db, root, execution, *, started):
    from distr.core.db.workflow import DevelopmentCommand

    claim = execution.get("followup_start")
    if not isinstance(claim, dict):
        return
    command = db.get(DevelopmentCommand, claim.get("command_id")) if claim.get("command_id") else None
    manual_token = claim.get("manual_token")
    if manual_token:
        from .queued_submissions import _manual_start_matches, _release_manual_start, _require_manual_source

        matches = _manual_start_matches(command, root, manual_token)
    else:
        matches = bool(command and command.chat_id == root.id and command.command_type == "followup"
                       and command.status == "dispatching")
    if not matches:
        if started:
            raise DevelopmentExecutionBusy("The follow-up changed before it reached the worker.")
        execution.pop("followup_start", None)
        return
    if started:
        if manual_token:
            metadata = _json(command.metadata_json)
            _require_manual_source(db, command, metadata.pop("manual_start"))
            command.metadata_json = json.dumps(metadata, ensure_ascii=False)
        command.status = "delivered"
        command.delivered_at = utc_now_naive()
        command.result_summary = "Handed to the Development runtime."
    else:
        if manual_token:
            _release_manual_start(command)
        else:
            command.status = "queued"
            command.delivered_at = None
            command.result_summary = "Startup stopped. The follow-up remains queued for review."
            command.metadata_json = json.dumps({**_json(command.metadata_json), "review_after_startup_failure": True})
        turn = db.get(Chat, execution.get("turn_id")) if execution.get("turn_id") else None
        if turn is not None and turn.parent_id == root.id and not turn.response:
            turn.response = execution.get("error") or execution.get("summary") or command.result_summary
            turn.modified_date = utc_now_naive()
    execution.pop("followup_start", None)


def _start_next_queued_instruction(chat_id: int, *, after_proactive: dict | None = None, _session_provider=None) -> None:
    from distr.core.db.workflow import DevelopmentCommand

    with (_session_provider or get_session)() as db:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        root = db.get(Chat, int(chat_id))
        if root is None:
            return
        command = _queue_head(db, root)
        if command is None or command.status != 'queued':
            return
        if after_proactive is not None and (
            command.command_type != "followup" or command.workflow_id is not None or command.run_id is not None
        ):
            return
        command_id = command.id
        content = str(command.content or "").strip()
        if not content:
            return
        command_metadata = _json(command.metadata_json)
        if command_metadata.get("review_after_startup_failure") is True:
            return
        direct_followup = (
            after_proactive is None and command.command_type == "followup"
            and command.workflow_id is None and command.run_id is None
        )
        source = command.source
        revision = command.modified_at.isoformat()
        if after_proactive is not None:
            after_proactive = {**after_proactive, "command_id": command_id, "modified_at": command.modified_at}
        elif not direct_followup:
            if not db.query(DevelopmentCommand).filter_by(id=command.id, status='queued').update(
                {'status': 'dispatching'}, synchronize_session=False,
            ):
                return
            db.commit()
    if direct_followup:
        from .queued_submissions import start_next_followup

        try:
            start_next_followup(chat_id, command_id, revision)
        except (ValueError, LookupError):
            logger.info("Follow-up %s remains queued for thread %s", command_id, chat_id)
        else:
            if source != "app_view":
                from distr.core.workflow.development_control import resume_thread_time

                resume_thread_time(chat_id)
        return
    skill_ids = command_metadata.get("skill_ids")
    composer = command_metadata.get("composer")
    options = {
        "attachments": command_metadata.get("attachments") or [],
        "skill_ids": skill_ids if isinstance(skill_ids, list) else [],
        "use_playwright": isinstance(composer, dict) and bool(composer.get("use_playwright")),
        "dispatch_async": True,
    }
    from distr.core.workflow.development_control import resume_thread_time
    if after_proactive is not None:
        from distr.core.workflow.queued_submissions import submission_metadata

        options["attachments"] = submission_metadata(chat_id, command_metadata)["attachments"]
        dispatch_development_prompt(int(chat_id), content, save_prompt=True, queued_followup=after_proactive, **options)
        resume_thread_time(int(chat_id))
        return
    try:
        from distr.core.workflow.queued_submissions import record_submission_message
        record_submission_message(int(chat_id), command_id, content)
        resume_thread_time(int(chat_id))
        dispatch_development_prompt(int(chat_id), content, **options)
    except Exception:
        with get_session() as db:
            row = db.get(DevelopmentCommand, command_id)
            row.status = 'queued'
            db.commit()
        raise
    with get_session() as db:
        row = db.get(DevelopmentCommand, command_id)
        row.status = 'delivered'
        row.delivered_at = utc_now_naive()
        row.result_summary = 'Started after the previous agent turn completed.'
        db.commit()


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
    selected_route: tuple[str, str, dict[str, Any], str],
    attachments: list[dict[str, Any]],
    skill_ids: list[str],
    skill_recommendation: str,
    use_playwright: bool,
    autonomy_level: str,
    tool_event_id: str | None,
    recovery: dict[str, Any] | None = None,
) -> None:
    from distr.core.chat_turns import complete_turn, create_event, finish_tool, start_tool, terminal_turn, update_event
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

    backend, model, adapter_options, complexity = selected_route
    if assessment.get("bounded_automation") == "email_action_items":
        from distr.core.automation.execution_policy import email_triage_options

        adapter_options.update(email_triage_options())
    runtime_metadata = _metadata_with_runtime_identity(
        metadata,
        backend=backend,
        model=model,
        adapter_options=adapter_options,
    )
    runtime_metadata["turn_attachments"] = list(attachments)
    runtime_metadata["turn_skill_ids"] = list(skill_ids)
    runtime_metadata["skill_recommendation"] = skill_recommendation
    if autonomy_level == "plan":
        adapter_options["read_only_expected"] = True
    native_tool_events: dict[str, str] = {}
    worker_identity: dict[str, str] = {}
    from distr.core.turn_runtime.verification import VerificationReceipts
    verification_receipts = VerificationReceipts(prompt)
    # 2026-11-12 agent-conversational: mid-task updates lead with a monotonic percent.
    mid_task_progress: dict[str, int] = {}
    audit_only = _audit_only_request(prompt) or development_execution_state(chat_id).get('work_mode') == 'audit'
    metadata = {**metadata, 'work_mode': 'audit' if audit_only else 'implementation'}
    runtime_metadata['work_mode'] = 'audit' if audit_only else 'implementation'
    expected_scope = development_scope_identity(project.id, metadata, chat_id=chat_id)
    runtime_started = False
    scope_changed = False

    def on_event(event: TurnEvent) -> None:
        nonlocal scope_changed
        if scope_changed:
            return
        summary = str(event.summary or "Working")[:1000]
        try:
            state = _update_execution(
                chat_id, job_id=job_id, runtime_scope=expected_scope, status="running",
                runtime_id=event.runtime_id, execution_session_id=event.execution_session_id,
                output_delta=str(event.output_delta or ""), activity=summary, activity_status=event.status.value,
            )
        except DevelopmentScopeChanged:
            scope_changed = True
            return
        if state.get("job_id") != job_id or state.get("status") != "running":
            return
        verification_receipts.observe(event)
        call_id = str(event.details.get("call_id") or "")
        merged_worker = (
            bool(tool_event_id)
            and bool(event.details.get("worker_lifecycle"))
            and event.tool_name in {"development_agent", "cursor_agent"}
        )
        if merged_worker:
            # The harness worker start/end duplicates the outer development-agent
            # row; fold its identity into that row instead of a second line.
            worker_identity.update(
                {
                    "worker_name": str(event.details.get("agent_name") or ""),
                    "harness": str(event.details.get("harness") or backend),
                    "model": str(event.details.get("model") or model or ""),
                }
            )
        if merged_worker:
            pass
        elif event.kind.value == "tool_started" and event.tool_name:
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
                detail=str(event.details.get("output") or ""),
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
                    detail=str(event.details.get("output") or ""),
                    metadata=event.details,
                )
        elif event.kind.value == "progress":
            from distr.core.conversational_progress import conversational_mid_task

            # Stage 1 of 4 (dispatched) is done while the agent works: floor 25%.
            progress_text, progress_pct = conversational_mid_task(
                summary, floor_percent=25, previous_percent=mid_task_progress.get("pct"),
            )
            mid_task_progress["pct"] = progress_pct
            create_event(
                int(chat_id), "tool_completed", turn_id=turn_id, status="completed",
                title="Progress update", summary=progress_text,
                metadata={"source": "native_development", "tool_name": "model_progress",
                          **event.details, "progress_percent": progress_pct},
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

    def hold_result(message: str) -> None:
        state = _update_execution(
            chat_id, job_id=job_id, scope_lost=True, status="failed",
            result_held=runtime_started, streamed_output="", turn_response=message,
            completed_at=utc_now_naive().isoformat(), error=message,
            summary="Result held for scope review." if runtime_started else message,
            activity_status="failed",
        )
        if state.get("job_id") != job_id or state.get("status") != "failed" or state.get("error") != message:
            return
        if state.get("turn_id") is None:
            _write_response(chat_id, None, message)
        if tool_event_id:
            finish_tool(tool_event_id, success=False, summary=message, detail=message)
        terminal_turn(chat_id, "turn_failed", turn_id=turn_id, summary=message)

    try:
        if not _direct_job_can_start(chat_id, job_id, expected_scope, board_id):
            return
        if autonomy_level != 'plan':
            from distr.core.planning.prerequisites import require_plan_prerequisites
            require_plan_prerequisites(ticket_id, chat_id=chat_id, project_id=project.id, board_id=board_id)
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
                **(recovery or {}),
            },
        )
        from distr.core.turn_runtime.anthropic_adapter import is_fable_52

        fable_messages_route = (
            backend == "pi" and is_fable_52(model) and request.route.provider in {"anthropic", "openrouter"}
        )
        runtime_id = select_turn_runtime_id(
            request,
            runtime_id="cli_harness" if backend in {"pi", "cursor", "codex", "claude_code"} and not fable_messages_route else "",
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
        runtime_metadata["skill_recommendation"] = skill_recommendation
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
        startup = _update_execution(
            chat_id,
            job_id=job_id,
            startup_scope=expected_scope,
            status="initializing",
            backend_id=backend,
            model=model,
            project_id=int(project.id),
            board_id=board_id,
            turn_id=turn_id,
            runtime_id=runtime_id,
            route_receipt=_route_receipt(
                metadata,
                assessment,
                backend=backend,
                model=model,
                adapter_options=adapter_options,
                complexity=complexity,
                actual={"runtime": runtime_id},
            ),
        )
        if startup.get("job_id") != job_id or startup.get("status") != "initializing":
            return
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
                state = _update_execution(
                    chat_id,
                    job_id=job_id,
                    startup_scope=expected_scope,
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
                if state.get("job_id") != job_id or state.get("status") != "failed" or state.get("error") != message:
                    return
                _write_response(chat_id, turn_id, message)
                if tool_event_id:
                    finish_tool(tool_event_id, success=False, summary=message[:1000], detail=message)
                terminal_turn(chat_id, "turn_failed", turn_id=turn_id, summary=message)
                return
        if runtime_id == "cli_harness" and backend in CLI_BACKENDS:
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
                state = _update_execution(
                    chat_id,
                    job_id=job_id,
                    startup_scope=expected_scope,
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
                if state.get("job_id") != job_id or state.get("status") != "failed" or state.get("error") != message:
                    return
                _write_response(chat_id, turn_id, message)
                if tool_event_id:
                    finish_tool(tool_event_id, success=False, summary=message[:1000], detail=message)
                terminal_turn(chat_id, "turn_failed", turn_id=turn_id, summary=message)
                return
        if startup.get("followup_start"):
            handed_off = _update_execution(chat_id, job_id=job_id, startup_scope=expected_scope, start_followup=True)
            if handed_off.get("job_id") != job_id or handed_off.get("status") != "initializing":
                return
        elif not _direct_job_can_start(chat_id, job_id, expected_scope, board_id):
            return
        runtime_started = True
        result = await execute_turn(
            request,
            on_event=on_event,
            runtime_id=runtime_id,
        )
        if scope_changed:
            raise DevelopmentScopeChanged(_DIRECT_RESULT_HELD)
        current = development_execution_state(chat_id)
        if current.get("job_id") != job_id or current.get("status") == "cancelled":
            return
        success = bool(result.success)
        blocked = str(getattr(result, "status", "")) == "blocked"
        waiting = bool(result.waits_for_human and (success or blocked))
        output = str(result.output or "").strip()
        error = str(result.error or "").strip()
        response = output or error or ("Work completed." if success else "The development agent did not return a result.")
        status = "blocked" if blocked else "waiting" if waiting else "completed" if success else "failed"
        changes = _turn_change_manifest(
            str(project.folder_location or ""),
            dict(current.get("git_status_before") or {}),
            before_untracked={
                str(path): str(digest)
                for path, digest in dict(current.get("git_untracked_before") or {}).items()
                if path and digest
            },
            before_fingerprints=dict(current.get("git_fingerprints_before") or {}),
            before_head=str(current.get('git_head_before') or ''),
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
        verification = verification_receipts.report()
        if success and not waiting and change_expected and verification['status'] != 'passed':
            status = 'blocked'
            success = False
            error = 'Required verification has not passed. Inspect the recorded checks and rerun them.'
            response = 'Verification incomplete: ' + error + '\n\nAgent report (not independently verified):\n' + response
        if audit_only and git_change_count:
            status = 'failed'
            success = False
            error = 'Audit-only scope was violated: project files changed. Review the changes before proceeding.'
            response = error + '\n\nAgent report:\n' + response
        # 2026-11-12 agent-conversational: Paul sees "<pct>% done." then conversational
        # prose, never the raw harness banner. The technical error stays in `error`.
        from distr.core.conversational_progress import conversational_development_reply

        response = conversational_development_reply(
            response,
            status=status,
            success=success,
            change_expected=change_expected,
            changed_files=git_change_count,
            verification=verification,
            audit_violation=bool(audit_only and git_change_count),
        )
        outcome = _turn_outcome(
            backend_id=str(result.backend_id or backend),
            runtime_id=str(result.runtime_id or runtime_id),
            success=success,
            result=result,
            changes=changes,
            summary=response,
            noop=noop,
        )
        outcome.update(verification=verification, work_mode='audit' if audit_only else 'implementation',
                       implementation_status='changed' if git_change_count else 'unchanged',
                       deployment_status='not_verified')
        actual_provider = str(adapter_options.get("model_provider") or "").strip().lower()
        if (
            str(result.backend_id or "").strip().lower() == str(adapter_options.get("fallback_backend") or "").strip().lower()
            and str(result.model or "").strip() == str(adapter_options.get("fallback_model") or "").strip()
        ):
            actual_provider = str(adapter_options.get("fallback_model_provider") or actual_provider).strip().lower()
        actual_backend = str(result.backend_id or backend).strip().lower()
        state = _update_execution(
            chat_id,
            job_id=job_id,
            runtime_scope=expected_scope,
            turn_response=response,
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
            route_receipt=_route_receipt(
                metadata,
                assessment,
                backend=backend,
                model=model,
                adapter_options=adapter_options,
                complexity=complexity,
                actual={
                    "backend": actual_backend,
                    "provider": actual_provider,
                    "model": str(result.model or model),
                    "runtime": str(result.runtime_id or runtime_id),
                    **(
                        {
                            "reasoning_effort": str(adapter_options.get("reasoning_effort") or "medium"),
                            "service_tier": str(adapter_options.get("service_tier") or "standard"),
                        }
                        if actual_backend == "codex"
                        else {}
                    ),
                },
            ),
        )
        if state.get("job_id") != job_id or state.get("status") != status:
            return
        if state.get("turn_id") is None:
            _write_response(chat_id, None, response)
        if not waiting and result.execution_session_id:
            from distr.core.kanban.project_execution import complete_execution_session
            complete_execution_session(result.execution_session_id, success=success, status=status,
                error=error, output_packet={'completion_review': outcome}, merge_output_packet=True)
        if tool_event_id:
            finish_tool(
                tool_event_id,
                success=success,
                summary=response[:1000],
                detail=error,
                metadata={
                    "agent_name": "Development agent",
                    "worker_name": worker_identity.get("worker_name", ""),
                    "harness": actual_backend or worker_identity.get("harness", ""),
                    "model": str(result.model or worker_identity.get("model") or model or ""),
                },
            )
        if success:
            complete_turn(chat_id, turn_id=turn_id, display_text=response)
        else:
            terminal_turn(chat_id, "turn_failed", turn_id=turn_id, summary=response.split("\n\n", 1)[0] or error)
        if (
            bool((adapter_options or {}).get("unload_when_done"))
            and str((adapter_options or {}).get("model_provider") or "").lower() == "ollama"
        ):
            _unload_ollama_model(model)
        if not waiting and success and not audit_only:
            _start_next_queued_instruction(chat_id)
    except DevelopmentScopeChanged as exc:
        hold_result(str(exc))
    except Exception as exc:
        if scope_changed:
            hold_result(_DIRECT_RESULT_HELD)
            return
        cancelled = isinstance(exc, TurnCancelled)
        status = "cancelled" if cancelled else "failed"
        message = "Stopped by the user." if cancelled else str(exc) or "The development agent failed to start."
        details = {} if cancelled else {
            "error": message,
            "turn_response": message,
            "outcome": {
                "backend_id": backend,
                "runtime_id": "",
                "success": False,
                "tool_counts": {},
                "git_change_count": 0,
                "summary": message[:2000],
            },
        }
        try:
            state = _update_execution(
                chat_id, job_id=job_id, startup_scope=expected_scope if not runtime_started else None,
                runtime_scope=expected_scope if runtime_started else None,
                status=status, completed_at=utc_now_naive().isoformat(), summary=message,
                activity_status=status, **details,
            )
        except DevelopmentScopeChanged as scope_error:
            hold_result(str(scope_error))
            return
        if state.get("job_id") != job_id or state.get("status") != status:
            return
        if cancelled:
            if tool_event_id:
                update_event(tool_event_id, status="cancelled", summary=message)
            if turn_id is not None:
                terminal_turn(chat_id, "turn_cancelled", turn_id=turn_id, summary=message)
            return
        if state.get("turn_id") is None:
            _write_response(chat_id, None, message)
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
    save_prompt: bool = False,
    queued_followup: dict | None = None,
    telegram_proposal_approval: tuple[str, int] | None = None,
) -> dict[str, Any]:
    clean = str(prompt or "").strip()
    if not clean:
        raise ValueError("A development instruction is required.")
    project, metadata, ticket_id, board_id = _project_and_scope(int(chat_id))
    from distr.core.incoming.media import required_thread_attachments
    from distr.core.incoming.proposals import restore_reviewed_thread_merge_attachments

    restore_reviewed_thread_merge_attachments(int(chat_id), int(project.id))
    source_attachments = required_thread_attachments(int(chat_id))
    if metadata.get("source_type") == "plan_workspace":
        raise ValueError("Use the Plan conversation for planning edits. Plan threads cannot dispatch application code changes.")
    with get_session() as db:
        root = db.get(Chat, int(chat_id))
        raw_autonomy = autonomy_override or root.autonomy_level or "full"
    from distr.core.workflow.development_threads import normalize_autonomy_level

    autonomy_level = normalize_autonomy_level(raw_autonomy)
    if autonomy_level is None:
        raise ValueError(
            f"Unsupported Development autonomy level {str(raw_autonomy)!r}; "
            "expected full, goal, plan or approval."
        )
    if autonomy_level == "goal":
        # Goal threads (Tensology approvals, composer "Goal") dispatch exactly
        # like full autonomy; goal tracking lives in development_control.
        autonomy_level = "full"
    previous = development_execution_state(chat_id)
    previous_mode = previous.get('work_mode')
    recovery = None
    original_request = clean
    if previous.get("interrupted") and clean.lower().rstrip(".!") in {"resume", "continue", "resume interrupted work"}:
        from distr.core.turn_runtime.contracts import TurnScope
        from distr.core.turn_runtime.recovery import RecoveryJournal

        previous_turn_id = _execution_record_id(previous.get("turn_id"))
        previous_session_id = _execution_record_id(previous.get("execution_session_id"))
        if not previous_turn_id or not previous_session_id or not previous.get("job_id"):
            raise ValueError("The interrupted turn has no durable recovery checkpoint.")
        with get_session() as db:
            original_turn_id = _execution_record_id(previous.get("recovery_original_turn_id")) or previous_turn_id
            previous_turn = db.get(Chat, original_turn_id)
            if previous_turn is None or previous_turn.parent_id != chat_id or not previous_turn.input:
                raise ValueError("The original request is unavailable in this Development thread.")
            clean = previous_turn.input
        RecoveryJournal.load(
            previous_session_id,
            scope=TurnScope(
                chat_id=int(chat_id), project_id=int(project.id), board_id=board_id,
                ticket_id=ticket_id, project_folder=str(project.folder_location or ""),
            ),
            turn_id=previous_turn_id,
        )
        recovery = {
            "resume_session_id": previous_session_id,
            "resume_turn_id": previous_turn_id,
            "untrusted_external_input": bool(previous.get("recovery_untrusted_input")),
        }
        if previous.get("autonomy_level") == "plan":
            autonomy_level = "plan"
        attachments = [*(attachments or []), *(previous.get("recovery_attachments") or [])]
        use_playwright = use_playwright or bool(previous.get("recovery_use_playwright"))
    audit_only = _audit_only_request(clean) or (
        previous_mode == 'audit' and clean.strip().lower().rstrip('.!') in {'continue', 'carry on', 'go on', 'yes', 'ok'}
    )
    if audit_only:
        autonomy_level = 'plan'
    if autonomy_level != 'plan':
        from distr.core.planning.prerequisites import require_plan_prerequisites
        require_plan_prerequisites(ticket_id, chat_id=chat_id, project_id=project.id, board_id=board_id)
    from distr.core.chat_turns import latest_active_turn_id, start_tool

    turn_id = None if save_prompt else latest_active_turn_id(int(chat_id))
    job_id = uuid.uuid4().hex
    assessment = dict(routing_assessment or {})
    if recovery:
        recovery["untrusted_external_input"] |= assessment.get("operational_state") == "external_untrusted"
    if not save_prompt:
        control_result = _handle_owned_automation_control(int(chat_id), clean, metadata, assessment=assessment)
        if control_result is not None:
            return control_result
    from distr.core.workflow.execution_mode import assess_development_request, choose_development_execution_mode

    persistent_attachments = _linked_ticket_attachments(ticket_id)
    persistent_attachments.extend(source_attachments)
    turn_attachments = _safe_turn_attachments(
        [*(attachments or []), *persistent_attachments],
        project_folder=str(project.folder_location or ""),
    )
    if not assessment and not recovery:
        assessment = assess_development_request(
            clean, has_images=any(str(item.get("mime_type") or "").startswith("image/") for item in turn_attachments),
        )

    # Workflows only run when the caller already chose a named workflow_id
    # (threads/studio → dispatch_work_item). Auto-wrap into managed class
    # workflows is retired: every prompt/ticket continues as a direct turn.
    execution_mode = choose_development_execution_mode(clean, assessment=assessment)
    if execution_mode.get("policy") == "proposal-scope-v1":
        assessment.pop("proposal_scope", None)
        assessment["complexity"] = execution_mode["complexity"]
    assessment["execution_mode_decision"] = execution_mode

    # Skills are auto-matched from the original prompt at submit; any failure means "no skill", never a blocked turn.
    explicit_skill_ids: list[str] = []
    turn_skill_ids: list[str] = []
    skill_recommendation = ""
    try:
        from distr.core.skills.catalog import filter_known_skill_ids, infer_skills_for_ticket

        explicit_skill_ids = filter_known_skill_ids(list(skill_ids or []))[:12]
        turn_skill_ids = list(explicit_skill_ids)
        if not turn_skill_ids:
            # Fill gaps: route skills inferred from the ticket/prompt into CLI harness context.
            inferred = infer_skills_for_ticket(
                "\n".join(part for part in (clean, str((metadata or {}).get("ticket_title") or "")) if part),
                limit=5,
            )
            turn_skill_ids = filter_known_skill_ids(list(inferred or []))[:12]
        from distr.core.capabilities_pack import recommend_design_skills

        recommended, skill_recommendation = recommend_design_skills(
            "\n".join(part for part in (clean, str((metadata or {}).get("ticket_title") or "")) if part)
        )
        for skill_id in filter_known_skill_ids(recommended):
            if skill_id not in turn_skill_ids and len(turn_skill_ids) < 12:
                turn_skill_ids.append(skill_id)
    except Exception:
        logger.debug("Skill auto-match failed for chat %s; continuing without skills", chat_id, exc_info=True)
        turn_skill_ids = list(explicit_skill_ids)
        skill_recommendation = ""
    selected_route = _route(project, metadata, assessment, board_id=board_id)
    backend, model, adapter_options, complexity = selected_route
    folder = str(project.folder_location or "")
    git_status_before = previous.get("git_status_before") if recovery else _git_status_snapshot(folder)
    git_untracked_before = previous.get("git_untracked_before") if recovery else _git_untracked_fingerprints(folder, git_status_before)
    execution = _update_execution(
        int(chat_id),
        job_id=job_id,
        expected_scope=development_scope_identity(project.id,metadata,chat_id=int(chat_id)),
        resume_job_id=str(previous["job_id"]) if recovery else None,
        new_prompt=original_request if save_prompt else None,
        new_prompt_metadata={"skill_ids": explicit_skill_ids} if save_prompt and explicit_skill_ids else None,
        expected_autonomy=str(raw_autonomy).strip().lower() if save_prompt else None,
        queued_followup=queued_followup,
        telegram_proposal_approval=telegram_proposal_approval,
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
        execution_mode_decision=execution_mode,
        runtime_id="cli_harness",
        git_status_before=git_status_before,
        git_untracked_before=git_untracked_before,
        git_fingerprints_before=previous.get("git_fingerprints_before") if recovery else _git_dirty_fingerprints(folder, git_status_before),
        git_head_before=previous.get("git_head_before") if recovery else _git_head(folder),
        autonomy_level=autonomy_level,
        recovery_attachments=turn_attachments,
        recovery_use_playwright=bool(use_playwright),
        recovery_untrusted_input=(
            recovery["untrusted_external_input"] if recovery else assessment.get("operational_state") == "external_untrusted"
        ),
        recovery_original_turn_id=original_turn_id if recovery else None,
        work_mode='audit' if audit_only else 'implementation',
        route_receipt={
            **_route_receipt(
                metadata,
                assessment,
                backend=backend,
                model=model,
                adapter_options=adapter_options,
                complexity=complexity,
            ),
            "turn_skill_ids": list(turn_skill_ids),
            "turn_skills_auto": not explicit_skill_ids,
        },
    )
    try:
        if save_prompt:
            turn_id = execution["turn_id"]
            record_chat_audit_event(chat_id=int(chat_id), chat_row_id=turn_id, role="user", content=original_request)
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
            "selected_route": selected_route,
            "attachments": turn_attachments,
            "skill_ids": turn_skill_ids,
            "skill_recommendation": skill_recommendation,
            "use_playwright": bool(use_playwright),
            "autonomy_level": autonomy_level,
            "tool_event_id": tool_event_id,
        }
        if recovery:
            kwargs["recovery"] = recovery
        if dispatch_async:
            threading.Thread(
                target=lambda: asyncio.run(_run_execution(**kwargs)),
                name=f"development-agent-{chat_id}",
                daemon=True,
            ).start()
    except Exception as exc:
        if not execution.get("followup_start"):
            raise
        message = "Could not start the Development worker."
        _update_execution(chat_id, job_id=job_id, scope_lost=True, status="failed",
                          completed_at=utc_now_naive().isoformat(), summary=message,
                          error=message, activity_status="failed")
        raise DevelopmentExecutionBusy(message) from exc
    if not dispatch_async:
        asyncio.run(_run_execution(**kwargs))
        execution = development_execution_state(int(chat_id))
    return {**execution, "direct": True, "id": execution.get("execution_session_id") or job_id}


def _record_proactive_stop(chat_id: int, expected_session_id: int, turn_id: int) -> bool:
    from distr.core.orchestrator_proactive import proactive_development_execution

    with get_session() as db:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        root = db.get(Chat, chat_id)
        if root is None or root.parent_id is not None:
            return False
        metadata = development_thread_metadata(root)
        scope = _development_execution_scope(db, root, metadata)
        state = proactive_development_execution(db, root, scope, metadata.get("proactive_dispatch"))
        if (not state or not state.get("can_stop") or state.get("turn_id") != turn_id
                or state.get("execution_session_id") != expected_session_id):
            return False
        params = _chat_params(root.params)
        params["development"]["proactive_stop_turn_id"] = turn_id
        root.params = json.dumps(params, ensure_ascii=False, default=str)
        db.commit()
        return True


def exact_development_stop_target(state: dict[str, Any]) -> dict[str, int] | None:
    active = ACTIVE_EXECUTION_STATUSES | {"paused"} if state.get("direct") is False else ACTIVE_EXECUTION_STATUSES
    if (str(state.get("status") or "").lower() not in active or state.get("completed_at")
            or state.get("stop_pending") is True or state.get("can_stop") is False):
        return None
    if state.get("direct") is False:
        field, value = "workflow_run_id", state.get("id")
    elif state.get("origin") == "proactive_orchestrator" and state.get("can_stop"):
        field, value = "execution_session_id", state.get("execution_session_id")
    elif state.get("runtime_id") == "cli_harness" and state.get("cli_stop_available") is True:
        field, value = "execution_session_id", state.get("execution_session_id")
    elif state.get("runtime_id") == "native":
        field, value = "turn_id", state.get("turn_id")
    else:
        return None
    return {field: value} if type(value) is int and value > 0 else None


def stop_development_execution(chat_id: int, *, expected_session_id: int | None = None,
                               expected_workflow_run_id: int | None = None,
                               expected_turn_id: int | None = None) -> dict[str, Any]:
    state = development_execution_state(int(chat_id))
    if expected_workflow_run_id is not None:
        from distr.core.workflow.dispatcher import cancel_run

        stopped = bool(
            exact_development_stop_target(state) == {"workflow_run_id": expected_workflow_run_id}
            and cancel_run(expected_workflow_run_id, workflow_id=state["workflow_id"], expected_chat_id=chat_id)
        )
        return {"stopped": stopped, "stop_requested": stopped}
    if expected_turn_id is not None:
        from distr.core.turn_runtime import cancel_active_turn

        requested = bool(
            exact_development_stop_target(state) == {"turn_id": expected_turn_id}
            and cancel_active_turn(chat_id, expected_turn_id=expected_turn_id)
        )
        return {"stopped": False, "stop_requested": requested}
    if state.get("origin") == "proactive_orchestrator" or expected_session_id is not None:
        from distr.core.project_cli_backends.registry import terminate_backend_process
        from distr.core.turn_runtime import cancel_active_turn

        requested = False
        if (state.get("runtime_id") == "cli_harness" and state.get("cli_stop_available") is True
                and exact_development_stop_target(state) == {"execution_session_id": expected_session_id}):
            requested = cancel_active_turn(chat_id, expected_turn_id=state["turn_id"])
            if requested:
                terminate_backend_process(
                    state["project_id"], state["backend_id"], board_id=state["board_id"],
                    execution_id=expected_session_id, execution_kind="development_session",
                )
            return {"stop_requested": requested, "stopped": False}
        if (state.get("origin") == "proactive_orchestrator" and state.get("can_stop")
                and expected_session_id == state.get("execution_session_id")
                and _record_proactive_stop(int(chat_id), expected_session_id, state["turn_id"])):
            requested = terminate_backend_process(
                state["project_id"], state["backend_id"], board_id=state["board_id"],
                execution_id=expected_session_id, execution_kind="proactive",
            )
        return {**state, "stop_requested": requested, "stopped": False}
    cancelled_workflow = False
    try:
        from distr.core.db.workflow import AutoWorkflowRun
        from distr.core.workflow.dispatcher import cancel_run

        with get_session() as db:
            root = db.get(Chat, int(chat_id))
            scope = _development_execution_scope(db, root, development_thread_metadata(root)) if root else None
            active_runs = (
                _scoped_development_runs(db, int(chat_id), scope)
                .filter(AutoWorkflowRun.status.in_(["running", "waiting"]))
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
    scoped_cli = state.get("cli_stop_available") is True
    process_stop = terminate_backend_process(
        int(state.get("project_id") or -int(chat_id)),
        str(state.get("backend_id") or "pi"),
        board_id=int(state["board_id"]) if state.get("board_id") is not None else None,
        execution_id=state["execution_session_id"] if scoped_cli else int(chat_id),
        execution_kind="development_session" if scoped_cli else "development",
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
    if not session_id:
        updated.pop("execution_session_id", None)
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
