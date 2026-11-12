"""Requirements Pack: a reverse-engineered capture of what has been asked so far (2026-11-11).

Stored DB-first as the Plan item ``requirements_pack`` (never projected to a file). The
proposed repository location is ``planning/REQUIREMENTS.md``; writing it there is a
separate, explicitly approved change and is not done here. Inputs: the project README /
AGENTS.md (read-only), Plan documents, the project's Development threads and tickets from
the last ``HISTORY_DAYS`` days, and Paul's manual plan edits. Every requirement in the
ledger is tagged ``derived`` (existing project / already communicated) or ``new``
(forward-looking); the same key stated differently by both is a conflict.
"""
from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from typing import Any

from distr.core.planning.provenance import origin_of

PACK_TYPE = "requirements_pack"
PROPOSED_PATH = "planning/REQUIREMENTS.md"
HISTORY_DAYS = 90
LEDGER_TYPES = ("frac", "prd", PACK_TYPE)
_REQ = re.compile(r"^\s*(?:#{2,4}\s+|[-*]\s+)(?:\*\*)?((?:FR|NFR|INF|NEW)-\d{3,})(?:\*\*)?\s*[:\u2014\-]?\s*(.*)$", re.M)
_COMMENT = re.compile(r"<!--.*?-->")
_GEN_HEADER = "<!-- decisions:generated requirements_pack -->"
_NEW_START, _NEW_END = "<!-- decisions:new-instructions -->", "<!-- /decisions:new-instructions -->"


def _norm(text: str) -> str:
    return " ".join(_COMMENT.sub("", str(text or "")).lower().split())


def parse_requirements(content: str) -> list[tuple[str, str]]:
    out, seen = [], set()
    for key, statement in _REQ.findall(str(content or "")):
        if key in seen:
            continue
        seen.add(key)
        out.append((key, _COMMENT.sub("", statement).strip()))
    return out


def _pack_new_block(content: str) -> str:
    text = str(content or "")
    if _NEW_START in text and _NEW_END in text:
        return text.split(_NEW_START, 1)[1].split(_NEW_END, 1)[0]
    return ""


def ledger_from_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    """items: dicts with item_type, id, title, content, last_source."""
    entries: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for item in items:
        kind = item.get("item_type")
        if kind not in LEDGER_TYPES:
            continue
        if kind == PACK_TYPE:
            # Only the hand-maintained "New instructions" block of the pack is a source.
            pairs, origin = parse_requirements(_pack_new_block(item.get("content") or "")), "new"
        else:
            pairs = parse_requirements(item.get("content") or "")
            origin = origin_of(item.get("last_source"))
            origin = "new" if origin == "new" else "derived"  # user edits/scans = already communicated
        for key, statement in pairs:
            current = entries.get(key)
            row = {"key": key, "statement": statement[:300], "origin": origin, "item_id": item.get("id"), "item_type": kind}
            if current is None:
                entries[key] = row
            elif current["origin"] != origin and _norm(current["statement"]) != _norm(statement) and statement:
                conflicts.append({"key": key, "derived": current if current["origin"] == "derived" else row,
                                  "new": row if origin == "new" else current})
                current["status"] = "conflict"
    rows = sorted(entries.values(), key=lambda r: r["key"])
    conflict_keys = sorted({c["key"] for c in conflicts})
    return {"requirements": rows, "conflicts": conflicts, "counts": {
        "derived": sum(1 for r in rows if r["origin"] == "derived"),
        "new": sum(1 for r in rows if r["origin"] == "new"),
        "conflicts": len(conflict_keys)}, "conflict_keys": conflict_keys}


def _read(path: Path, limit: int) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit].strip() if path.is_file() else ""
    except OSError:
        return ""


def _redact(value: Any, limit: int) -> str:
    try:
        from distr.core.chat_turns import redact_text
        text = redact_text(value, limit=limit)
    except Exception:
        text = str(value or "")[:limit]
    return " ".join(str(text).split())[:limit]


def _history(db, project_id: int, since) -> dict[str, list[dict[str, Any]]]:
    from sqlalchemy import or_
    from distr.core.db import Chat
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket

    board_ids = [row.id for row in db.query(KanbanBoard.id).filter(KanbanBoard.default_project_id == int(project_id)).all()]
    lane_ids = [row.id for row in db.query(KanbanLane.id).filter(KanbanLane.board_id.in_(board_ids)).all()] if board_ids else []
    lanes = {row.id: row.name for row in db.query(KanbanLane).filter(KanbanLane.id.in_(lane_ids)).all()} if lane_ids else {}
    ticket_filter = [KanbanTicket.linked_project_id == int(project_id)]
    if lane_ids:
        ticket_filter.append(KanbanTicket.lane_id.in_(lane_ids))
    tickets = (db.query(KanbanTicket).filter(or_(*ticket_filter), KanbanTicket.modified_date >= since)
               .order_by(KanbanTicket.modified_date.desc()).limit(60).all())
    threads = (db.query(Chat).filter(Chat.project_id == int(project_id), Chat.parent_id.is_(None), Chat.created_date >= since)
               .order_by(Chat.created_date.desc()).limit(40).all())
    return {
        "tickets": [{"id": t.id, "title": _redact(t.title, 160), "lane": lanes.get(t.lane_id, ""),
                     "summary": _redact(t.description, 280)} for t in tickets],
        "threads": [{"id": c.id, "title": _redact(c.title, 160), "prompt": _redact(c.input, 280),
                     "archived": bool(c.is_archived)} for c in threads],
    }


def build_pack(*, project_name: str, readme: str, agents: str, items: list[dict[str, Any]], history: dict[str, list],
               user_edits: list[dict[str, Any]], ledger: dict[str, Any], new_block: str, generated_at: str) -> str:
    def rows(values, fmt, empty):
        return [fmt(v) for v in values] or [empty]

    lines = [_GEN_HEADER, f"# {project_name} — Requirements Pack", "",
             f"_Generated {generated_at} by DecisionsAI from the existing project and its last {HISTORY_DAYS} days of "
             f"Development threads and tickets. Stored in Decisions only; proposed repository path `{PROPOSED_PATH}` "
             "(written there only as an approved change). Edit only the New instructions block; everything else is regenerated._", "",
             "## 1. What this project is (derived)", ""]
    intro = (readme or agents or "").strip()
    lines += [("> " + "\n> ".join(intro[:1800].splitlines())) if intro else "- No README or AGENTS.md found in the project folder.", ""]
    lines += ["## 2. What has been asked so far (derived)", "", "### Tickets", ""]
    lines += rows(history.get("tickets", []), lambda t: f"- #{t['id']} [{t['lane'] or 'lane?'}] {t['title']}" + (f" — {t['summary']}" if t["summary"] else ""),
                  "- No tickets in this period.")
    lines += ["", "### Development threads (first prompt)", ""]
    lines += rows(history.get("threads", []), lambda c: f"- Thread #{c['id']}: {c['title'] or '(untitled)'}" + (f" — \u201c{c['prompt']}\u201d" if c["prompt"] else ""),
                  "- No Development threads in this period.")
    lines += ["", "### Plan edits made by you", ""]
    lines += rows(user_edits, lambda e: f"- {e['item_type']} \u201c{e['title']}\u201d revision {e['revision']}" + (f": {e['instruction']}" if e.get("instruction") else ""),
                  "- None.")
    lines += ["", "## 3. Current system (derived)", ""]
    lines += rows([i for i in items if i.get("item_type") not in (PACK_TYPE,)],
                  lambda i: f"- {i['item_type']}: {i['title']} — {origin_label(i.get('last_source'))}", "- The Plan has no generated sections yet.")
    lines += ["", "## 4. Requirements ledger", "", "| Key | Origin | Status | Statement | Source |", "|---|---|---|---|---|"]
    for r in ledger["requirements"]:
        statement = r["statement"].replace("|", "/")[:160]
        lines.append(f"| {r['key']} | {r['origin']} | {r.get('status', 'open')} | {statement} | {r['item_type']} #{r['item_id']} |")
    if not ledger["requirements"]:
        lines.append("| — | — | — | No FR/NFR/INF keys found in FRAC or PRD yet. | — |")
    lines += ["", "## 5. New instructions (forward-looking, origin: new)", "",
              "Add new requirements here as `- NEW-001: …` (or reuse an FR key to change it). This block is kept on regeneration.", "",
              _NEW_START, (new_block.strip() or "- (none yet)"), _NEW_END, "", "## 6. Conflicts", ""]
    lines += rows(ledger["conflicts"], lambda c: f"- {c['key']}: derived \u201c{c['derived']['statement'][:120]}\u201d vs new \u201c{c['new']['statement'][:120]}\u201d",
                  "- None detected.")
    lines += ["", "## 7. Architecture and infrastructure layers", "",
              "- [derived] Architecture/ERD and file structure as listed in section 3.",
              "- [new] Changes must cite a NEW-/FR- key from section 4 or 5 before tickets are built.", "",
              "## 8. Prompt overview", "",
              "- Development workers receive this plan through the planning summary (origin per section, requirement counts, conflicts).",
              "- Tickets built from Plan cite their source Plan items and requirement keys; derived behaviour must not regress unless a new key supersedes it.", ""]
    return "\n".join(lines)


def origin_label(source: str | None) -> str:
    from distr.core.planning.provenance import ORIGIN_LABELS
    return ORIGIN_LABELS.get(origin_of(source), "Unknown source")


def generate_requirements_pack(workspace_id: int, *, new_instructions: str = "") -> dict[str, Any]:
    from distr.core.db.projects import Project
    from distr.core.db.workflow import PlanItem, PlanItemRevision, PlanWorkspace
    from distr.core.planning import service

    detail = service.get_workspace(int(workspace_id))
    if detail is None:
        raise LookupError("Plan workspace not found.")
    items = detail.get("items") or []
    existing = next((i for i in items if i.get("item_type") == PACK_TYPE), None)
    new_block = _pack_new_block((existing or {}).get("content") or "")
    if new_instructions.strip():
        new_block = (new_block.strip() + "\n" + new_instructions.strip()).strip()
    with service.get_session() as db:
        workspace = db.get(PlanWorkspace, int(workspace_id))
        project = db.get(Project, int(workspace.project_id)) if workspace and workspace.project_id else None
        project_name = str((project.name if project else None) or detail.get("board_name") or "Project")
        root = Path(project.folder_location).expanduser() if project and project.folder_location else None
        since = utc_now() - timedelta(days=HISTORY_DAYS)
        history = _history(db, int(project.id), since) if project else {"tickets": [], "threads": []}
        live_ids = [int(i["id"]) for i in items]
        user_edits = [{"item_type": item_type, "title": _redact(title, 120), "revision": revision, "instruction": _redact(instruction, 200)}
                      for item_type, title, revision, instruction in db.query(
                          PlanItem.item_type, PlanItemRevision.title, PlanItemRevision.revision, PlanItemRevision.instruction)
                      .join(PlanItem, PlanItem.id == PlanItemRevision.item_id)
                      .filter(PlanItemRevision.item_id.in_(live_ids or [0]), PlanItemRevision.source == "user")
                      .order_by(PlanItemRevision.created_at.desc()).limit(40).all()]
    readme = _read(root / "README.md", 6000) if root else ""
    agents = _read(root / "AGENTS.md", 3000) if root else ""
    probe = [dict(i) for i in items if i.get("item_type") != PACK_TYPE]
    probe.append({"item_type": PACK_TYPE, "id": (existing or {}).get("id"), "content": f"{_NEW_START}{new_block}{_NEW_END}"})
    ledger = ledger_from_items(probe)
    content = build_pack(project_name=project_name, readme=readme, agents=agents, items=probe[:-1], history=history,
                         user_edits=user_edits, ledger=ledger, new_block=new_block,
                         generated_at=utc_now().strftime("%Y-%m-%d %H:%M UTC"))
    if existing:
        if existing.get("content") == content:
            item = existing
        else:
            item = service.update_item(int(existing["id"]), content=content, source=PACK_TYPE,
                                       instruction="Regenerated Requirements Pack", workspace_id=int(workspace_id))
    else:
        item = service.create_item(workspace_id=int(workspace_id), item_type=PACK_TYPE, title="Requirements Pack",
                                   content=content, content_format="markdown", source=PACK_TYPE,
                                   instruction="Generated Requirements Pack")
    return {"item": item, "ledger": ledger, "proposed_path": PROPOSED_PATH, "history_days": HISTORY_DAYS,
            "tickets": len(history["tickets"]), "threads": len(history["threads"]), "written_to_repo": False}


def utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)
