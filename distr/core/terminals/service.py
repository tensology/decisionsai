"""Process inspection and control shared by project terminal transports."""
from __future__ import annotations
from typing import Any
import os
import re
import logging
logger = logging.getLogger(__name__)
_PROJECT_SERVER_COMMAND = re.compile(
    r"(?:^|\s)(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:dev|start|serve)(?:\s|$)|"
    r"(?:^|/)(?:vite|next|nuxt|webpack-dev-server)(?:\s|$)|"
    r"python(?:\d+(?:\.\d+)?)?\s+-m\s+http\.server(?:\s|$)|"
    r"(?:uvicorn|gunicorn|flask\s+run|manage\.py\s+runserver)(?:\s|$)",
    re.IGNORECASE,
)

def _backend_id_for_project(project) -> str:
    from distr.core.project_cli_backends import get_project_backend_id

    return get_project_backend_id(project)


def _coerce_optional_int(value: Any) -> int | None:
    try:
        if value in (None, "", False):
            return None
        return int(value)
    except Exception:
        return None


def _path_is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)
    except (OSError, ValueError):
        return False


def _process_memory_bytes(pid: int | None) -> int:
    """Return resident memory for a process tree without failing the API."""
    if not pid:
        return 0
    try:
        import psutil

        process = psutil.Process(int(pid))
        processes = [process, *process.children(recursive=True)]
        total = 0
        for item in processes:
            try:
                total += int(item.memory_info().rss)
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                continue
        return total
    except (ImportError, ValueError, OSError):
        return 0
    except Exception:
        return 0


def _attach_process_usage(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row, memory_bytes=_process_memory_bytes(row.get("pid"))) for row in rows]


def _managed_process_tree_pids(root_pids: set[int]) -> set[int]:
    """Return managed terminal PIDs and their descendants for discovery exclusion."""
    roots = {int(pid) for pid in root_pids if pid}
    if not roots:
        return set()
    try:
        import psutil
    except ImportError:
        return roots
    excluded = set(roots)
    for pid in roots:
        try:
            excluded.update(int(child.pid) for child in psutil.Process(pid).children(recursive=True))
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            continue
    return excluded


def _discover_project_server_processes(folder: str, *, excluded_pids: set[int] | None = None) -> list[dict[str, Any]]:
    """Find detached development servers still running inside one project folder."""
    if not folder or not os.path.isdir(folder):
        return []
    try:
        import psutil
    except ImportError:
        return []
    excluded = {int(pid) for pid in (excluded_pids or set()) if pid}
    candidates: dict[int, dict[str, Any]] = {}
    for process in psutil.process_iter(["pid", "ppid", "cmdline"]):
        try:
            pid = int(process.info["pid"])
            command = " ".join(process.info.get("cmdline") or []).strip()
            cwd = process.cwd()
            if pid in excluded or not command or not _PROJECT_SERVER_COMMAND.search(command):
                continue
            if not _path_is_within(cwd, folder):
                continue
            candidates[pid] = {
                "process_id": f"discovered:{pid}",
                "pid": pid,
                "ppid": int(process.info.get("ppid") or 0),
                "command": command,
                "cwd": cwd,
                "purpose": "project_server",
                "managed": False,
                "memory_bytes": _process_memory_bytes(pid),
            }
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            continue
    return [row for row in candidates.values() if row["ppid"] not in candidates]


def _stop_discovered_project_process(pid: int, folder: str) -> bool:
    """Stop one discovered server only after revalidating its project working directory."""
    try:
        import psutil
    except ImportError:
        return False

    try:
        process = psutil.Process(int(pid))
        command = " ".join(process.cmdline()).strip()
        if not _path_is_within(process.cwd(), folder) or not _PROJECT_SERVER_COMMAND.search(command):
            return False
        targets = process.children(recursive=True) + [process]
        for target in reversed(targets):
            try:
                target.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(targets, timeout=2)
        for target in alive:
            try:
                target.kill()
            except psutil.NoSuchProcess:
                pass
        return True
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError, ValueError):
        return False


def _resolve_terminal_overview_llm(settings: dict) -> tuple:
    """Provider/model for CLI Read Overview: match Projects CLI (coding_llm_*), then conversational LLM.

    Terminal overview previously read agent_model_name/default_model_name, which are often empty or
    OpenAI-style names while the app uses conversational_llm_* / coding_llm_* — causing Ollama to
    receive e.g. gpt-4o-mini and return 404.
    """
    from distr.core.llm_factory import normalize_provider, resolve_settings_keys

    cm = (settings.get("coding_llm_model") or "").strip()
    cp = (settings.get("coding_llm_provider") or "").strip()
    if cm:
        p = normalize_provider(cp or "Ollama")
        logger.debug("Terminal overview LLM: using coding_llm (%s / %s)", p, cm)
        return p, cm

    prov, model = resolve_settings_keys(settings)
    prov = normalize_provider(prov)
    model = (model or "").strip()
    if model:
        logger.debug("Terminal overview LLM: using resolve_settings_keys (%s / %s)", prov, model)
        return prov, model

    am = (settings.get("agent_model") or settings.get("agent_model_name") or settings.get("default_model_name") or "").strip()
    ap = (settings.get("agent_provider") or settings.get("default_provider") or "").strip()
    if am:
        p = normalize_provider(ap or "Ollama")
        logger.debug("Terminal overview LLM: using legacy agent model keys (%s / %s)", p, am)
        return p, am

    p = normalize_provider("Ollama")
    logger.debug("Terminal overview LLM: using fallback llama3.2 for Ollama")
    return p, "llama3.2"
