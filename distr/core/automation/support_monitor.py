"""Support incoming monitor + stale proposals, as a DecisionsAI automation tool.

Follows ~/TensologyTeam/handoffs/agents/support/MONITOR.md. Read-only:
list/count only, never send/reply/label/delete/mark-read, never print secrets,
never speak. Runs under ``read_only_scope`` (see read_only.py); the only write
is the state file configured in ``action_config.read_only.state_file``.

During parallel verification it uses ``state-decisions.json`` beside
MONITOR.md and seeds ``last_seen`` from the Grok Bot routine's ``state.json``
(read only) on first run, so the two monitors never fight over one file.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")
SUPPORT_DIR = Path.home() / "TensologyTeam/handoffs/agents/support"
DEFAULT_DB = Path(__file__).resolve().parents[3] / "db" / "settings.db"
MAILSHOT_ENV = Path.home() / "TensologyTeam/secrets/mailshot-finance.env"
TUNNEL_PORT = 18000
WINDOW = (7, 20)  # 07:00 <= local < 20:00

_ACTION_RE = re.compile(
    r"\?|invoice|payment|paid|overdue|statement|quote|proposal|contract|legal|bug|error|outage|down\b|not working|broken|urgent|asap|deadline|by \d|reverify|verify|expire|failed|spam|password|cost",
    re.I,
)
_NOISE_FROM = re.compile(r"no-?reply|newsletter|substack|marketing|notifications?@|mailer|digest|news@", re.I)
_OWNER_RULES = [
    (re.compile(r"invoice|payment|paid|statement|bank|capitec|cost", re.I), "Finance"),
    (re.compile(r"legal|contract|nda|terms", re.I), "Legal"),
    (re.compile(r"bug|error|outage|down\b|api|domain|server|deploy|twilio", re.I), "Platform"),
    (re.compile(r"quote|proposal|lead|contact", re.I), "Sales"),
]


def _owner(text: str) -> str:
    for rx, owner in _OWNER_RULES:
        if rx.search(text or ""):
            return owner
    return "Tensology Ops"


def working_days_between(start: datetime, end: datetime) -> float:
    """Working days (Mon-Fri) elapsed between two aware datetimes."""
    if end <= start:
        return 0.0
    total = 0.0
    cur = start
    while cur < end:
        nxt = min(end, (cur + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0))
        if cur.weekday() < 5:
            total += (nxt - cur).total_seconds() / 86400
        cur = nxt
    return total


def _sast(dt: datetime) -> str:
    return dt.astimezone(SAST).strftime("%Y-%m-%d %H:%M SAST")


def _utc_naive(text: Any) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).split(".")[0]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _ro_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    return out


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class SupportMonitor:
    def __init__(
        self,
        *,
        db_path: Path | str = DEFAULT_DB,
        state_file: Path | str = SUPPORT_DIR / "state-decisions.json",
        seed_state_file: Path | str | None = SUPPORT_DIR / "state.json",
        now: datetime | None = None,
        gmail_service: Any = None,
        mailshot_fetch: Callable[[], list[dict[str, Any]]] | None = None,
        port_check: Callable[[], bool] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.state_file = Path(state_file)
        self.seed_state_file = Path(seed_state_file) if seed_state_file else None
        self.now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.gmail_service = gmail_service
        self.gmail_error = ""
        self.mailshot_fetch = mailshot_fetch
        self.port_check = port_check or (lambda: port_open(TUNNEL_PORT))
        self.channel_status: dict[str, str] = {}

    # ---------------------------------------------------------------- state
    def load_state(self) -> dict[str, Any]:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text())
        seed: dict[str, Any] = {}
        if self.seed_state_file and self.seed_state_file.exists():
            src = json.loads(self.seed_state_file.read_text())
            seed = {k: src[k] for k in ("last_seen", "outstanding", "proposal_sources") if k in src}
            seed["seeded_from"] = str(self.seed_state_file.name)
        seed.setdefault("last_seen", {})
        return seed

    # ------------------------------------------------------------- channels
    def _mailshot(self, last: dict[str, Any]) -> tuple[list[dict], dict]:
        if not self.port_check():
            self.channel_status["mailshot"] = f"DOWN: ssh tunnel :{TUNNEL_PORT} not listening (not restarted; needs Paul)"
            return [], last
        try:
            emails = self.mailshot_fetch() if self.mailshot_fetch else self._mailshot_list()
        except Exception as exc:
            self.channel_status["mailshot"] = f"error: {type(exc).__name__}"
            return [], last
        since = str(last.get("ts") or "")
        items, newest = [], dict(last)
        for e in emails:
            ts = str(e.get("date") or "")
            if not ts or ts <= since or e.get("_id") == last.get("id"):
                continue
            name, addr = parseaddr(str(e.get("from") or ""))
            items.append({"channel": "Mailshot", "from": name or addr, "addr": addr, "subject": str(e.get("subject") or "")[:120], "at": datetime.fromisoformat(ts.replace("Z", "+00:00"))})
            if ts > str(newest.get("ts") or ""):
                newest = {"ts": ts, "id": e.get("_id")}
        self.channel_status["mailshot"] = f"ok ({len(emails)} listed)" if emails else "WARNING: inbox list returned 0 messages (check Mailshot)"
        return items, newest

    def _mailshot_list(self) -> list[dict[str, Any]]:
        env = _read_env(MAILSHOT_ENV)
        base = env.get("MAILSHOT_INTERNAL_BASE_URL", "").rstrip("/")
        if not base.startswith(("http://127.0.0.1", "http://localhost")):
            raise RuntimeError("Mailshot base is not the tunnel; refusing public host")
        req = urllib.request.Request(
            f"{base}/api/integrations/v1/messages?folder=inbox&limit=50",
            headers={"Authorization": f"Bearer {env.get('MAILSHOT_TENSOLOGY_API_KEY', '')}", "X-Tensology-Source": "support"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:  # list only: never opens a message
            return list((json.load(resp).get("data") or {}).get("emails") or [])

    def _gmail(self, last: dict[str, Any]) -> tuple[list[dict], dict]:
        if self.gmail_service is None:
            self.channel_status["gmail"] = f"unavailable: {self.gmail_error or 'not connected'}"
            return [], last
        from distr.core.automation import gmail_readonly

        since_ms = 0
        if last.get("ts"):
            since_ms = int(datetime.fromisoformat(str(last["ts"]).replace("Z", "+00:00")).timestamp() * 1000)
        after = max(0, since_ms // 1000 - 3600)
        try:
            msgs = gmail_readonly.search(self.gmail_service, f"in:inbox after:{after}", max_results=25)
        except Exception as exc:
            self.channel_status["gmail"] = f"error: {str(exc)[:120]}"
            return [], last
        items, newest = [], dict(last)
        for m in msgs:
            if m["internal_ms"] <= since_ms or m["id"] == last.get("id"):
                continue
            name, addr = parseaddr(m["from"])
            at = datetime.fromtimestamp(m["internal_ms"] / 1000, timezone.utc)
            items.append({"channel": "Gmail", "from": name or addr, "addr": addr, "subject": m["subject"][:120], "at": at})
            if m["internal_ms"] > since_ms:
                since_ms = m["internal_ms"]
                newest = {"ts": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "id": m["id"]}
        self.channel_status["gmail"] = f"ok ({len(msgs)} listed)"
        return items, newest

    def _whatsapp(self, con: sqlite3.Connection, last: dict[str, Any]) -> tuple[list[dict], dict]:
        last_id = int(last.get("id") or 0)
        rows = con.execute(
            "select id, jid, chat_type, sender_push_name, sender_phone, coalesce(text, caption, media_type, '') body, whatsapp_timestamp "
            "from whatsapp_messages where id > ? and coalesce(from_me,0)=0 order by id limit 500",
            (last_id,),
        ).fetchall()
        max_id = con.execute("select coalesce(max(id),0) from whatsapp_messages").fetchone()[0]
        grouped: dict[str, dict] = {}
        for r in rows:
            key = r["jid"]
            at = datetime.fromtimestamp(int(r["whatsapp_timestamp"] or 0), timezone.utc)
            g = grouped.setdefault(key, {"channel": "WhatsApp" + (" group" if r["chat_type"] == "group" else ""), "from": r["sender_push_name"] or "unknown", "addr": key, "subject": "", "at": at, "count": 0, "bodies": []})
            g["count"] += 1
            g["at"] = max(g["at"], at)
            g["bodies"].append(str(r["body"] or ""))
        items = []
        for g in grouped.values():
            last_body = g["bodies"][-1][:90].replace("\n", " ")
            g["subject"] = (f"{g['count']} msgs, latest: " if g["count"] > 1 else "") + last_body
            g["action_text"] = " ".join(g.pop("bodies"))
            items.append(g)
        self.channel_status["whatsapp"] = f"ok ({len(rows)} new incoming)"
        newest = {"ts": _sast(self.now), "id": max_id} if max_id > last_id else last
        return items, newest

    def _telegram(self, con: sqlite3.Connection, last: dict[str, Any]) -> tuple[list[dict], dict]:
        last_id = int(last.get("id") or 0)
        rows = con.execute("select id, chat_title, coalesce(text, caption, '') body, telegram_date from telegram_group_messages where id > ? order by id limit 200", (last_id,)).fetchall()
        self.channel_status["telegram"] = "dark: no bot token configured, nothing is ingested" if not rows else f"ok ({len(rows)} new)"
        items = [{"channel": "Telegram", "from": r["chat_title"] or "group", "addr": r["chat_title"], "subject": str(r["body"])[:120], "at": datetime.fromtimestamp(int(r["telegram_date"] or 0), timezone.utc)} for r in rows]
        newest = {"ts": last.get("ts"), "id": rows[-1]["id"]} if rows else last
        return items, newest

    def _tickets(self, con: sqlite3.Connection, last: dict[str, Any]) -> tuple[list[dict], dict]:
        last_id = int(last.get("id") or 0)
        rows = con.execute(
            "select t.id, t.title, t.created_date, b.name board, l.name lane from kanban_tickets t join kanban_lanes l on l.id=t.lane_id join kanban_boards b on b.id=l.board_id where t.id > ? order by t.id",
            (last_id,),
        ).fetchall()
        items = [{"channel": "Ticket", "from": f"{r['board']} / {r['lane']}", "addr": f"ticket:{r['id']}", "subject": f"#{r['id']} {r['title']}"[:120], "at": _utc_naive(r["created_date"]) or self.now} for r in rows]
        self.channel_status["tickets"] = f"ok ({len(rows)} new)"
        newest = {"ts": str(rows[-1]["created_date"])[:19], "id": rows[-1]["id"]} if rows else last
        return items, newest

    # ---------------------------------------------------------- staleness
    def stale(self, con: sqlite3.Connection) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        rows = con.execute(
            "select t.id, t.title, t.modified_date, b.name board, l.name lane from kanban_tickets t join kanban_lanes l on l.id=t.lane_id "
            "join kanban_boards b on b.id=l.board_id where coalesce(b.archived,0)=0 "
            "and lower(l.name) not in ('done','archived','archive','cancelled','canceled','backlog','complete','completed')"
        ).fetchall()
        for r in rows:
            mod = _utc_naive(r["modified_date"])
            if not mod:
                continue
            wd = working_days_between(mod, self.now)
            if wd > 2:
                rule = "same lane >3 working days" if wd > 3 else "no activity >2 working days"
                out.append({"key": f"ticket:{r['id']}", "item": f"{r['board']} #{r['id']} {r['title'][:70]}", "lane": r["lane"], "last": _sast(mod), "stuck": f"{wd:.1f} working days", "rule": rule, "next": "Paul/Jupiter: decide move, close or chase"})
        for r in con.execute("select id, workflow_id, started_at, status, ticket_id from auto_workflow_runs where status in ('running','waiting')").fetchall():
            started = _utc_naive(r["started_at"])
            if not started:
                continue
            hours = (self.now - started).total_seconds() / 3600
            wd = working_days_between(started, self.now)
            if (r["status"] == "running" and hours > 2) or (r["status"] == "waiting" and (hours > 2 or wd > 1)):
                out.append({"key": f"run:{r['id']}", "item": f"workflow run {r['id']} (workflow {r['workflow_id']}, ticket {r['ticket_id']})", "lane": r["status"], "last": _sast(started), "stuck": f"{hours:.0f} h", "rule": "run stuck >2h" + (" / gate waiting >1 working day" if r["status"] == "waiting" and wd > 1 else ""), "next": "Paul: review the run (observe only; gates are Paul's)"})
        try:
            for r in con.execute("select id, skill_name, status, updated_at, created_at from skill_judgment_proposals where lower(coalesce(status,'')) in ('pending','proposed','open','draft')").fetchall():
                at = datetime.fromtimestamp(float(r["updated_at"] or r["created_at"] or 0), timezone.utc)
                wd = working_days_between(at, self.now)
                if wd > 2:
                    out.append({"key": f"proposal:{r['id']}", "item": f"skill proposal {r['id']} {r['skill_name']}", "lane": r["status"], "last": _sast(at), "stuck": f"{wd:.1f} working days", "rule": "proposal idle >2 working days", "next": "Paul: decide"})
        except sqlite3.Error:
            pass
        return out

    # ------------------------------------------------------------------ run
    def run(self, *, write_state: Callable[[dict], Any] | None = None, force: bool = False) -> dict[str, Any]:
        local = self.now.astimezone(SAST)
        if not force and not (WINDOW[0] <= local.hour < WINDOW[1]):
            return {"success": True, "notify": False, "skipped": "outside 07:00-20:00 SAST", "spoken_summary": "Outside monitor hours; silent."}
        state = self.load_state()
        last_seen = state.setdefault("last_seen", {})
        items: list[dict[str, Any]] = []
        got, last_seen["mailshot"] = self._mailshot(last_seen.get("mailshot") or {})
        items += got
        got, last_seen["gmail"] = self._gmail(last_seen.get("gmail") or {})
        items += got
        con = _ro_db(self.db_path)
        try:
            for name, fn in (("whatsapp", self._whatsapp), ("telegram", self._telegram), ("tickets", self._tickets)):
                try:
                    got, last_seen[name] = fn(con, last_seen.get(name) or {})
                    items += got
                except sqlite3.Error as exc:
                    self.channel_status[name] = f"error: {exc}"
            stale = self.stale(con)
        finally:
            con.close()
        # dedupe: same sender + subject within 24h is one item
        seen: dict[tuple, dict] = {}
        for it in sorted(items, key=lambda i: i["at"]):
            key = ((it.get("addr") or it["from"]).lower(), re.sub(r"^(re|fwd?):\s*", "", it["subject"].lower()).strip())
            prev = seen.get(key)
            if prev and (it["at"] - prev["at"]) < timedelta(hours=24):
                continue
            seen[key] = it
        new_items = list(seen.values())
        for it in new_items:
            text = f"{it['subject']} {it.get('action_text', '')}"
            noise = bool(_NOISE_FROM.search(it.get("addr") or ""))
            it["needs_action"] = (not noise) and bool(_ACTION_RE.search(text))
            it["owner"] = _owner(text + " " + (it.get("addr") or ""))
        reported_stale = set(state.get("stale_reported") or [])
        newly_stale = [s for s in stale if s["key"] not in reported_stale]
        outages = state.setdefault("outages", [])
        if self.channel_status.get("mailshot", "").startswith("DOWN"):
            outages.append({"at": _sast(self.now), "what": self.channel_status["mailshot"]})
            del outages[:-50]
        need = sum(1 for i in new_items if i["needs_action"])
        notify = bool(new_items or newly_stale)
        lines = [f"**{len(new_items)} new items, {need} need action.**" + (f" {len(newly_stale)} newly stale." if newly_stale else "")]
        for it in new_items:
            flag = "NEEDS ACTION - " if it["needs_action"] else ""
            lines.append(f"- {flag}{it['channel']} | {it['from']} | {it['subject']} | {_sast(it['at'])} | owner: {it['owner']}")
            if it["needs_action"]:
                lines.append(f"  Paul/Jupiter: this needs a reply or action; suggested: {it['owner']} to review and draft a reply (draft only).")
        if stale:
            lines.append("\n**Stale proposals**")
            groups: dict[str, list[dict]] = {}
            for s in stale:
                groups.setdefault(s["item"].split(" #")[0] + " / " + s["lane"] if s["key"].startswith("ticket:") else s["key"], []).append(s)
            for label, members in groups.items():
                new_n = sum(1 for s in members if s["key"] not in reported_stale)
                mark = "NEW " if new_n else ""
                if len(members) > 3:
                    oldest = max(members, key=lambda s: float(s["stuck"].split()[0]))
                    lines.append(f"- {mark}{label}: {len(members)} tickets idle (oldest {oldest['item'].split(' ', 1)[1]}, last {oldest['last']}, {oldest['stuck']}) | next: Paul/Jupiter: triage the lane")
                    continue
                for s in members:
                    mark = "NEW " if s["key"] not in reported_stale else ""
                    lines.append(f"- {mark}{s['item']} | {s['lane']} | last {s['last']} | {s['stuck']} ({s['rule']}) | next: {s['next']}")
        outstanding = [o for o in state.get("outstanding") or [] if o.get("status") != "resolved"]
        if outstanding:
            lines.append("\n**Outstanding**")
            for o in outstanding:
                lines.append(f"- {'NEEDS ACTION - ' if o.get('needs_action') else ''}{o.get('item')} (received {o.get('received')})")
        lines.append("\n_Channels: " + "; ".join(f"{k}: {v}" for k, v in self.channel_status.items()) + "_")
        markdown = "\n".join(lines)
        for it in new_items:
            if it["needs_action"]:
                outstanding.append({"item": f"{it['channel']} {it['from']}: {it['subject']}", "received": _sast(it["at"]), "needs_action": True, "owner": it["owner"], "source": "decisions-monitor"})
        state["outstanding"] = (state.get("outstanding") and [o for o in state["outstanding"] if o.get("status") == "resolved"] or []) + outstanding
        state["stale_reported"] = sorted({s["key"] for s in stale})
        state["channel_status"] = self.channel_status
        state["last_run"] = local.isoformat(timespec="minutes")
        if notify:
            state["last_report"] = {"at": local.isoformat(timespec="minutes"), "headline": lines[0]}
        if write_state:
            write_state(state)
        return {
            "success": True,
            "notify": notify,
            "needs_action": need > 0,
            "headline": lines[0].strip("*"),
            "markdown": markdown,
            "spoken_summary": lines[0].strip("*") if notify else "Support monitor: nothing new.",
            "new_items": len(new_items),
            "newly_stale": len(newly_stale),
            "channel_status": dict(self.channel_status),
        }


class SupportMonitorTool:
    """Automation tool binding (``action_config.tool = 'support_monitor'``)."""

    def __init__(self) -> None:
        self._gmail_service = None
        self._gmail_error = ""

    def prepare(self, **_: Any) -> None:
        # Credential warm-up outside the read-only scope (token refresh may persist).
        try:
            from distr.core.automation import gmail_readonly

            self._gmail_service = gmail_readonly.connect()
        except Exception as exc:
            self._gmail_error = str(exc)[:160]

    def _run(self, *, read_only_policy: Any = None, db_path: str | None = None, force: bool = False, seed_state_file: str | None = None, **_: Any) -> dict[str, Any]:
        if read_only_policy is None or read_only_policy.state_file is None:
            return {"success": False, "spoken_summary": "Support monitor refuses to run outside read-only mode."}
        mon = SupportMonitor(
            db_path=db_path or DEFAULT_DB,
            state_file=read_only_policy.state_file,
            seed_state_file=seed_state_file or (read_only_policy.state_file.parent / "state.json"),
            gmail_service=self._gmail_service,
        )
        mon.gmail_error = self._gmail_error
        return mon.run(write_state=read_only_policy.write_state, force=bool(force))
