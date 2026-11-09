"""Period work report: collate work intervals across sources, analyse, persist, export PDF.

Sources (only what Decisions can actually read; empty sources are reported as empty):
- Decisions thread timer (DevelopmentTimeEntry), schedule timer blocks (ScheduleBlock),
  workflow runs, automation runs, and project execution sessions (Pi / Codex / Cursor / native)
  from the Decisions database.
- Local Pi ("Py") CLI sessions: ~/.pi/agent/sessions/**.jsonl
- Local Codex sessions: ~/.codex/sessions/YYYY/MM/DD/*.jsonl
Cursor IDE's own local history is not read; Cursor work is counted through Decisions records.

All periods are whole days in Africa/Johannesburg. Database times are naive UTC.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")
UNASSIGNED_ID = 0
UNASSIGNED_NAME = "Unassigned"
SESSION_GAP_SECONDS = 20 * 60          # a local CLI session splits into blocks across idle gaps
MIN_BLOCK_SECONDS = 60                 # one isolated event counts as one minute
MAX_BLOCK_SECONDS = 24 * 60 * 60
MAX_CANCELLED_SECONDS = 6 * 60 * 60
REPORT_MARKER = "[decisions-period-report]"  # keeps the report's own model run out of later reports
MAX_PROMPT_CHARS = 160
MAX_ANALYSIS_CONTEXT = 60000

FAMILIES = {
    "decisions": "Decisions AI",
    "pi": "Pi (Py) CLI",
    "codex": "Codex",
    "cursor": "Cursor",
}

_TS_RE = re.compile(r'"timestamp"\s*:\s*"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z?)"')


# ---------------------------------------------------------------- periods

@dataclass(frozen=True)
class Period:
    start: date
    end: date            # inclusive
    kind: str            # "rolling" | "month" | "custom"
    label: str

    @property
    def key(self) -> str:
        return f"{self.start.isoformat()}_{self.end.isoformat()}"

    def bounds_utc(self) -> tuple[datetime, datetime]:
        """Naive-UTC [start, end) for SAST midnight boundaries."""
        lo = datetime(self.start.year, self.start.month, self.start.day, tzinfo=SAST)
        hi_day = self.end + timedelta(days=1)
        hi = datetime(hi_day.year, hi_day.month, hi_day.day, tzinfo=SAST)
        return lo.astimezone(UTC).replace(tzinfo=None), hi.astimezone(UTC).replace(tzinfo=None)


def today_sast(now: datetime | None = None) -> date:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(SAST).date()


def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _month_end(year: int, month: int) -> date:
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    return date(ny, nm, 1) - timedelta(days=1)


def default_period(today: date | None = None) -> Period:
    """Rolling default: the 6th of the previous calendar month through today (SAST)."""
    today = today or today_sast()
    year, month = _prev_month(today.year, today.month)
    start = date(year, month, 6)
    label = f"{start.strftime('%-d %b %Y')} – {today.strftime('%-d %b %Y')}"
    return Period(start, today, "rolling", label)


def month_period(value: str, today: date | None = None) -> Period:
    match = re.fullmatch(r"(\d{4})-(\d{2})", str(value or "").strip())
    if not match:
        raise ValueError("Month must look like YYYY-MM.")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        raise ValueError("Month must look like YYYY-MM.")
    start = date(year, month, 1)
    end = _month_end(year, month)
    today = today or today_sast()
    label = start.strftime("%B %Y")
    if start <= today < end:
        end = today
        label += " (to date)"
    return Period(start, end, "month", label)


def resolve_period(*, month: str | None = None, start: str | None = None, end: str | None = None,
                   today: date | None = None) -> Period:
    today = today or today_sast()
    if month:
        return month_period(month, today)
    if start or end:
        try:
            lo = date.fromisoformat(str(start))
            hi = date.fromisoformat(str(end))
        except (TypeError, ValueError) as exc:
            raise ValueError("Start and end must be YYYY-MM-DD dates.") from exc
        if hi < lo:
            raise ValueError("The period end is before its start.")
        if (hi - lo).days > 400:
            raise ValueError("Pick a period of at most about one year.")
        default = default_period(today)
        if (lo, hi) == (default.start, default.end):
            return default
        return Period(lo, hi, "custom", f"{lo.strftime('%-d %b %Y')} – {hi.strftime('%-d %b %Y')}")
    return default_period(today)


def period_options(today: date | None = None, months: int = 12) -> list[dict]:
    """Rolling default first, then calendar months newest first."""
    today = today or today_sast()
    default = default_period(today)
    options = [{"value": "rolling", "label": f"{default.label} (default)", "start": default.start.isoformat(),
                "end": default.end.isoformat(), "kind": "rolling"}]
    year, month = today.year, today.month
    for _ in range(months):
        period = month_period(f"{year:04d}-{month:02d}", today)
        options.append({"value": f"{year:04d}-{month:02d}", "label": period.label, "start": period.start.isoformat(),
                        "end": period.end.isoformat(), "kind": "month"})
        year, month = _prev_month(year, month)
    return options


def parse_project_ids(value) -> list[int] | None:
    """None means all projects. 0 is the Unassigned bucket."""
    if value is None or value == "" or value == "all":
        return None
    raw = value if isinstance(value, (list, tuple)) else str(value).split(",")
    ids = []
    for item in raw:
        text = str(item).strip()
        if not text or text == "all":
            continue
        if not text.isdigit():
            raise ValueError("Project ids must be numbers.")
        ids.append(int(text))
    return sorted(set(ids)) or None


def project_key(project_ids: list[int] | None) -> str:
    return "all" if not project_ids else ",".join(str(i) for i in sorted(set(project_ids)))


# ---------------------------------------------------------------- helpers

def _utc_iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat() + "Z"


def _local_iso(value: datetime) -> str:
    return value.replace(tzinfo=UTC).astimezone(SAST).replace(tzinfo=None, microsecond=0).isoformat()


def _parse_ts(text: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _norm_path(path: str | None) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    text = os.path.expanduser(text)
    if text.startswith("/private/var/"):
        text = text[len("/private"):]
    return text.rstrip("/") or "/"


class ProjectMatcher:
    """Longest folder-prefix match from a working directory to a Decisions project."""

    def __init__(self, projects: dict[int, dict]):
        self.folders = sorted(
            ((_norm_path(p.get("folder")), pid) for pid, p in projects.items() if _norm_path(p.get("folder")) not in ("", "/")),
            key=lambda item: len(item[0]), reverse=True,
        )

    def match(self, cwd: str | None) -> int | None:
        path = _norm_path(cwd)
        if not path:
            return None
        for folder, pid in self.folders:
            if path == folder or path.startswith(folder + "/"):
                return pid
        return None


def _is_test_cwd(cwd: str | None) -> bool:
    text = str(cwd or "")
    return "/pytest-of-" in text or ("/pytest-" in text and "/T/" in text)


def _clip(start: datetime, end: datetime, lo: datetime, hi: datetime):
    start, end = max(start, lo), min(end, hi)
    return (start, end) if end > start else None


def _block(source, family, label, start, end, project_id, title, ref=""):
    return {
        "source": source, "family": family, "source_label": label,
        "start": start, "end": end, "project_id": project_id, "title": (title or "").strip()[:200] or label,
        "ref": ref,
    }


def _record_seconds(started, ended, status) -> int | None:
    if not started or not ended:
        return None
    raw = (ended - started).total_seconds()
    if raw < 0:
        return None
    if str(status or "").lower() in {"cancelled", "canceled", "failed"} and raw > MAX_CANCELLED_SECONDS:
        return None
    if raw > MAX_BLOCK_SECONDS:
        return None
    return int(raw)


# ---------------------------------------------------------------- database sources

def load_projects(get_session=None) -> dict[int, dict]:
    from distr.core.db.projects import Project
    if get_session is None:
        from distr.core.reports import service
        get_session = service.get_session
    with get_session() as db:
        return {int(p.id): {"id": int(p.id), "name": p.name or f"Project {p.id}", "folder": p.folder_location or ""}
                for p in db.query(Project).order_by(Project.position, Project.id).all()}


def collect_database_blocks(lo: datetime, hi: datetime, get_session=None) -> tuple[list[dict], dict]:
    from sqlalchemy import or_
    from distr.core.db import Chat
    from distr.core.db.automation import Automation, AutomationRun
    from distr.core.db.kanban import KanbanTicket, ProjectExecutionSession
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, DevelopmentTimeEntry, DevelopmentWorkItem

    if get_session is None:
        from distr.core.reports import service
        get_session = service.get_session
    blocks: list[dict] = []
    counts = defaultdict(int)
    pad = timedelta(hours=24)  # records that started before the window but ran into it
    with get_session() as db:
        chat_project = {}

        def project_for_chat(chat_id):
            if chat_id is None:
                return None
            if chat_id in chat_project:
                return chat_project[chat_id]
            pid, seen, current = None, set(), db.get(Chat, int(chat_id))
            while current is not None and current.id not in seen:
                seen.add(current.id)
                if current.project_id:
                    pid = int(current.project_id)
                    break
                current = db.get(Chat, current.parent_id) if current.parent_id else None
            chat_project[chat_id] = pid
            return pid

        rows = (db.query(DevelopmentTimeEntry, DevelopmentWorkItem, Chat)
                .outerjoin(DevelopmentWorkItem, DevelopmentTimeEntry.work_item_id == DevelopmentWorkItem.id)
                .outerjoin(Chat, Chat.id == DevelopmentTimeEntry.chat_id)
                .filter(DevelopmentTimeEntry.source != "reset",
                        DevelopmentTimeEntry.started_at < hi, DevelopmentTimeEntry.ended_at > lo)
                .all())
        for entry, item, chat in rows:
            seconds = _record_seconds(entry.started_at, entry.ended_at, None)
            if not seconds:
                continue
            title = (item.ticket_title if item is not None and item.ticket_title else None) or (
                chat.title if chat is not None and chat.title else f"Thread {entry.chat_id}")
            blocks.append(_block("decisions_thread", "decisions", "Decisions thread timer", entry.started_at,
                                 entry.ended_at, project_for_chat(entry.chat_id), title, f"chat:{entry.chat_id}"))
            counts["decisions_thread"] += 1

        try:
            from distr.core.db.schedule_blocks import ScheduleBlock
            for block in (db.query(ScheduleBlock)
                          .filter(ScheduleBlock.is_timer_entry.is_(True), ScheduleBlock.start_at < hi, ScheduleBlock.end_at > lo)
                          .all()):
                if _record_seconds(block.start_at, block.end_at, None):
                    blocks.append(_block("schedule_timer", "decisions", "Decisions schedule timer", block.start_at,
                                         block.end_at, block.project_id, block.title or "Timer", f"schedule:{block.id}"))
                    counts["schedule_timer"] += 1
        except Exception:  # noqa: BLE001 - optional table on older installs
            pass

        for run, definition in (db.query(AutoWorkflowRun, AutoWorkflow)
                                .join(AutoWorkflow, AutoWorkflowRun.workflow_id == AutoWorkflow.id)
                                .filter(AutoWorkflowRun.status != "dispatch_accepted",
                                        AutoWorkflowRun.started_at < hi, AutoWorkflowRun.started_at >= lo - pad)
                                .all()):
            if _record_seconds(run.started_at, run.completed_at, run.status) is None:
                continue
            pid = project_for_chat(run.chat_id or definition.chat_id)
            blocks.append(_block("decisions_workflow", "decisions", "Decisions workflow run", run.started_at,
                                 run.completed_at, pid, definition.name, f"workflow_run:{run.id}"))
            counts["decisions_workflow"] += 1

        for run, definition in (db.query(AutomationRun, Automation)
                                .join(Automation, AutomationRun.automation_id == Automation.id)
                                .filter(AutomationRun.status != "dispatch_accepted",
                                        AutomationRun.started_at < hi, AutomationRun.started_at >= lo - pad)
                                .all()):
            if _record_seconds(run.started_at, run.completed_at, run.status) is None:
                continue
            blocks.append(_block("decisions_automation", "decisions", "Decisions automation run", run.started_at,
                                 run.completed_at, definition.project_id, definition.name, f"automation_run:{run.id}"))
            counts["decisions_automation"] += 1

        tickets = {}
        sessions = (db.query(ProjectExecutionSession)
                    .filter(ProjectExecutionSession.started_at < hi, ProjectExecutionSession.started_at >= lo - pad,
                            or_(ProjectExecutionSession.input_packet.is_(None),
                                ~ProjectExecutionSession.input_packet.contains(REPORT_MARKER)))
                    .all())
        ticket_ids = {s.ticket_id for s in sessions if s.ticket_id}
        if ticket_ids:
            tickets = dict(db.query(KanbanTicket.id, KanbanTicket.title).filter(KanbanTicket.id.in_(ticket_ids)).all())
        for session in sessions:
            end = session.completed_at or session.updated_at
            if _record_seconds(session.started_at, end, session.status) is None:
                continue
            backend = str(session.route_backend or "").strip().lower()
            family = backend if backend in {"pi", "codex", "cursor"} else "decisions"
            ide = str(session.route_type or "") == "ide_bridge"
            label = {
                "pi": "Pi via Decisions",
                "codex": "Codex IDE" if ide else "Codex via Decisions",
                "cursor": "Cursor IDE" if ide else "Cursor via Decisions",
            }.get(family, "Decisions agent run")
            title = tickets.get(session.ticket_id) or f"{label} ({session.origin or 'run'})"
            source = f"{family}_ide" if ide and family in {"codex", "cursor"} else (
                f"{family}_decisions" if family != "decisions" else "decisions_execution")
            blocks.append(_block(source, family, label, session.started_at, end, session.project_id, title,
                                 f"execution:{session.id}"))
            counts[source] += 1
    return blocks, dict(counts)


# ---------------------------------------------------------------- local CLI session files

def _session_activity(path: Path, *, kind: str) -> tuple[dict, list[datetime]]:
    """Return (meta, timestamps) for one JSONL session file without loading it whole."""
    meta: dict = {"cwd": "", "prompt": "", "id": path.stem}
    stamps: list[datetime] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for index, line in enumerate(handle):
            head = line[:400]
            match = _TS_RE.search(head)
            if match:
                stamp = _parse_ts(match.group(1))
                if stamp:
                    stamps.append(stamp)
            if index == 0:
                try:
                    first = json.loads(line)
                except ValueError:
                    first = {}
                payload = first.get("payload") if kind == "codex" else first
                if isinstance(payload, dict):
                    meta["cwd"] = str(payload.get("cwd") or "")
                    meta["id"] = str(payload.get("id") or meta["id"])
                continue
            if meta["prompt"] or index > 400:
                continue
            if kind == "pi" and '"role":"user"' in line.replace(" ", ""):
                try:
                    message = json.loads(line).get("message") or {}
                except ValueError:
                    continue
                content = message.get("content")
                if isinstance(content, list):
                    content = " ".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
                meta["prompt"] = " ".join(str(content or "").split())[:MAX_PROMPT_CHARS]
            elif kind == "codex" and '"user_message"' in head:
                try:
                    payload = json.loads(line).get("payload") or {}
                except ValueError:
                    continue
                meta["prompt"] = " ".join(str(payload.get("message") or "").split())[:MAX_PROMPT_CHARS]
    return meta, sorted(stamps)


def activity_intervals(stamps: list[datetime], gap_seconds: int = SESSION_GAP_SECONDS) -> list[tuple[datetime, datetime]]:
    """Split event times into active intervals across idle gaps; a lone event counts one minute."""
    intervals = []
    start = prev = None
    for stamp in stamps:
        if start is None:
            start = prev = stamp
            continue
        if (stamp - prev).total_seconds() > gap_seconds:
            intervals.append((start, prev))
            start = stamp
        prev = stamp
    if start is not None:
        intervals.append((start, prev))
    return [(a, b if (b - a).total_seconds() >= MIN_BLOCK_SECONDS else a + timedelta(seconds=MIN_BLOCK_SECONDS))
            for a, b in intervals]


def _session_files(root: Path, lo: datetime, *, kind: str) -> list[Path]:
    if not root.is_dir():
        return []
    cutoff = (lo - timedelta(days=1)).replace(tzinfo=UTC).timestamp()
    files = []
    for path in root.rglob("*.jsonl"):
        try:
            if path.stat().st_mtime >= cutoff:
                files.append(path)
        except OSError:
            continue
    return files


def collect_local_sessions(lo: datetime, hi: datetime, matcher: ProjectMatcher, *, pi_root: Path | None = None,
                           codex_root: Path | None = None) -> tuple[list[dict], dict, dict]:
    home = Path.home()
    roots = {
        "pi_local": (pi_root or home / ".pi" / "agent" / "sessions", "pi", "Pi (Py) CLI session"),
        "codex_local": (codex_root or home / ".codex" / "sessions", "codex", "Codex session"),
    }
    blocks, counts, status = [], defaultdict(int), {}
    for source, (root, family, label) in roots.items():
        if not root.is_dir():
            status[source] = "missing"
            continue
        status[source] = "read"
        for path in _session_files(root, lo, kind=family):
            try:
                meta, stamps = _session_activity(path, kind=family)
            except OSError:
                continue
            if _is_test_cwd(meta["cwd"]):
                continue
            pid = matcher.match(meta["cwd"])
            folder = Path(meta["cwd"]).name if meta["cwd"] else ""
            title = meta["prompt"] or (f"{label} in {folder}" if folder else label)
            used = False
            for start, end in activity_intervals(stamps):
                if _clip(start, end, lo, hi):
                    blocks.append(_block(source, family, label, start, end, pid, title, f"{source}:{meta['id']}"))
                    used = True
            if used:
                counts[source] += 1
    return blocks, dict(counts), status


# ---------------------------------------------------------------- collation

def split_by_sast_day(start: datetime, end: datetime) -> list[tuple[str, datetime, datetime]]:
    """Split a naive-UTC interval at Africa/Johannesburg midnights: [(YYYY-MM-DD, start, end)]."""
    parts = []
    cursor = start
    while cursor < end:
        local_day = cursor.replace(tzinfo=UTC).astimezone(SAST).date()
        next_midnight = datetime(local_day.year, local_day.month, local_day.day, tzinfo=SAST) + timedelta(days=1)
        boundary = next_midnight.astimezone(UTC).replace(tzinfo=None)
        piece_end = min(end, boundary)
        parts.append((local_day.isoformat(), cursor, piece_end))
        cursor = piece_end
    return parts


def _union_seconds(intervals: list[tuple[datetime, datetime]]) -> int:
    total, cur_start, cur_end = 0.0, None, None
    for start, end in sorted(intervals):
        if cur_end is None or start > cur_end:
            if cur_end is not None:
                total += (cur_end - cur_start).total_seconds()
            cur_start, cur_end = start, end
        else:
            cur_end = max(cur_end, end)
    if cur_end is not None:
        total += (cur_end - cur_start).total_seconds()
    return int(total)


def collate(period: Period, project_ids: list[int] | None = None, *, get_session=None, pi_root=None,
            codex_root=None, include_local=True, include_automations=False) -> dict:
    lo, hi = period.bounds_utc()
    projects = load_projects(get_session)
    matcher = ProjectMatcher(projects)
    raw, db_counts = collect_database_blocks(lo, hi, get_session)
    local_counts, local_status = {}, {}
    if include_local:
        local, local_counts, local_status = collect_local_sessions(lo, hi, matcher, pi_root=pi_root, codex_root=codex_root)
        raw.extend(local)
    selected = set(project_ids) if project_ids else None
    blocks = []
    for block in raw:
        if block["source"] == "decisions_automation" and not include_automations:
            continue
        raw_pid = block["project_id"]
        pid = int(raw_pid) if raw_pid is not None and int(raw_pid) in projects else UNASSIGNED_ID
        if selected is not None and pid not in selected:
            continue
        job_start, job_end = block["start"], block["end"]
        clipped = _clip(job_start, job_end, lo, hi)
        if not clipped:
            continue
        start, end = clipped
        if (end - start).total_seconds() < 1:
            continue
        blocks.append({**block, "project_id": pid, "start": start, "end": end,
                       "job_start": job_start, "job_end": job_end})
    blocks.sort(key=lambda b: (b["start"], b["source"]))

    per_project = defaultdict(list)
    per_project_activity = defaultdict(int)
    first_job: dict[int, datetime] = {}
    per_source = defaultdict(lambda: {"seconds": 0, "blocks": 0})
    per_family = defaultdict(list)
    per_day = defaultdict(lambda: defaultdict(list))
    titles = defaultdict(lambda: defaultdict(lambda: {"seconds": 0, "sources": set(), "days": set()}))
    for block in blocks:
        pid = block["project_id"]
        span = (block["start"], block["end"])
        seconds = int((block["end"] - block["start"]).total_seconds())
        per_project[pid].append(span)
        per_project_activity[pid] += seconds
        job_start = block["job_start"]
        if pid not in first_job or job_start < first_job[pid]:
            first_job[pid] = job_start
        per_family[block["family"]].append(span)
        per_source[block["source"]]["seconds"] += seconds
        per_source[block["source"]]["blocks"] += 1
        entry = titles[pid][block["title"]]
        entry["seconds"] += seconds
        entry["sources"].add(block["source_label"])
        for day, day_start, day_end in split_by_sast_day(block["start"], block["end"]):
            per_day[day][pid].append((day_start, day_end))
            entry["days"].add(day)

    def name(pid):
        return projects.get(pid, {}).get("name") if pid in projects else UNASSIGNED_NAME

    project_rows = sorted(
        ({"project_id": pid, "name": name(pid), "seconds": _union_seconds(spans),
          "activity_seconds": per_project_activity[pid], "blocks": len(spans),
          "days": sum(1 for d in per_day.values() if pid in d),
          "first_start": _utc_iso(first_job[pid]), "first_start_local": _local_iso(first_job[pid])}
         for pid, spans in per_project.items()),
        key=lambda row: -row["seconds"],
    )
    total = _union_seconds([span for spans in per_project.values() for span in spans])
    activity_total = sum(per_project_activity.values())
    families = [{"family": key, "label": label, "seconds": _union_seconds(per_family.get(key, [])),
                 "blocks": len(per_family.get(key, []))} for key, label in FAMILIES.items()]
    sources = _source_status(db_counts, local_counts, local_status, per_source)
    if not include_automations:
        for row in sources:
            if row["source"] == "decisions_automation":
                row["state"] = "excluded"
    days = [{"date": day, "seconds": _union_seconds([s for spans in pids.values() for s in spans]),
             "projects": {str(pid): _union_seconds(spans) for pid, spans in pids.items()}}
            for day, pids in sorted(per_day.items())]
    highlights = {
        str(pid): [
            {"title": title, "seconds": data["seconds"], "sources": sorted(data["sources"]), "days": len(data["days"])}
            for title, data in sorted(items.items(), key=lambda kv: -kv[1]["seconds"])[:25]
        ]
        for pid, items in titles.items()
    }
    long_timers = sum(1 for b in blocks if b["source"] == "decisions_thread"
                      and (b["end"] - b["start"]).total_seconds() > 8 * 3600)
    return {
        "period": {"start": period.start.isoformat(), "end": period.end.isoformat(), "kind": period.kind,
                   "label": period.label, "key": period.key, "timezone": "Africa/Johannesburg"},
        "project_ids": project_ids or [],
        "include_automations": bool(include_automations),
        "project_key": project_key(project_ids),
        "projects": [{"id": pid, "name": p["name"]} for pid, p in projects.items()]
                    + [{"id": UNASSIGNED_ID, "name": UNASSIGNED_NAME}],
        "totals": {"seconds": total, "activity_seconds": activity_total, "blocks": len(blocks),
                   "projects": len(project_rows), "active_days": len(per_day)},
        "dedupe": {
            "wall_clock_seconds": "totals.seconds and by_project.seconds",
            "activity_seconds": "totals.activity_seconds and by_project.activity_seconds",
            "rule": (
                "Overlapping intervals from any harness count once in clock time and once per block "
                "in recorded time. Blocks are kept."
            ),
        },
        "collected_at": _utc_iso(datetime.now(UTC).replace(tzinfo=None)),
        "by_project": project_rows,
        "by_family": families,
        "by_day": days,
        "sources": sources,
        "highlights": highlights,
        "blocks": [
            {"id": f"b{i}", "source": b["source"], "family": b["family"], "source_label": b["source_label"],
             "project_id": b["project_id"], "project_name": name(b["project_id"]), "title": b["title"],
             "start": _utc_iso(b["start"]), "end": _utc_iso(b["end"]),
             "start_local": _local_iso(b["start"]), "end_local": _local_iso(b["end"]),
             "job_start": _utc_iso(b["job_start"]), "job_end": _utc_iso(b["job_end"]),
             "job_start_local": _local_iso(b["job_start"]), "job_end_local": _local_iso(b["job_end"]),
             "starts_before_period": b["job_start"] < b["start"], "ends_after_period": b["job_end"] > b["end"],
             "seconds": int((b["end"] - b["start"]).total_seconds()), "ref": b["ref"]}
            for i, b in enumerate(blocks)
        ],
        "notes": [
            "Clock time merges overlapping intervals, so a thread timer and the agent run inside it count once.",
            "Recorded time adds every block. The same span from a timer and a run counts in both.",
            "First start is the earliest recorded start for that project, including when the interval began before this period. Calendar bars are clipped to the period.",
            "Local Pi and Codex sessions are split into active blocks wherever there is a gap of more than 20 minutes.",
            "Cursor IDE's own history is not read; Cursor work appears only when it reported into Decisions.",
            "Claude session files are not read. There is no supported adapter for them.",
            ("Scheduled automation runs are included." if include_automations else
             "Scheduled automation runs (background jobs such as nightly DB loads) are excluded; turn on Automations to count them."),
            *([f"{long_timers} thread timer interval(s) ran longer than 8 hours (likely left running); they count as recorded."]
              if long_timers else []),
        ],
    }


_SOURCE_LABELS = {
    "decisions_thread": "Decisions thread timer",
    "schedule_timer": "Decisions schedule timer",
    "decisions_workflow": "Decisions workflow runs",
    "decisions_automation": "Decisions automation runs",
    "decisions_execution": "Decisions agent runs",
    "pi_decisions": "Pi via Decisions",
    "pi_local": "Pi (Py) local sessions",
    "codex_decisions": "Codex via Decisions",
    "codex_ide": "Codex IDE (reported)",
    "codex_local": "Codex local sessions",
    "cursor_decisions": "Cursor via Decisions",
    "cursor_ide": "Cursor IDE (reported)",
}


def _source_status(db_counts, local_counts, local_status, per_source):
    rows = []
    for key, label in _SOURCE_LABELS.items():
        found = per_source.get(key, {"seconds": 0, "blocks": 0})
        records = db_counts.get(key, local_counts.get(key, 0))
        state = "data" if found["blocks"] else "empty"
        if key in local_status and local_status[key] == "missing":
            state = "unavailable"
        rows.append({"source": key, "label": label, "records": records, "blocks": found["blocks"],
                     "seconds": found["seconds"], "state": state})
    rows.append({"source": "cursor_local", "label": "Cursor IDE local history", "records": 0, "blocks": 0,
                 "seconds": 0, "state": "not_read"})
    rows.append({"source": "claude_local", "label": "Claude local history", "records": 0, "blocks": 0,
                 "seconds": 0, "state": "unsupported"})
    return rows


# ---------------------------------------------------------------- AI analysis

def default_model() -> dict:
    """The planning default route (same one Plan uses to build a plan). No fallback chain."""
    from distr.core.planning.chats import default_route
    provider, model = default_route()
    return {"provider": provider or "", "model": model or ""}


def _hours(seconds) -> float:
    return round(int(seconds or 0) / 3600, 2)


def analysis_payload(snapshot: dict) -> dict:
    names = {str(row["project_id"]): row["name"] for row in snapshot["by_project"]}
    payload = {
        "period": snapshot["period"],
        "total_hours": _hours(snapshot["totals"]["seconds"]),
        "projects": [{"name": row["name"], "hours": _hours(row["seconds"]), "active_days": row["days"],
                      "work_items": [{"title": h["title"], "hours": _hours(h["seconds"]), "days": h["days"],
                                      "sources": h["sources"]}
                                     for h in snapshot["highlights"].get(str(row["project_id"]), [])]}
                     for row in snapshot["by_project"]],
        "by_source_family": [{"source": f["label"], "hours": _hours(f["seconds"])} for f in snapshot["by_family"]],
        "sources_without_data": [s["label"] for s in snapshot["sources"] if s["state"] != "data"],
        "busiest_days": sorted(({"date": d["date"], "hours": _hours(d["seconds"]),
                                 "projects": {names.get(k, k): _hours(v) for k, v in d["projects"].items()}}
                                for d in snapshot["by_day"]), key=lambda d: -d["hours"])[:10],
    }
    text = json.dumps(payload, ensure_ascii=False)
    while len(text) > MAX_ANALYSIS_CONTEXT and any(p["work_items"] for p in payload["projects"]):
        for project in payload["projects"]:
            if len(project["work_items"]) > 3:
                project["work_items"] = project["work_items"][: max(3, len(project["work_items"]) // 2)]
        text = json.dumps(payload, ensure_ascii=False)
        if all(len(p["work_items"]) <= 3 for p in payload["projects"]):
            break
    return payload


ANALYSIS_INSTRUCTION = (
    f"{REPORT_MARKER} You write a concise work report for one person's software work over a period. "
    "Use only the supplied data. Do not invent tasks, hours or sources. Hours are already measured; quote them as given. "
    "Return only strict JSON, no code fences: "
    '{"summary": string (3-5 sentences), '
    '"projects": [{"name": string, "hours": number, "highlights": [string, at most 5 short bullets of what was done]}], '
    '"notes": [string, at most 4 short observations, e.g. gaps, sources without data, focus shifts]}. '
    "Order projects by hours, highest first, and include every supplied project."
)


def _decode_analysis(text: str) -> dict:
    clean = str(text or "").strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", clean).strip()
    start, end = clean.find("{"), clean.rfind("}")
    if start < 0 or end < start:
        raise ValueError("The selected model did not return a JSON report.")
    data = json.loads(clean[start:end + 1])
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str):
        raise ValueError("The selected model returned a report without a summary.")
    projects = []
    for row in data.get("projects") or []:
        if isinstance(row, dict) and row.get("name"):
            projects.append({"name": str(row["name"])[:200],
                             "hours": float(row.get("hours") or 0),
                             "highlights": [str(h)[:400] for h in (row.get("highlights") or []) if str(h).strip()][:8]})
    return {"summary": data["summary"].strip()[:4000], "projects": projects,
            "notes": [str(n)[:400] for n in (data.get("notes") or []) if str(n).strip()][:6]}


def run_analysis(snapshot: dict, *, provider: str, model: str, project_hint: int | None = None) -> dict:
    from distr.core.planning import runtime
    from distr.core.planning.conversation import SUPPORTED_PROVIDERS, load_settings_from_db

    provider = str(provider or "").strip().lower()
    model = str(model or "").strip()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError("Select a supported provider for the report model.")
    if not model or model.lower() == "auto":
        raise ValueError("Select an explicit model for the report.")
    projects = load_projects()
    project_id = project_hint if project_hint in projects else None
    if project_id is None:
        # The CLI route needs a project scope; any real project works for a proposal-only run.
        project_id = next((row["project_id"] for row in snapshot["by_project"] if row["project_id"] in projects),
                          next(iter(projects), None))
    if project_id is None:
        raise ValueError("Create a project before building a report.")
    messages = [
        {"role": "system", "content": ANALYSIS_INSTRUCTION},
        {"role": "user", "content": json.dumps(analysis_payload(snapshot), ensure_ascii=False)
         + f"\n{REPORT_MARKER} Write the report JSON for this period."},
    ]
    chunks = []
    try:
        for chunk in runtime.create_stream(provider, model, messages, load_settings_from_db(), project_id=int(project_id),
                                           project_name=projects[project_id]["name"], chat_id=-910000 - int(project_id),
                                           turn_id=None):
            if isinstance(chunk, str):
                chunks.append(chunk)
    except runtime.PlanningRuntimeError as exc:
        raise ValueError(str(exc)) from exc
    try:
        return _decode_analysis("".join(chunks))
    except json.JSONDecodeError as exc:
        raise ValueError("The selected model did not return valid report JSON.") from exc


# ---------------------------------------------------------------- persistence

def _stored_key(project_ids, include_automations) -> str:
    return project_key(project_ids) + ("|auto" if include_automations else "")


def _ensure_table():
    from distr.core.db import engine
    from distr.core.db.work_reports import WorkReport
    WorkReport.__table__.create(bind=engine, checkfirst=True)


def _snapshot_for_storage(snapshot: dict) -> dict:
    return {key: value for key, value in snapshot.items() if key not in {"blocks", "projects"}}


def save_report(snapshot: dict, analysis: dict, *, provider: str, model: str, get_session=None) -> dict:
    from distr.core.db.work_reports import WorkReport
    if get_session is None:
        _ensure_table()
        from distr.core.reports import service
        get_session = service.get_session
    with get_session() as db:
        row = WorkReport(period_key=snapshot["period"]["key"], start_date=snapshot["period"]["start"],
                         end_date=snapshot["period"]["end"],
                         project_key=_stored_key(snapshot["project_ids"], snapshot.get("include_automations")),
                         provider=provider,
                         model=model, status="complete", analysis=json.dumps(analysis),
                         snapshot=json.dumps(_snapshot_for_storage(snapshot), default=str))
        db.add(row)
        db.commit()
        return _row_dict(row)


def latest_report(period: Period, project_ids: list[int] | None, get_session=None, *,
                  include_automations: bool = False) -> dict | None:
    from distr.core.db.work_reports import WorkReport
    if get_session is None:
        _ensure_table()
        from distr.core.reports import service
        get_session = service.get_session
    with get_session() as db:
        row = (db.query(WorkReport)
               .filter(WorkReport.period_key == period.key,
                       WorkReport.project_key == _stored_key(project_ids, include_automations))
               .order_by(WorkReport.created_at.desc(), WorkReport.id.desc()).first())
        return _row_dict(row) if row is not None else None


def _row_dict(row) -> dict:
    try:
        analysis = json.loads(row.analysis or "{}")
    except ValueError:
        analysis = {}
    try:
        snapshot = json.loads(row.snapshot or "{}")
    except ValueError:
        snapshot = {}
    return {"id": row.id, "provider": row.provider, "model": row.model, "status": row.status,
            "created_at": _utc_iso(row.created_at) if row.created_at else None, "analysis": analysis,
            "snapshot_totals": snapshot.get("totals") or {}}


def ledger_day_sast(epoch) -> str:
    """SAST calendar day for a ledger epoch, matching the cost ledger's day label."""
    if epoch is None:
        return ""
    return datetime.fromtimestamp(float(epoch), tz=UTC).astimezone(SAST).date().isoformat()


def _unverified_model_label(model: str) -> bool:
    """True for the requested DeepSeek 4.1 label, not for a real provider model id.

    Slash ids (OpenRouter) and colon ids (Ollama cloud tags) are left alone.
    This does not accept or rename the label.
    """
    text = str(model or "").strip().lower()
    if not text or "/" in text or ":" in text:
        return False
    compact = re.sub(r"[^a-z0-9]+", "", text)
    return compact in {"deepseek41", "deepseekv41"}


def report_route_verdict(provider: str | None = None, model: str | None = None) -> dict:
    """Describe the requested analysis route without substituting a model id."""
    from distr.core.planning.conversation import SUPPORTED_PROVIDERS

    try:
        configured = default_model()
    except Exception:  # noqa: BLE001 - settings unreadable: report that, do not invent a route
        configured = {"provider": "", "model": ""}
    configured_provider = str(configured.get("provider") or "").strip().lower()
    configured_model = str(configured.get("model") or "").strip()
    explicit_provider = str(provider or "").strip().lower()
    explicit_model = str(model or "").strip()
    selected_provider = explicit_provider or configured_provider
    selected_model = explicit_model or configured_model
    blocked = _unverified_model_label(selected_model)
    limitation = (
        "Requested analysis route is Pi, provider OpenRouter, model DeepSeek 4.1. "
        "OpenRouter is a supported provider id. DeepSeek 4.1 is not a model id in the supported route table. "
        "Build does not rename it to deepseek/deepseek-v4.1-flash, deepseek-v4.1-flash:cloud, or any other id."
    )
    return {
        "requested": {"harness": "pi", "provider": "openrouter", "model": "DeepSeek 4.1", "verified": False},
        "configured": {"provider": configured_provider, "model": configured_model},
        "selected": {"provider": selected_provider, "model": selected_model},
        "substituted": False,
        "blocked_unverified_label": blocked,
        "supported_providers": sorted(SUPPORTED_PROVIDERS),
        "harnesses": [
            {"id": "pi_local", "reader": "supported", "detail": "Reads ~/.pi/agent/sessions when that folder exists."},
            {"id": "codex_local", "reader": "supported", "detail": "Reads ~/.codex/sessions when that folder exists."},
            {"id": "decisions_records", "reader": "supported",
             "detail": "Thread timers, schedule timers, workflow runs, and execution sessions already stored in Decisions."},
            {"id": "cursor_local", "reader": "not_read",
             "detail": "Cursor IDE local history is not read. Cursor work counts only from Decisions records."},
            {"id": "claude_local", "reader": "unsupported",
             "detail": "No supported adapter reads Claude session files."},
        ],
        "limitation": limitation,
    }


def summarize_period_costs(rows, *, start: date, end: date, project_ids: list[int] | None = None,
                           project_names: dict | None = None) -> dict:
    """Sum each ledger row once for the SAST period. Do not add provider and resource on top of blended."""
    names = project_names or {}
    selected = set(project_ids) if project_ids else None
    buckets: dict[int, dict] = {}
    entries = 0
    for row in rows or []:
        day = str(row.get("day_sast") or "")
        if not day or day < start.isoformat() or day > end.isoformat():
            continue
        raw_pid = row.get("project_id")
        bucket = UNASSIGNED_ID if raw_pid is None else int(raw_pid)
        if selected is not None and bucket not in selected:
            continue
        if row.get("blended_cost_usd") is not None:
            cost = float(row.get("blended_cost_usd") or 0.0)
        else:
            cost = float(row.get("provider_cost_usd") or 0.0) + float(row.get("resource_cost_usd") or 0.0)
        slot = buckets.setdefault(bucket, {"project_id": bucket, "cost_usd": 0.0, "entries": 0})
        slot["entries"] += 1
        slot["cost_usd"] = round(slot["cost_usd"] + cost, 8)
        entries += 1
    projects = []
    for bucket, slot in buckets.items():
        known = names.get(bucket)
        if bucket == UNASSIGNED_ID:
            known = known or UNASSIGNED_NAME
        projects.append({**slot, "name": known, "cost_usd": round(slot["cost_usd"], 8)})
    projects.sort(key=lambda item: (-item["cost_usd"], item["name"] or ""))
    return {
        "available": True,
        "currency": "USD",
        "counting": (
            "Each cost ledger row is added once. Provider and resource amounts are not added again "
            "on top of the blended total. Rows with no project are Unassigned. Missing rows are not estimated."
        ),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "entries": entries,
        "total_cost_usd": round(sum(item["cost_usd"] for item in projects), 8),
        "by_project": projects,
    }


def _read_cost_rows() -> list[dict]:
    """Read ledger rows through the existing cost table. No provider calls."""
    from sqlalchemy import text

    from distr.core.cost_ledger.pricing import blended_cost_usd
    from distr.core.db import engine

    # Do not create the ledger from a report read. A missing table is "not available".
    with engine.connect() as conn:
        stored = conn.execute(text("SELECT * FROM cost_ledger_entries")).mappings().all()
    rows = []
    for stored_row in stored:
        data = dict(stored_row)
        data["blended_cost_usd"] = blended_cost_usd(data.get("provider_cost_usd"), data.get("resource_cost_usd"))
        data["day_sast"] = ledger_day_sast(data.get("recorded_at"))
        rows.append(data)
    return rows


def safe_period_costs(snapshot: dict, project_ids: list[int] | None) -> dict:
    """Period cost summary, or an explicit unavailable result. Never a fake zero success."""
    period = snapshot.get("period") or {}
    try:
        start = date.fromisoformat(period["start"])
        end = date.fromisoformat(period["end"])
        rows = _read_cost_rows()
    except Exception as exc:  # noqa: BLE001 - surface the failure, do not invent costs
        return {"available": False, "reason": f"Cost ledger could not be read ({type(exc).__name__}).",
                "entries": 0, "by_project": [], "total_cost_usd": None, "currency": "USD"}
    names = {}
    for project in snapshot.get("projects") or []:
        try:
            names[int(project["id"])] = project.get("name")
        except (KeyError, TypeError, ValueError):
            continue
    return summarize_period_costs(rows, start=start, end=end, project_ids=project_ids, project_names=names)


def attach_report_extras(snapshot: dict, project_ids: list[int] | None) -> dict:
    """Add the analysis-route verdict and period costs onto a live snapshot."""
    snapshot["analysis_route"] = report_route_verdict()
    snapshot["costs"] = safe_period_costs(snapshot, project_ids)
    return snapshot


def get_period_report(period: Period, project_ids: list[int] | None, **kwargs) -> dict:
    """Live collation plus the newest saved analysis for this period, projects and automation choice."""
    snapshot = collate(period, project_ids, **kwargs)
    snapshot["saved"] = latest_report(period, project_ids, kwargs.get("get_session"),
                                      include_automations=kwargs.get("include_automations", False))
    snapshot["default_model"] = default_model()
    snapshot["period_options"] = period_options()
    attach_report_extras(snapshot, project_ids)
    return snapshot


def build_period_report(period: Period, project_ids: list[int] | None, *, provider: str | None = None,
                        model: str | None = None, include_automations: bool = False) -> dict:
    verdict = report_route_verdict(provider, model)
    if verdict["blocked_unverified_label"]:
        raise ValueError(verdict["limitation"])
    provider = verdict["selected"]["provider"]
    model = verdict["selected"]["model"]
    if not provider or not model:
        raise ValueError("No default planning model is configured. Pick a provider and model for the report.")
    snapshot = collate(period, project_ids, include_automations=include_automations)
    if not snapshot["blocks"]:
        analysis = {"summary": "No recorded work was found for this period and project selection.",
                    "projects": [], "notes": [s["label"] + ": no data" for s in snapshot["sources"] if s["state"] != "data"][:4]}
    else:
        analysis = run_analysis(snapshot, provider=provider, model=model,
                                project_hint=(project_ids or [None])[0])
    saved = save_report(snapshot, analysis, provider=provider, model=model)
    snapshot["saved"] = saved
    snapshot["default_model"] = {"provider": verdict["configured"]["provider"], "model": verdict["configured"]["model"]}
    snapshot["period_options"] = period_options()
    snapshot["analysis_route"] = verdict
    snapshot["costs"] = safe_period_costs(snapshot, project_ids)
    return snapshot


# ---------------------------------------------------------------- PDF

def _fmt_hours(seconds) -> str:
    seconds = int(seconds or 0)
    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


PALETTE = ["#f97316", "#38bdf8", "#a78bfa", "#22c55e", "#f43f5e", "#eab308", "#14b8a6", "#ec4899", "#8b5cf6",
           "#84cc16", "#06b6d4", "#fb7185"]


def project_color(project_id: int) -> str:
    return "#94a3b8" if int(project_id) == UNASSIGNED_ID else PALETTE[int(project_id) % len(PALETTE)]


def render_pdf(snapshot: dict) -> bytes:
    """Readable A4 report: period, totals, project pie, AI analysis, daily totals, sources."""
    from io import BytesIO

    from reportlab.graphics.charts.piecharts import Pie
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small", fontSize=8.5, leading=11)
    body = styles["BodyText"].clone("body", fontSize=10, leading=13.5)
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm,
                            bottomMargin=14 * mm, title=f"Work report {snapshot['period']['label']}")
    story = []
    period = snapshot["period"]
    names = [p["name"] for p in snapshot.get("projects", []) if p["id"] in set(snapshot.get("project_ids") or [])]
    story.append(Paragraph("Work report", styles["Title"]))
    story.append(Paragraph(escape(f"{period['label']} · {period['start']} to {period['end']} (Africa/Johannesburg)"), body))
    story.append(Paragraph(escape("Projects: " + (", ".join(names) if names else "All projects")), body))
    totals = snapshot["totals"]
    story.append(Paragraph(escape(f"Active time {_fmt_hours(totals['seconds'])} · {totals['blocks']} work blocks · "
                                  f"{totals['projects']} projects · {totals['active_days']} active days"), body))
    story.append(Spacer(1, 6 * mm))

    rows = [r for r in snapshot["by_project"] if r["seconds"] > 0]
    if rows:
        drawing = Drawing(170 * mm, 62 * mm)
        pie = Pie()
        pie.x, pie.y, pie.width, pie.height = 6 * mm, 4 * mm, 54 * mm, 54 * mm
        pie.data = [r["seconds"] for r in rows]
        pie.labels = None
        pie.slices.strokeColor = colors.white
        pie.slices.strokeWidth = 0.5
        for i, row in enumerate(rows):
            pie.slices[i].fillColor = colors.HexColor(project_color(row["project_id"]))
        drawing.add(pie)
        story.append(drawing)
        total = sum(r["seconds"] for r in rows) or 1
        table = Table([["", "Project", "Active time", "Share", "Days"]] + [
            ["■", r["name"], _fmt_hours(r["seconds"]), f"{100 * r['seconds'] / total:.0f}%", str(r["days"])]
            for r in rows], colWidths=[6 * mm, 80 * mm, 30 * mm, 20 * mm, 18 * mm])
        style = [("FONTSIZE", (0, 0), (-1, -1), 9), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                 ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.grey), ("ALIGN", (2, 0), (-1, -1), "RIGHT")]
        for i, row in enumerate(rows, start=1):
            style.append(("TEXTCOLOR", (0, i), (0, i), colors.HexColor(project_color(row["project_id"]))))
        table.setStyle(TableStyle(style))
        story.append(table)
    else:
        story.append(Paragraph("No recorded work in this period.", body))
    story.append(Spacer(1, 6 * mm))

    saved = snapshot.get("saved")
    story.append(Paragraph("Analysis", styles["Heading2"]))
    if saved and saved.get("analysis"):
        analysis = saved["analysis"]
        built = saved.get("created_at") or ""
        stamp = _parse_ts(built) if built else None
        if stamp is not None:
            built = stamp.replace(tzinfo=UTC).astimezone(SAST).strftime("%-d %b %Y %H:%M SAST")
        story.append(Paragraph(escape(f"Built {built} with {saved.get('provider')} / {saved.get('model')}"), small))
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(escape(analysis.get("summary") or ""), body))
        for project in analysis.get("projects") or []:
            story.append(Paragraph(escape(f"{project['name']} — {project.get('hours', 0)} h"), styles["Heading4"]))
            for item in project.get("highlights") or []:
                story.append(Paragraph("• " + escape(item), body))
        if analysis.get("notes"):
            story.append(Paragraph("Notes", styles["Heading4"]))
            for note in analysis["notes"]:
                story.append(Paragraph("• " + escape(note), body))
    else:
        story.append(Paragraph("No analysis has been built for this period and project selection yet. Use Build report.", body))
    story.append(Spacer(1, 6 * mm))

    if snapshot["by_day"]:
        story.append(Paragraph("Daily active time", styles["Heading2"]))
        name_by_id = {str(r["project_id"]): r["name"] for r in snapshot["by_project"]}
        day_rows = [["Date", "Active", "Projects"]]
        for day in snapshot["by_day"]:
            parts = sorted(day["projects"].items(), key=lambda kv: -kv[1])
            day_rows.append([day["date"], _fmt_hours(day["seconds"]),
                             Paragraph(escape(", ".join(f"{name_by_id.get(k, k)} {_fmt_hours(v)}" for k, v in parts)), small)])
        table = Table(day_rows, colWidths=[26 * mm, 20 * mm, 132 * mm], repeatRows=1)
        table.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8.5), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                                   ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(table)
        story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("Sources", styles["Heading2"]))
    state_text = {"data": "has data", "empty": "no records in period", "unavailable": "not found on this machine",
                  "not_read": "not read", "excluded": "excluded (Automations off)"}
    src_rows = [["Source", "Blocks", "Time", "Status"]] + [
        [s["label"], str(s["blocks"]), _fmt_hours(s["seconds"]), state_text.get(s["state"], s["state"])]
        for s in snapshot["sources"]]
    table = Table(src_rows, colWidths=[70 * mm, 20 * mm, 26 * mm, 50 * mm])
    table.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8.5), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                               ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.grey)]))
    story.append(table)
    for note in snapshot.get("notes") or []:
        story.append(Paragraph(escape(note), small))
    doc.build(story)
    return buffer.getvalue()
