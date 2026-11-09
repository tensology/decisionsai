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
import hashlib
import os
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")
UNASSIGNED_ID = 0
UNASSIGNED_NAME = "Unassigned"
SESSION_GAP_SECONDS = 20 * 60          # a local CLI session splits into blocks across idle gaps
MIN_BLOCK_SECONDS = 0                  # elapsed time is never padded for invoicing
MAX_BLOCK_SECONDS = 24 * 60 * 60
MAX_CANCELLED_SECONDS = 6 * 60 * 60
MAX_TIMER_SECONDS = 6 * 60 * 60          # a stored thread timer longer than this is stale, not work
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
_SUMMARY_RE = re.compile(r"Summary:\s*(.+)", re.DOTALL)
_STATUS_PREFIX_RE = re.compile(r"^\s*(?:\d{1,3}\s*%\s*)?(?:done|completed|complete|status)\b\s*[:.\-–—]*\s*", re.IGNORECASE)
_MD_HEAD_RE = re.compile(r"^\s*#{1,6}\s*")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1")
_MD_CODE_RE = re.compile(r"`([^`]+)`")
_GREETING_RE = re.compile(r"^(?:hello|hi|hey|good (?:morning|afternoon|evening))\b", re.IGNORECASE)


def clean_description(text: str) -> str:
    """Turn a recorded agent status message into a short, plain one-line description.

    Only strips formatting and status markers from text the session already recorded; it
    never adds or infers content. Returns "" when nothing describable remains.
    """
    value = str(text or "")
    value = _STATUS_PREFIX_RE.sub("", value)
    value = _MD_HEAD_RE.sub("", value)
    value = _MD_LINK_RE.sub(r"\1", value)
    value = _MD_BOLD_RE.sub(r"\2", value)
    value = _MD_CODE_RE.sub(r"\1", value)
    value = value.replace("**", "").replace("__", "").replace("`", "")
    line = next((candidate.strip() for candidate in value.splitlines() if candidate.strip()), "")
    line = " ".join(line.split())
    if not line or _GREETING_RE.match(line):
        return ""
    cut = len(line)
    for separator, extra in ((". ", 1), (" - ", 0), (" — ", 0), (" | ", 0)):
        start = 0
        while True:
            index = line.find(separator, start)
            if index == -1:
                break
            if index >= 12:
                cut = min(cut, index + extra)
                break
            start = index + 1
    line = line[:cut].rstrip(" :;,-–—")
    if len(line) > 100:
        line = line[:100].rsplit(" ", 1)[0].rstrip(" :;,-–—")
    return line


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
    return os.path.normpath(text) if text else ''


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _tokens(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", str(text or "").lower()) if token]


class ProjectMatcher:
    """Folder-prefix match, then a bounded name/trigger alias match for the same project.

    The alias match only fires when the folder match misses and exactly one project
    claims the working directory (by name, alias phrase, or a distinctive path token),
    so a working directory that two projects could own is never guessed.
    """

    def __init__(self, projects: dict[int, dict]):
        self.folders = sorted(
            ((_norm_path(p.get("folder")), pid) for pid, p in projects.items() if _norm_path(p.get("folder")) not in ("", "/")),
            key=lambda item: len(item[0]), reverse=True,
        )
        self.aliases: dict[str, set[int]] = {}
        self.tokens: dict[str, set[int]] = {}
        for pid, project in projects.items():
            pid = int(pid)
            for phrase in [project.get("name") or "", *(project.get("triggers") or [])]:
                slug = _slug(phrase)
                if len(slug) >= 4:
                    self.aliases.setdefault(slug, set()).add(pid)
                for token in _tokens(phrase):
                    if len(token) >= 5:
                        self.tokens.setdefault(token, set()).add(pid)

    def match(self, cwd: str | None) -> int | None:
        path = _norm_path(cwd)
        if not path:
            return None
        for folder, pid in self.folders:
            if path == folder or path.startswith(folder + "/"):
                return pid
        # The working directory is an ancestor of a project folder (a sub-repo, a renamed
        # checkout, or a folder beside the registered one). Only trust it when one project fits.
        under = {pid for folder, pid in self.folders if folder.startswith(path + "/")}
        if len(under) == 1:
            return next(iter(under))
        base = path.rsplit("/", 1)[-1]
        hits = set(self.aliases.get(_slug(base), ()))
        if not hits:
            for token in _tokens(base):
                hits |= self.tokens.get(token, set())
        return next(iter(hits)) if len(hits) == 1 else None


# Working directories that are not real project work: throwaway test, benchmark, sandbox and
# Decisions' own internal orchestration folders.
_IGNORED_CWD_MARKERS = (
    "/private/tmp/",
    "/decisionsai-local-worker-proof",
    "/spotify-remake-e2e",
    "/decisions-live-provider-matrix",
    "/decisions-phase7-live",
    "/sandbox/",
    "/.decisions/work/",
)


def _is_test_cwd(cwd: str | None) -> bool:
    text = str(cwd or "")
    if "/pytest-of-" in text or ("/pytest-" in text and "/T/" in text):
        return True
    return any(marker in text for marker in _IGNORED_CWD_MARKERS)


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

def _trigger_words(raw) -> list[str]:
    if not raw:
        return []
    try:
        loaded = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    if not isinstance(loaded, (list, tuple)):
        return []
    return [str(item).strip() for item in loaded if str(item).strip()]


def _git_commit_subjects(folder: str, lo: datetime, hi: datetime) -> list[tuple[datetime, str]]:
    """Subjects of commits in one repo between lo and hi (naive UTC). Read-only, bounded.

    Used only to describe work the repo already records; a missing repo or no commits
    returns nothing and the caller keeps the session's own description.
    """
    path = Path(folder)
    if not folder or not (path / ".git").is_dir():
        return []
    since = (lo - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    until = (hi + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "log", "--all", "--no-merges",
             f"--since={since}", f"--until={until}", "--pretty=%at%x09%s"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    commits: list[tuple[datetime, str]] = []
    for line in proc.stdout.splitlines():
        stamp, _, subject = line.partition("\t")
        subject = subject.strip()
        try:
            when = datetime.fromtimestamp(int(stamp), tz=UTC).replace(tzinfo=None)
        except (ValueError, OSError, OverflowError):
            continue
        if subject:
            commits.append((when, subject))
    commits.sort()
    return commits


def load_projects(get_session=None) -> dict[int, dict]:
    from distr.core.db.projects import Project
    if get_session is None:
        from distr.core.reports import service
        get_session = service.get_session
    with get_session() as db:
        return {int(p.id): {"id": int(p.id), "name": p.name or f"Project {p.id}", "folder": p.folder_location or "",
                            "triggers": _trigger_words(getattr(p, "additional_trigger_words", None))}
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

        def timer_project(entry, item):
            # Chat and parent assignment win. A work-item project is used only when those are empty.
            chat_pid = project_for_chat(entry.chat_id)
            if chat_pid is not None:
                return chat_pid
            if item is not None and item.created_at and item.created_at > entry.started_at:
                # Deleted SQLite row ids can be reused by newer work items. They cannot
                # establish ownership of an older timer whose original chat is gone.
                return None
            raw = getattr(item, "project_id", None) if item is not None else None
            if raw is None or isinstance(raw, bool):
                return None
            try:
                candidate = int(raw)
            except (TypeError, ValueError):
                return None
            if candidate <= 0:
                return None
            return candidate

        rows = (db.query(DevelopmentTimeEntry, DevelopmentWorkItem, Chat)
                .outerjoin(DevelopmentWorkItem, DevelopmentTimeEntry.work_item_id == DevelopmentWorkItem.id)
                .outerjoin(Chat, Chat.id == DevelopmentTimeEntry.chat_id)
                .filter(DevelopmentTimeEntry.source != "reset",
                        DevelopmentTimeEntry.started_at < hi, DevelopmentTimeEntry.ended_at > lo)
                .all())
        for entry, item, chat in rows:
            if (entry.ended_at - entry.started_at).total_seconds() > MAX_TIMER_SECONDS:
                counts["stale_thread_timer"] += 1
                continue
            seconds = _record_seconds(entry.started_at, entry.ended_at, None)
            if not seconds:
                continue
            pid = timer_project(entry, item)
            future_item = bool(item is not None and item.created_at and item.created_at > entry.started_at)
            # A timer whose thread row is gone and whose work item names no project cannot
            # be attributed to any client, so it is dropped instead of being shown as an
            # ownerless "Thread N" block.
            if chat is None and pid is None and not (future_item and item.project_id):
                counts["orphaned_thread_timer"] += 1
                continue
            title = (item.ticket_title if item is not None and not future_item and item.ticket_title else None) or (
                chat.title if chat is not None and chat.title else f"Thread {entry.chat_id}")
            blocks.append(_block("decisions_thread", "decisions", "Decisions thread timer", entry.started_at,
                                 entry.ended_at, pid, title, f"chat:{entry.chat_id}"))
            if future_item and chat is None:
                blocks[-1]['project_provenance'] = 'historical_metadata_conflict'
            counts["decisions_thread"] += 1

        try:
            from distr.core.db.schedule_blocks import ScheduleBlock
            for block in (db.query(ScheduleBlock)
                          .filter(ScheduleBlock.is_timer_entry.is_(True), ScheduleBlock.start_at < hi, ScheduleBlock.end_at > lo)
                          .all()):
                if _record_seconds(block.start_at, block.end_at, None):
                    if (block.end_at - block.start_at).total_seconds() > MAX_TIMER_SECONDS:
                        counts["stale_thread_timer"] += 1
                        continue
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
            chat_id = run.chat_id or definition.chat_id
            pid = project_for_chat(chat_id)
            if chat_id and pid is None and db.get(Chat, int(chat_id)) is None:
                counts["orphaned_workflow_run"] += 1
                continue
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
            block = _block(source, family, label, session.started_at, end, session.project_id, title,
                           f"execution:{session.id}")
            try:
                packet = json.loads(session.input_packet or '{}')
            except ValueError:
                packet = {}
            instruction = str(packet.get('instruction') or '') if isinstance(packet, dict) else ''
            if instruction:
                block['instruction_hash'] = hashlib.sha256(' '.join(instruction.split()).encode()).hexdigest()
            blocks.append(block)
            counts[source] += 1
    return blocks, dict(counts)


# ---------------------------------------------------------------- local CLI session files

_CD_WORKDIR_RE = re.compile(r'''(?:^|[;&|\n])\s*cd\s+(?:--\s+)?(?:"([^"]+)"|'([^']+)'|([^\s;&|]+))''')


def _recorded_workdirs(arguments) -> list[str]:
    """Explicit local command workspaces, not paths mentioned in tool output or prose."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return []
    if not isinstance(arguments, dict):
        return []
    paths = [arguments[k] for k in ('cwd', 'workdir') if isinstance(arguments.get(k), str)]
    command = arguments.get('command') or arguments.get('cmd') or ''
    for part in command if isinstance(command, list) else [command]:
        if isinstance(part, str):
            paths.extend(next(p for p in match.groups() if p) for match in _CD_WORKDIR_RE.finditer(part))
    return [path for path in paths if path.startswith('/')]


def _recorded_activity(row: dict, kind: str, stamp: datetime) -> dict | None:
    def text(content):
        if isinstance(content, str):
            return content
        return '\n'.join(str(p.get('text') or '') for p in (content or [])
                         if isinstance(p, dict) and p.get('type') in {'text', 'input_text', 'output_text'})
    role, body, workdirs = '', '', []
    if kind == 'pi':
        message = row.get('message') or {}
        if isinstance(message, dict):
            role = message.get('role') or ''
            content = message.get('content')
            if role in {'user', 'assistant'}:
                body = text(content)
            for part in content if isinstance(content, list) else []:
                if isinstance(part, dict) and part.get('type') == 'toolCall':
                    workdirs.extend(_recorded_workdirs(part.get('arguments')))
    else:
        payload = row.get('payload') or {}
        if not isinstance(payload, dict):
            return None
        ptype = payload.get('type')
        if row.get('type') == 'turn_context' and payload.get('cwd'):
            workdirs = [str(payload['cwd'])]
        elif ptype in {'function_call', 'custom_tool_call'}:
            workdirs = _recorded_workdirs(payload.get('arguments'))
        elif ptype == 'message':
            role, body = payload.get('role') or '', text(payload.get('content'))
        elif ptype in {'user_message', 'agent_message', 'task_complete'}:
            role = 'user' if ptype == 'user_message' else 'assistant'
            body = str(payload.get('message') or payload.get('last_agent_message') or '')
    if body or workdirs:
        return {'time': stamp, 'role': role, 'text': body[:8000], 'workdirs': workdirs,
                'instruction_hash': hashlib.sha256(' '.join(body.split()).encode()).hexdigest()
                if role == 'user' and body else None}
    return None


def session_interval_context(meta: dict, start: datetime, end: datetime, matcher: ProjectMatcher) -> dict:
    """Attribute only from activity inside this span, never from another interval's ending."""
    events = [event for event in meta.get('activity', []) if start <= event['time'] <= end]
    folders = getattr(matcher, 'folders', ())
    paths = []
    unregistered = []
    for event in events:
        for path in event['workdirs']:
            normalized = _norm_path(path)
            matches = [(folder, pid) for folder, pid in folders
                       if normalized == folder or normalized.startswith(folder + '/')]
            if matches:
                longest = max(len(folder) for folder, _ in matches)
                owners = {pid for folder, pid in matches if len(folder) == longest}
                if len(owners) == 1:
                    paths.append((next(iter(owners)), path))
            else:
                children = {pid for folder, pid in folders if folder.startswith(normalized + '/')}
                if len(children) == 1:
                    paths.append((next(iter(children)), path))
                elif '/development/' in normalized and not (normalized == _norm_path(meta.get('cwd')) and len(children) > 1):
                    unregistered.append(path)
    owners = {pid for pid, _ in paths}
    cwd = str(meta.get('cwd') or '')
    provenance = 'session_folder'
    if len(owners) == 1 and not unregistered:
        pid, cwd = paths[-1]
        provenance = 'recorded_workdir'
    elif len(owners) > 1 or (owners and unregistered):
        pid, cwd, provenance = None, '', 'mixed_workspaces'
    elif unregistered:
        pid, cwd, provenance = None, unregistered[-1], 'unregistered_workdir'
    else:
        normalized = _norm_path(cwd)
        descendants = {pid for folder, pid in folders if folder.startswith(normalized + '/')}
        # An organization-level launch folder is not evidence that all later work belongs to it.
        pid = None if len(descendants) > 1 else matcher.match(cwd)
    summaries, assistants, users = [], [], []
    for event in events:
        if event['role'] == 'assistant':
            found = _SUMMARY_RE.search(event['text'])
            if found:
                summaries.append(found.group(1))
            assistants.append(event['text'])
        elif event['role'] == 'user':
            # Clipboard file paths are not descriptions of the work.
            lines = [line for line in event['text'].splitlines() if not line.strip().startswith(('/var/folders/', '/private/var/folders/'))]
            users.append('\n'.join(lines))
    title = next((value for raw in [*reversed(summaries), *reversed(assistants), *users]
                  if (value := clean_description(raw))), '')
    instruction_hash = next((event.get('instruction_hash') for event in events
                             if event['role'] == 'user' and event.get('instruction_hash')), None)
    return {'project_id': pid, 'working_directory': cwd, 'project_provenance': provenance,
            'title': title, 'instruction_hash': instruction_hash}


def _session_activity(path: Path, *, kind: str) -> tuple[dict, list[datetime]]:
    """Return (meta, timestamps) for one JSONL session file without loading it whole."""
    meta: dict = {"cwd": "", "prompt": "", "summary": "", "last_assistant": "", "task_complete": "", "id": path.stem}
    meta['activity'] = []
    stamps: list[datetime] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for index, line in enumerate(handle):
            head = line[:400]
            match = _TS_RE.search(head)
            if match:
                stamp = _parse_ts(match.group(1))
                if stamp:
                    stamps.append(stamp)
                    if index:
                        try:
                            event = json.loads(line)
                        except ValueError:
                            event = {}
                        activity = _recorded_activity(event, kind, stamp) if isinstance(event, dict) else None
                        if activity:
                            meta['activity'].append(activity)
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
            # Recorded outcome/description: the session's own final account of the work.
            # Read on a full pass (not limited to the first 400 lines) and never invented.
            if kind == "pi" and '"role":"assistant"' in line.replace(" ", ""):
                if "Summary:" in line or '"type":"text"' in line.replace(" ", ""):
                    try:
                        message = json.loads(line).get("message") or {}
                    except ValueError:
                        message = {}
                    content = message.get("content")
                    if isinstance(content, list):
                        content = "\n".join(str(block.get("text") or "") for block in content if isinstance(block, dict))
                    text = str(content or "").strip()
                    if text:
                        meta["last_assistant"] = text
                        found = _SUMMARY_RE.search(text)
                        if found:
                            meta["summary"] = " ".join(found.group(1).split())
            elif kind == "codex" and ('"task_complete"' in head or '"output_text"' in line or '"agent_message"' in line):
                try:
                    payload = json.loads(line).get("payload") or {}
                except ValueError:
                    payload = {}
                ptype = payload.get("type")
                if ptype == "task_complete":
                    text = str(payload.get("last_agent_message") or "").strip()
                    if text:
                        meta["task_complete"] = text
                elif ptype == "agent_message" or '"output_text"' in line:
                    parts = [part.get("text") for part in (payload.get("content") or []) if isinstance(part, dict)]
                    text = "\n".join(str(part or "") for part in parts).strip()
                    if text:
                        if "Payload:" in text:
                            text = text.split("Payload:", 1)[1].strip()
                        meta["last_assistant"] = text
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
                meta['instruction_hash'] = hashlib.sha256(' '.join(str(content or '').split()).encode()).hexdigest()
            elif kind == "codex" and '"user_message"' in head:
                try:
                    payload = json.loads(line).get("payload") or {}
                except ValueError:
                    continue
                meta["prompt"] = " ".join(str(payload.get("message") or "").split())[:MAX_PROMPT_CHARS]
                meta['instruction_hash'] = hashlib.sha256(' '.join(str(payload.get('message') or '').split()).encode()).hexdigest()
    return meta, sorted(stamps)


def activity_intervals(stamps: list[datetime], gap_seconds: int = SESSION_GAP_SECONDS) -> list[tuple[datetime, datetime]]:
    """Split recorded event times across idle gaps without inventing elapsed time."""
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
    return [(a, b) for a, b in intervals if b > a]


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
            folder = Path(meta["cwd"]).name if meta["cwd"] else ""
            fallback_title = f"{label} in {folder}" if folder else label
            used = False
            for start, end in activity_intervals(stamps):
                if _clip(start, end, lo, hi):
                    context = session_interval_context(meta, start, end, matcher)
                    block = _block(source, family, label, start, end, context['project_id'],
                                   context['title'] or fallback_title, f"{source}:{meta['id']}")
                    block.update({key: context[key] for key in ('working_directory', 'project_provenance')})
                    block['instruction_hash'] = context['instruction_hash']
                    blocks.append(block)
                    used = True
            if used:
                counts[source] += 1
    return blocks, dict(counts), status


# ---------------------------------------------------------------- collation

def combine_duplicate_runs(blocks):
    """One run recorded by Decisions and a CLI is one work entry, with both references kept.

    Matching titles or overlapping times alone never establish identity. Require the
    exact instruction, same harness, compatible owner, near-identical bounds and one match.
    """
    from distr.core.reports.entry_descriptions import entry_key
    locals_by_instruction = defaultdict(list)
    for block in blocks:
        if block['source'] in {'pi_local', 'codex_local'} and block.get('instruction_hash'):
            locals_by_instruction[(block['family'], block['instruction_hash'])].append(block)
    claimed = set()
    replacements = {}
    for block in blocks:
        if not str(block.get('ref') or '').startswith('execution:') or not block.get('instruction_hash'):
            continue
        candidates = []
        for local in locals_by_instruction.get((block['family'], block['instruction_hash']), []):
            if id(local) in claimed or local['project_id'] not in (None, block['project_id']):
                continue
            overlap = (min(local['end'], block['end'])-max(local['start'], block['start'])).total_seconds()
            shorter = min((local['end']-local['start']).total_seconds(), (block['end']-block['start']).total_seconds())
            if overlap <= 0 or shorter <= 0 or overlap/shorter < .9:
                continue
            if abs((local['start']-block['start']).total_seconds()) <= 5 and abs((local['end']-block['end']).total_seconds()) <= 5:
                candidates.append(local)
        if len(candidates) != 1:
            continue
        local = candidates[0]
        # A local record must not also match another execution of the same instruction.
        rivals = [other for other in blocks if other is not block and str(other.get('ref') or '').startswith('execution:')
                  and other.get('instruction_hash') == block['instruction_hash'] and other['family'] == block['family']
                  and abs((other['start']-local['start']).total_seconds()) <= 5
                  and abs((other['end']-local['end']).total_seconds()) <= 5]
        if rivals:
            continue
        claimed.add(id(local))
        replacements[id(block)] = {**block, 'description_key': entry_key(block),
            'related_description_keys': [entry_key(local)],
            'start': min(block['start'], local['start']), 'end': max(block['end'], local['end']),
            'related_refs': [local['ref']], 'thread_ref': local['ref'],
            'working_directory': local.get('working_directory'), 'project_provenance': 'linked_execution'}
    return [replacements.get(id(block), block) for block in blocks if id(block) not in claimed]

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
            codex_root=None, include_local=True, include_automations=False, combine_duplicates=True,
            invoice_only=False) -> dict:
    lo, hi = period.bounds_utc()
    projects = load_projects(get_session)
    matcher = ProjectMatcher(projects)
    raw, db_counts = collect_database_blocks(lo, hi, get_session)
    local_counts, local_status = {}, {}
    if include_local:
        local, local_counts, local_status = collect_local_sessions(lo, hi, matcher, pi_root=pi_root, codex_root=codex_root)
        raw.extend(local)
    from distr.core.reports.entry_reviews import apply_project_reviews, apply_invoice_reviews
    if get_session is None:
        apply_project_reviews(raw, projects)
    # Explicit assignments apply before filtering or combining source records.
    try:
        from distr.core.reports.projection_store import manual_project_map
        manual = manual_project_map(get_session)
    except Exception:
        manual = {}
    for block in raw:
        override = manual.get((block['source'], block.get('ref') or ''))
        if override is not None:
            block['project_id'] = override if override in projects else UNASSIGNED_ID
            block['manual_project_override'] = True
    if combine_duplicates:
        raw = combine_duplicate_runs(raw)
    if get_session is None:
        raw = apply_invoice_reviews(raw, projects)
    from distr.core.reports.projection_store import manual_interval_project_map, _stamp
    interval_choices = manual_interval_project_map(get_session)
    for block in raw:
        choice = interval_choices.get((block['source'], block['ref'], _stamp(block['start']), _stamp(block['end'])))
        if choice is not None and choice != block['project_id']:
            block.update(project_id=choice if choice in projects else 0, project_provenance='manual',
                         invoice_status='needs_review', description_status='needs_review',
                         invoice_reason='Project changed after the evidence review.')
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

    from distr.core.reports.entry_descriptions import apply_descriptions
    if get_session is None:
        apply_descriptions(blocks)
        from distr.core.reports.entry_reviews import apply_billing_policy
        apply_billing_policy(blocks)
    invoice_summary = {
        'ready': sum(invoice_eligible(b) for b in blocks),
        'non_billable': sum(b.get('invoice_status') == 'non_billable' for b in blocks),
        'needs_review': sum(not invoice_eligible(b) and b.get('invoice_status') != 'non_billable' for b in blocks),
    }
    if invoice_only:
        blocks = [b for b in blocks if invoice_eligible(b)]

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
        if invoice_only and pid in {7, 10}:
            return "Spoortrack [old]" if pid == 7 else "Spoortrack [new]"
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
                "in recorded time. Matching Decisions and CLI records with identical instructions and "
                "near-identical bounds are combined, retaining both source references."
            ),
        },
        "collected_at": _utc_iso(datetime.now(UTC).replace(tzinfo=None)),
        "invoice_only": invoice_only, "invoice_summary": invoice_summary,
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
             "seconds": int((b["end"] - b["start"]).total_seconds()), "ref": b["ref"],
             "source_start": b['job_start'].isoformat() + 'Z', "source_end": b['job_end'].isoformat() + 'Z',
             "description_status": b.get('description_status'),
             "invoice_status": b.get('invoice_status'), "invoice_reason": b.get('invoice_reason'),
             "invoice_reviewed_at": b.get('invoice_reviewed_at'),
             "invoice_evidence": b.get('invoice_evidence', []), "review_parent_key": b.get('review_parent_key'),
             "invoice_source_missing": b.get('invoice_source_missing', False),
             "invoice_source_note": b.get('invoice_source_note'), "invoice_source_excerpt": b.get('invoice_source_excerpt'),
             "billing_project_id": b.get('billing_project_id'), "billing_policy_asof": b.get('billing_policy_asof'),
             "invoice_rate_override": b.get('invoice_rate_override'),
             "invoice_strikethrough": b.get('invoice_strikethrough', False),
             "description_asof": b.get('description_asof'),
             "recorded_title": b.get('recorded_title'),
             "description_key": b.get('description_key'), "related_refs": b.get('related_refs', []),
             "thread_ref": b.get('thread_ref'),
             "working_directory": b.get('working_directory'), "project_provenance": b.get('project_provenance')}
            for i, b in enumerate(blocks)
        ],
        "notes": [
            "The same run recorded by Decisions and a local CLI is combined only when its instruction, harness and time bounds agree unambiguously. Both source references remain available.",
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
            *([f"{db_counts.get('orphaned_thread_timer', 0)} thread timer(s) referenced deleted threads and were omitted as unattributable."]
              if db_counts.get("orphaned_thread_timer") else []),
            *([f"{db_counts.get('orphaned_workflow_run', 0)} workflow run(s) referenced deleted threads and were omitted as unattributable."]
              if db_counts.get("orphaned_workflow_run") else []),
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


def invoice_eligible(block) -> bool:
    """A reviewed description is necessary; unsupported, internal-test and excluded time stays out."""
    return (bool(block.get('project_id')) and block.get('description_status') == 'ready'
            and block.get('invoice_status') not in {'non_billable', 'needs_review'})


# Client-facing wording. The report can ship to clients, so work is described with steady
# progress verbs and technical failure language is softened out of the highlights.
_CLIENT_WORDING = (
    (re.compile(r"\bfixed\b", re.I), "updated"),
    (re.compile(r"\bfixes\b", re.I), "updates"),
    (re.compile(r"\bfixing\b", re.I), "updating"),
    (re.compile(r"\bfix\b", re.I), "update"),
    (re.compile(r"\bbugs\b", re.I), "issues"),
    (re.compile(r"\bbug\b", re.I), "issue"),
    (re.compile(r"\berrors\b", re.I), "issues"),
    (re.compile(r"\berror\b", re.I), "issue"),
    (re.compile(r"\bfailures\b", re.I), "improvements"),
    (re.compile(r"\bfailure\b", re.I), "improvement"),
    (re.compile(r"\bbroken\b", re.I), "restored"),
    (re.compile(r"\bcrash(?:es|ed)?\b", re.I), "stability issue"),
)


def _soften_wording(text: str) -> str:
    """Rewrite client-hostile words in a work description without changing its meaning."""
    result = str(text or "")
    for pattern, replacement in _CLIENT_WORDING:
        def _swap(match, _replacement=replacement):
            if match.group(0)[:1].isupper():
                return _replacement[:1].upper() + _replacement[1:]
            return _replacement
        result = pattern.sub(_swap, result)
    return result


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
    f"{REPORT_MARKER} You write a concise client-facing work report for one person's software work over a period. "
    "Use only the supplied data. Do not invent tasks, hours or sources. Hours are already measured; quote them as given. "
    "Describe the work that was carried out in plain, steady business language. Write each highlight as a short phrase "
    "that opens with a past-tense verb of progress: Updated, Modified, Enhanced, Improved, Added, Extended, Refined, "
    "Adjusted, Streamlined, Refreshed, Deployed, Prepared, Delivered or Reviewed. "
    "Preserve the factual meaning of each work description. Keep investigation, proposed work and completed changes "
    "distinct. Do not turn an unsuccessful attempt into a delivered fix or hide a limitation through euphemisms. "
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
    snapshot = collate(period, project_ids, include_automations=include_automations, invoice_only=True)
    if not snapshot["blocks"]:
        analysis = {"summary": "No reviewed invoice work was found for this period and project selection.",
                    "projects": [], "notes": [s["label"] + ": no data" for s in snapshot["sources"] if s["state"] != "data"][:4]}
    else:
        analysis = run_analysis(snapshot, provider=provider, model=model,
                                project_hint=(project_ids or [None])[0])
    coverage = snapshot.get('invoice_summary', {})
    excluded = coverage.get('non_billable', 0) + coverage.get('needs_review', 0)
    if excluded:
        analysis.setdefault('notes', []).append(f"Excluded {excluded} non-billable or unverified activity entries from report hours.")
    if any(b.get('invoice_status') == 'zero_rate' for b in snapshot['blocks']):
        analysis.setdefault('notes', []).append('Multisnack time is tracked on the Crystal Logic invoice, struck through at R0, with no charge.')
    saved = save_report(snapshot, analysis, provider=provider, model=model)
    # The calendar remains a complete activity record; the saved client report uses reviewed work only.
    snapshot = collate(period, project_ids, include_automations=include_automations)
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
    styles["Heading2"].keepWithNext = True
    styles["Heading4"].keepWithNext = True
    small = styles["BodyText"].clone("small", fontSize=8.5, leading=11)
    body = styles["BodyText"].clone("body", fontSize=10, leading=13.5)
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm,
                            bottomMargin=14 * mm, title=f"Work report {snapshot['period']['label']}")
    story = []
    period = snapshot["period"]
    display_names = {p["project_id"]: p["name"] for p in snapshot.get("by_project", [])}
    names = [display_names.get(p["id"], p["name"]) for p in snapshot.get("projects", [])
             if p["id"] in set(snapshot.get("project_ids") or [])]
    story.append(Paragraph("Work report", styles["Title"]))
    story.append(Paragraph(escape(f"{period['label'].replace(chr(8211), '-').replace(chr(8212), '-')} | {period['start']} to {period['end']} (Africa/Johannesburg)"), body))
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
            story.append(Paragraph(escape(f"{project['name']} - {_fmt_hours(float(project.get('hours', 0)) * 3600)}"), styles["Heading4"]))
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
