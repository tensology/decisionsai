"""
Execute queued draft payloads after user approval (R11).

``approve_draft_in_queue`` runs ``execute_payload`` then removes the row — shared by
``InitiativeService.approve_draft``, Web Settings approve, and voice approve paths.
Tools enqueue drafts only; disk is not mutated until approval.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from distr.core.initiative.draft_queue import DraftQueue
from distr.core.mcp.config import (
    document_to_dict,
    load_mcp_config,
    parse_config_dict,
    save_mcp_config,
    default_config_path,
)

logger = logging.getLogger(__name__)


def approve_draft_in_queue(queue: DraftQueue, draft_id: str) -> bool:
    """
    Run ``execute_payload`` if present, then remove the draft.

    Used by InitiativeService, Web UI, and voice approve paths so behaviour stays consistent.
    """
    queue.expire_old()
    entry = queue.get_by_id(draft_id)
    if entry is None:
        return False
    payload = entry.execute_payload
    if payload:
        try:
            run_execute_payload(payload)
        except Exception:
            logger.exception(
                "approve_draft_in_queue: execute_payload failed draft_id=%s",
                draft_id,
            )
            return False
    return queue.remove(draft_id)


def bundled_skills_directory() -> Path:
    """``DecisionsAI/skills`` (repo root one level above ``distr``)."""
    return Path(__file__).resolve().parent.parent.parent.parent / "skills"


def skills_registry_path() -> Path:
    """``skills/skills_registry.json`` alongside bundled skill folders."""
    return bundled_skills_directory() / "skills_registry.json"


def run_execute_payload(payload: dict[str, Any]) -> None:
    """Dispatch a persisted execute_payload dict."""
    kind = payload.get("kind")
    if kind == "mcp_install":
        server = payload.get("server")
        if not isinstance(server, dict):
            raise ValueError("mcp_install payload missing server dict")
        merge_mcp_server_into_config(server)
        return
    if kind == "skill_install":
        url = payload.get("repo_url")
        folder = payload.get("folder_name")
        if not isinstance(url, str) or not isinstance(folder, str):
            raise ValueError("skill_install payload missing repo_url or folder_name")
        execute_skill_clone(url.strip(), folder.strip())
        return
    if kind == "initiative_action":
        from distr.core.initiative.action_handlers import execute_initiative_action
        from distr.core.settings import load_settings_from_db

        action = payload.get("action") or {}
        if not isinstance(action, dict):
            raise ValueError("initiative_action payload missing action dict")
        settings = load_settings_from_db()
        execute_initiative_action(
            action_type=str(action.get("action_type") or "none"),
            description=str(action.get("description") or ""),
            payload=action.get("payload") if isinstance(action.get("payload"), dict) else {},
            draft=str(action.get("draft") or ""),
            settings=settings,
        )
        return
    if kind == "hermes_triage_ack":
        from distr.core.hermes import emit_event

        candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
        execution_result = _execute_hermes_triage_candidate(candidate)
        emit_event(
            source="hermes",
            event_type="daily_triage_candidate_approved",
            status="approved",
            summary=str(candidate.get("question") or candidate.get("title") or "Hermes triage candidate approved"),
            payload={"candidate": candidate, "execution_result": execution_result},
        )
        return
    raise ValueError(f"unknown execute_payload kind: {kind!r}")


def _execute_hermes_triage_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Perform the safest concrete action for an approved Hermes triage item."""
    if not isinstance(candidate, dict):
        return {"status": "noop", "reason": "candidate missing"}
    action_type = str(candidate.get("action_type") or "").strip().lower()
    source = str(candidate.get("source") or "").strip().lower()
    proposal = (candidate.get("payload") or {}).get("proposal")
    proposal_payload = proposal.get("payload") if isinstance(proposal, dict) and isinstance(proposal.get("payload"), dict) else {}

    if action_type == "create_ticket" and source == "whatsapp":
        board_id = proposal_payload.get("linked_board_id")
        message_ids = proposal_payload.get("message_ids")
        if board_id and isinstance(message_ids, list) and message_ids:
            return _create_whatsapp_snapshot_ticket(
                board_id=int(board_id),
                message_ids=[int(mid) for mid in message_ids if str(mid).strip().isdigit()],
                candidate=candidate,
            )
        return {
            "status": "needs_input",
            "reason": "WhatsApp candidate has no linked board/message ids yet",
        }

    return {
        "status": "acknowledged",
        "reason": f"{action_type or 'decision'} approval recorded for Hermes/orchestrator follow-up",
    }


def _create_whatsapp_snapshot_ticket(
    *,
    board_id: int,
    message_ids: list[int],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    from distr.core.db import WhatsAppMessage, get_session
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket

    if not message_ids:
        return {"status": "needs_input", "reason": "no message ids"}

    with get_session() as session:
        board = session.query(KanbanBoard).filter(KanbanBoard.id == board_id).first()
        if not board:
            return {"status": "failed", "reason": f"board {board_id} not found"}
        lane = (
            session.query(KanbanLane)
            .filter(KanbanLane.board_id == board.id)
            .order_by(KanbanLane.position.asc(), KanbanLane.id.asc())
            .first()
        )
        if not lane:
            return {"status": "failed", "reason": f"board {board_id} has no lanes"}
        messages = (
            session.query(WhatsAppMessage)
            .filter(WhatsAppMessage.id.in_(message_ids))
            .order_by(WhatsAppMessage.whatsapp_timestamp.asc(), WhatsAppMessage.id.asc())
            .all()
        )
        if not messages:
            return {"status": "failed", "reason": "messages not found"}

        latest = messages[-1]
        contact = (
            getattr(latest, "sender_push_name", None)
            or getattr(latest, "sender_phone", None)
            or getattr(latest, "jid_phone", None)
            or "WhatsApp"
        )
        title = str(candidate.get("title") or "").strip()
        if not title or len(title) > 120:
            title = f"WhatsApp follow-up: {contact}"
        lines = [
            str(candidate.get("question") or candidate.get("title") or "Approved Hermes WhatsApp triage."),
            "",
            "Source messages:",
        ]
        for msg in messages:
            who = "Me" if getattr(msg, "from_me", False) else (
                getattr(msg, "sender_push_name", None)
                or getattr(msg, "sender_phone", None)
                or "Unknown"
            )
            body = (
                getattr(msg, "text", None)
                or getattr(msg, "caption", None)
                or f"[{getattr(msg, 'media_type', None) or 'message'}]"
            )
            lines.append(f"- #{getattr(msg, 'id', '')} {who}: {str(body).strip()[:500]}")

        max_pos = max((int(t.position or 0) for t in lane.tickets), default=-1)
        ticket = KanbanTicket(
            lane_id=lane.id,
            title=title[:250],
            description="\n".join(lines),
            priority="medium",
            complexity="medium",
            position=max_pos + 1,
            whatsapp_message_id=getattr(latest, "id", None),
            whatsapp_message_wa_id=getattr(latest, "message_id", None),
            source_provider="whatsapp",
            source_external_id=getattr(latest, "message_id", None),
            source_thread_id=getattr(latest, "jid", None) or getattr(latest, "jid_phone", None),
            source_contact=contact,
            source_label="WhatsApp",
        )
        session.add(ticket)
        session.flush()
        snapshot_group = f"{ticket.id}_{lane.id}"
        for msg in messages:
            msg.processed = True
            msg.snapshot_group = snapshot_group
        ticket_id = ticket.id
        board_name = board.name or f"Board {board.id}"

    return {
        "status": "created_ticket",
        "ticket_id": ticket_id,
        "board_id": board_id,
        "board_name": board_name,
        "message_count": len(messages),
    }


def validated_mcp_server_for_install(
    server: dict[str, Any], *, config_path: Path | None = None
) -> dict[str, Any]:
    """
    Normalize ``server`` and ensure it merges cleanly with the current on-disk config.

    Raises ``ValueError`` if invalid or duplicate name. Does **not** write.
    """
    norm = _normalize_mcp_server_row(server)
    if norm is None:
        raise ValueError("invalid MCP server definition")

    path = config_path or default_config_path()
    doc = load_mcp_config(path)
    data = document_to_dict(doc)
    servers: list[dict[str, Any]] = list(data.get("servers") or [])
    key = norm["name"].strip().lower()
    for s in servers:
        if str(s.get("name", "")).strip().lower() == key:
            raise ValueError(f"duplicate MCP server name: {norm['name']!r}")

    trial = servers + [norm]
    new_doc = parse_config_dict({"servers": trial})
    if len(new_doc.servers) != len(trial):
        raise ValueError("MCP server failed validation")
    return norm


def merge_mcp_server_into_config(server: dict[str, Any], *, config_path: Path | None = None) -> None:
    """Append one validated MCP server row and save (duplicate names rejected)."""
    norm = validated_mcp_server_for_install(server, config_path=config_path)

    path = config_path or default_config_path()
    doc = load_mcp_config(path)
    data = document_to_dict(doc)
    servers: list[dict[str, Any]] = list(data.get("servers") or [])
    servers.append(norm)
    new_doc = parse_config_dict({"servers": servers})
    if len(new_doc.servers) != len(servers):
        raise ValueError("MCP server failed validation after merge")
    save_mcp_config(new_doc, path)


def validate_skill_install_queue(repo_url: str, folder_name: str = "") -> tuple[str, str]:
    """
    Validate https URL and destination folder name for a queued skill install.

    Does **not** require git, clone, or write. Raises ``ValueError`` if the folder exists.
    """
    url = repo_url.strip()
    if not _is_safe_https_clone_url(url):
        raise ValueError("only https:// clone URLs are allowed (no file:// or ssh)")

    derived = _folder_name_from_git_url(url) if not folder_name.strip() else folder_name.strip()
    safe_folder = _sanitize_skill_folder_name(derived)
    if not safe_folder:
        raise ValueError("invalid folder name for skill install")

    root = bundled_skills_directory()
    dest = root / safe_folder
    if dest.exists():
        raise ValueError(f"skill folder already exists: {safe_folder!r}")

    return url, safe_folder


def execute_skill_clone(repo_url: str, folder_name: str) -> None:
    """Clone ``repo_url`` into ``skills/<folder_name>/``; remove partial dir on failure."""
    url, safe_folder = validate_skill_install_queue(repo_url, folder_name)

    if shutil.which("git") is None:
        raise RuntimeError("git is not available on PATH")

    root = bundled_skills_directory()
    root.mkdir(parents=True, exist_ok=True)
    dest = root / safe_folder

    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.CalledProcessError as e:
        logger.warning("skill_install git clone failed (exit %s)", e.returncode)
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError("git clone failed") from e

    if not (dest / "SKILL.md").is_file() and not (dest / "skill.md").is_file():
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError("cloned repository has no SKILL.md at root")

    try:
        append_installed_skill_to_registry(safe_folder, repo_source_url=url)
    except Exception:
        logger.exception(
            "skill_install: bundled clone succeeded but skills_registry.json append failed",
        )


def append_installed_skill_to_registry(
    skill_folder: str,
    *,
    repo_source_url: str = "",
    registry_file: Path | None = None,
    skills_root: Path | None = None,
) -> bool:
    """
    Append one entry to ``skills_registry.json`` if ``skill_folder`` is not already listed.

    Uses ``skill_folder`` as registry ``id`` and ``path`` (matches on-disk layout). ``name`` /
    ``description`` come from SKILL.md YAML frontmatter when present. Never raises — returns
    ``False`` if skipped or on error (clone already succeeded).
    """
    folder = skill_folder.strip()
    if not folder:
        return False

    root = skills_root or bundled_skills_directory()
    reg_path = registry_file or skills_registry_path()
    skill_dir = root / folder

    meta = _skill_registry_metadata(skill_dir, folder_id=folder, repo_url=repo_source_url)

    try:
        entries = _load_skills_registry_list(reg_path)
    except OSError:
        logger.warning("skills_registry.json unreadable — skip append for %s", folder)
        return False
    if entries is None:
        logger.warning(
            "skills_registry.json exists but is invalid JSON — skip append for %s (fix file manually)",
            folder,
        )
        return False

    key = meta["id"].strip().lower()
    for row in entries:
        rid = str(row.get("id") or "").strip().lower()
        if rid == key:
            logger.info("skills_registry.json already contains id %r — skip append", meta["id"])
            return False

    entries.append(
        {
            "id": meta["id"],
            "name": meta["name"],
            "description": meta["description"],
            "path": meta["path"],
        }
    )
    entries.sort(key=lambda r: str(r.get("id") or "").lower())

    try:
        _atomic_write_json(reg_path, entries)
    except OSError:
        logger.warning("skills_registry.json write failed — skip append for %s", folder, exc_info=True)
        return False

    logger.info("skills_registry.json appended skill %r", meta["id"])
    return True


def _load_skills_registry_list(path: Path) -> Optional[list[dict[str, Any]]]:
    """Return rows, ``[]`` if missing, or ``None`` if file exists but is not valid JSON."""
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, list):
        return None
    return [x for x in raw if isinstance(x, dict)]


def _atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _skill_registry_metadata(skill_dir: Path, folder_id: str, repo_url: str) -> dict[str, str]:
    """Derive registry row fields from SKILL.md frontmatter (best-effort)."""
    name = folder_id
    description = (
        f"Bundled skill installed from git. Source: {repo_url}"
        if repo_url.strip()
        else "Bundled skill (installed via approve flow)."
    )

    md_path = skill_dir / "SKILL.md"
    if not md_path.is_file():
        md_path = skill_dir / "skill.md"
    if md_path.is_file():
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        if text.startswith("---"):
            end = text.find("---", 3)
            if end > 0:
                fm = text[3:end]
                fm_id = ""
                for line in fm.split("\n"):
                    line = line.strip()
                    if line.lower().startswith("id:"):
                        fm_id = line.split(":", 1)[1].strip().strip('"').strip("'")
                    elif line.lower().startswith("name:"):
                        v = line.split(":", 1)[1].strip().strip('"').strip("'")
                        if v:
                            name = v
                    elif line.lower().startswith("description:"):
                        v = line.split(":", 1)[1].strip().strip('"').strip("'")
                        if v:
                            description = v
                if fm_id and fm_id.strip().lower() != folder_id.strip().lower():
                    logger.debug(
                        "SKILL frontmatter id %r differs from folder %r — registry id uses folder",
                        fm_id,
                        folder_id,
                    )

    return {"id": folder_id, "name": name, "description": description, "path": folder_id}


def _normalize_mcp_server_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Mirror Web MCP route normalization for one server object."""
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    transport = str(raw.get("transport") or "stdio").strip().lower()
    if transport not in ("stdio", "sse"):
        return None
    entry: dict[str, Any] = {
        "name": name,
        "enabled": bool(raw.get("enabled", True)),
        "transport": transport,
    }
    if transport == "stdio":
        cmd = raw.get("command") or []
        if isinstance(cmd, list):
            entry["command"] = [str(x) for x in cmd]
        env = raw.get("env") or {}
        if isinstance(env, dict) and env:
            entry["env"] = {str(k): str(v) for k, v in env.items()}
        if not entry.get("command"):
            return None
    else:
        entry["url"] = str(raw.get("url") or "").strip()
        hdr = raw.get("headers") or {}
        if isinstance(hdr, dict) and hdr:
            entry["headers"] = {str(k): str(v) for k, v in hdr.items()}
        if not entry["url"]:
            return None
    return entry


def _is_safe_https_clone_url(url: str) -> bool:
    p = urlparse(url.strip())
    if p.scheme != "https":
        return False
    if not p.netloc:
        return False
    if ".." in url:
        return False
    return True


def _folder_name_from_git_url(url: str) -> str:
    path = urlparse(url.strip()).path.strip("/")
    if not path:
        return ""
    base = path.rsplit("/", 1)[-1]
    if base.endswith(".git"):
        base = base[: -len(".git")]
    return base


def _sanitize_skill_folder_name(name: str) -> str:
    s = name.strip()
    if not s:
        return ""
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", s).strip("-_.")
    return s[:128] if s else ""
