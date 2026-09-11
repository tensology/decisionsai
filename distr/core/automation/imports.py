"""Import scheduled automations from local agent clients without duplicates."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

# Complete database initialization before loading the automation store. The
# migration bootstrap imports the scheduler, which in turn imports the store.
import distr.core.db  # noqa: F401

from distr.core.automation.store import (
    create_automation,
    list_automations,
    normalize_schedule,
    update_automation,
)

logger = logging.getLogger(__name__)

_USER_HOME = Path.home()

SOURCE_DIRECTORIES: dict[str, tuple[Path, ...]] = {
    "codex": (_USER_HOME / ".codex" / "automations",),
    "cursor": (
        _USER_HOME / ".cursor" / "automations",
        _USER_HOME / ".cursor" / "scheduled-tasks",
    ),
    "claude": (
        _USER_HOME / ".claude" / "automations",
        _USER_HOME / ".claude" / "scheduled-tasks",
    ),
}

_DAY_MAP = {"SU": "0", "MO": "1", "TU": "2", "WE": "3", "TH": "4", "FR": "5", "SA": "6"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _import_route_config(routing: dict[str, Any] | None) -> dict[str, Any]:
    """Validate and normalize the operator-selected route for imported tasks."""
    if not isinstance(routing, dict):
        return {}
    provider = _clean(routing.get("model_provider")).lower()
    model = _clean(routing.get("model"))
    complexity = _clean(routing.get("complexity") or "medium").lower()
    reasoning = _clean(routing.get("reasoning_effort") or "medium").lower()
    environment = _clean(routing.get("execution_environment") or "local").lower()
    if complexity not in {"low", "medium", "high"}:
        raise ValueError("Import complexity must be low, medium, or high")
    if reasoning not in {"low", "medium", "high", "xhigh"}:
        raise ValueError("Import reasoning must be low, medium, high, or xhigh")
    if environment not in {"local", "hosted", "auto"}:
        raise ValueError("Import execution environment must be local, hosted, or auto")
    if bool(provider) != bool(model):
        raise ValueError("Choose both a provider and model, or leave both on Automatic")
    backend = ""
    if provider:
        backend = "codex" if provider == "openai" else "claude" if provider == "anthropic" else "pi"
    return {
        "backend": backend,
        "model_provider": provider,
        "model": model,
        "reasoning_effort": reasoning,
        "complexity": complexity,
        "execution_environment": environment,
        "adaptive_model_routing": bool(routing.get("adaptive_model_routing", True)),
        "allow_provider_failover": bool(routing.get("allow_provider_failover", True)),
    }


def _status(value: Any) -> str:
    return "paused" if _clean(value).lower() in {"paused", "disabled", "inactive", "off"} else "active"


def _time(hour: Any, minute: Any = 0) -> str:
    try:
        return f"{max(0, min(23, int(hour))):02d}:{max(0, min(59, int(minute))):02d}"
    except (TypeError, ValueError):
        return "09:00"


from distr.core.automation.schedule_import import schedule_from_rrule, import_schedule as _schedule


def _load_file(path: Path) -> list[dict[str, Any]]:
    try:
        if path.suffix.lower() == ".toml":
            loaded: Any = tomllib.loads(path.read_text(encoding="utf-8"))
        elif path.suffix.lower() == ".jsonl":
            return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        elif path.suffix.lower() == ".json":
            loaded = json.loads(path.read_text(encoding="utf-8"))
        elif path.suffix.lower() in {".yaml", ".yml"}:
            import yaml  # type: ignore

            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            return []
    except Exception:
        logger.warning("Could not read automation import file %s", path, exc_info=True)
        return []
    if isinstance(loaded, list):
        return [item for item in loaded if isinstance(item, dict)]
    if isinstance(loaded, dict) and isinstance(loaded.get("automations"), list):
        return [item for item in loaded["automations"] if isinstance(item, dict)]
    return [loaded] if isinstance(loaded, dict) else []


def _source_files(source: str) -> Iterable[Path]:
    for directory in SOURCE_DIRECTORIES.get(source, ()):
        if not directory.is_dir():
            continue
        for pattern in ("**/automation.toml", "**/*.json", "**/*.jsonl", "**/*.yaml", "**/*.yml"):
            yield from directory.glob(pattern)


def _match_project_id(cwds: list[str]) -> int | None:
    if not cwds:
        return None
    try:
        from distr.core.db import get_session
        from distr.core.db.projects import Project

        candidates = [(Path(cwd).expanduser().resolve(), cwd) for cwd in cwds if _clean(cwd)]
        with get_session() as session:
            projects = session.query(Project).all()
            resolved = set()
            for cwd, _ in candidates:
                matches = []
                for project in projects:
                    if not _clean(project.folder_location):
                        continue
                    folder = Path(project.folder_location).expanduser().resolve()
                    if cwd == folder or folder in cwd.parents:
                        matches.append((len(folder.parts), int(project.id)))
                if not matches:
                    return None
                depth = max(length for length, _ in matches)
                best = {identity for length, identity in matches if length == depth}
                if len(best) != 1:
                    return None
                resolved.update(best)
            return next(iter(resolved)) if len(resolved) == 1 else None
    except Exception:
        return None


def _normalized_record(source: str, data: dict[str, Any], path: Path) -> dict[str, Any] | None:
    external_id = _clean(data.get("id") or data.get("automation_id") or path.parent.name or path.stem)
    name = _clean(data.get("name") or data.get("title") or external_id)
    instruction = _clean(data.get("prompt") or data.get("instruction") or data.get("message"))
    if not external_id or not name or not instruction:
        return None
    target = data.get("target") if isinstance(data.get("target"), dict) else {}
    cwds = [str(item) for item in (data.get("cwds") or data.get("working_directories") or []) if _clean(item)]
    if not cwds and _clean(data.get("cwd")):
        cwds = [_clean(data.get("cwd"))]
    provider = _clean(data.get("provider") or data.get("model_provider"))
    if not provider and source == "codex":
        provider = "openai"
    model = _clean(data.get("model") or data.get("model_name"))
    backend = _clean(data.get("backend") or data.get("execution_backend"))
    if not backend and source == "codex":
        backend = "codex"
    fingerprint_payload = {
        "source": source,
        "id": external_id,
        "name": name,
        "instruction": instruction,
        "schedule": _schedule(data),
        "status": _status(data.get("status")),
        "provider": provider,
        "model": model,
        "reasoning_effort": _clean(data.get("reasoning_effort") or data.get("reasoning")),
        "target": target,
        "cwds": cwds,
    }
    fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    linked_project_id = _match_project_id(cwds)
    if cwds and linked_project_id is None:
        raise ValueError("Working directories do not resolve to one unambiguous containing project.")
    action_config = {
        "run_in_new_thread": True,
        "backend": backend,
        "model_provider": provider,
        "model": model,
        "reasoning_effort": _clean(data.get("reasoning_effort") or data.get("reasoning")) or "medium",
        "fallback_backend": _clean(data.get("fallback_backend")),
        "fallback_model_provider": _clean(data.get("fallback_provider") or data.get("fallback_model_provider")),
        "fallback_model": _clean(data.get("fallback_model")),
        "execution_environment": _clean(data.get("execution_environment")) or "local",
        "target": target,
        "cwds": cwds,
        "linked_project_id": linked_project_id,
        "import_source": source,
        "import_external_id": external_id,
        "import_key": f"{source}:{external_id}",
        "import_fingerprint": fingerprint,
        "import_path": str(path),
    }
    return {
        "external_id": external_id,
        "import_key": action_config["import_key"],
        "fingerprint": fingerprint,
        "name": name,
        "instruction": instruction,
        "status": _status(data.get("status")),
        "schedule": fingerprint_payload["schedule"],
        "action_config": action_config,
    }


def discover_imports(source: str) -> list[dict[str, Any]]:
    normalized_source = _clean(source).lower()
    if normalized_source not in SOURCE_DIRECTORIES:
        raise ValueError(f"Unsupported automation import source: {source}")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(set(_source_files(normalized_source))):
        for data in _load_file(path):
            try:
                record = _normalized_record(normalized_source, data, path)
            except ValueError as exc:
                external_id = _clean(data.get("id") or path.parent.name)
                record = {"import_key": f"{normalized_source}:{external_id}", "external_id": external_id, "name": _clean(data.get("name") or external_id), "error": str(exc)}
            if record and record["import_key"] not in seen:
                seen.add(record["import_key"])
                records.append(record)
    return records


def import_status() -> dict[str, Any]:
    existing_keys = {
        _clean((item.get("action_config") or {}).get("import_key"))
        for item in list_automations()
    }
    sources = []
    for source in SOURCE_DIRECTORIES:
        records = discover_imports(source)
        sources.append({
            "id": source,
            "label": source.title(),
            "available": bool(records),
            "found": len(records),
            "new": sum(1 for record in records if not record.get("error") and record["import_key"] not in existing_keys),
            "errors": [{"name": record["name"], "message": record["error"]} for record in records if record.get("error")],
            "existing": sum(1 for record in records if record["import_key"] in existing_keys),
        })
    return {
        "sources": sources,
        "routing_defaults": {
            "model_provider": "ollama",
            "model": "muse-glimmer:30b-mlx",
            "reasoning_effort": "medium",
            "complexity": "medium",
            "execution_environment": "local",
            "adaptive_model_routing": True,
            "update_existing": True,
        },
    }


def import_automations(
    sources: list[str] | None = None,
    *,
    routing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected = [_clean(source).lower() for source in (sources or list(SOURCE_DIRECTORIES))]
    unsupported = [source for source in selected if source not in SOURCE_DIRECTORIES]
    if unsupported:
        raise ValueError(f"Unsupported automation import source: {', '.join(unsupported)}")
    existing = {
        _clean((item.get("action_config") or {}).get("import_key")): item
        for item in list_automations()
        if _clean((item.get("action_config") or {}).get("import_key"))
    }
    imported: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    route_config = _import_route_config(routing)
    update_existing = bool((routing or {}).get("update_existing", True))
    for source in selected:
        for record in discover_imports(source):
            if record.get("error"):
                errors.append({"source": source, "external_id": record["external_id"], "name": record["name"], "message": record["error"]})
                continue
            if record["import_key"] in existing:
                current = existing[record["import_key"]]
                if route_config and update_existing:
                    current_config = dict(current.get("action_config") or {})
                    updated_automation = update_automation(
                        current["id"],
                        action_config={
                            **current_config,
                            "import_original_backend": current_config.get("import_original_backend") or current_config.get("backend") or record["action_config"].get("backend"),
                            "import_original_model_provider": current_config.get("import_original_model_provider") or current_config.get("model_provider") or record["action_config"].get("model_provider"),
                            "import_original_model": current_config.get("import_original_model") or current_config.get("model") or record["action_config"].get("model"),
                            **route_config,
                            "import_route_configured_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                        },
                    )
                    existing[record["import_key"]] = updated_automation
                    updated.append(updated_automation)
                    continue
                skipped.append({
                    "source": source,
                    "external_id": record["external_id"],
                    "automation_id": existing[record["import_key"]].get("id"),
                    "reason": "already_imported",
                })
                continue
            original_config = dict(record["action_config"])
            created = create_automation(
                name=record["name"],
                automation_type="scheduled_instruction",
                status=record["status"],
                instruction=record["instruction"],
                preset_id="",
                schedule=record["schedule"],
                action_config={
                    **original_config,
                    "import_original_backend": original_config.get("backend"),
                    "import_original_model_provider": original_config.get("model_provider"),
                    "import_original_model": original_config.get("model"),
                    **route_config,
                    "imported_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                },
            )
            existing[record["import_key"]] = created
            imported.append(created)
    return {
        "success": True,
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "imported_count": len(imported),
        "updated_count": len(updated),
        "skipped_count": len(skipped),
        "errors": errors,
        "error_count": len(errors),
    }
