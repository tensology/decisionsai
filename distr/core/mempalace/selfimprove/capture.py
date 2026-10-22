"""Frustration capture: scan new transcript lines, detect, redact, file to MemPalace.

Runs out of band (LaunchAgent every 10 minutes), so it never blocks a harness
session. Each detected frustration becomes:
- a logstream event ``type=frustration.v1`` on stream ``frustrations/<project>``
  with the structured schema in ``metadata``; and
- a drawer in wing ``agent_frustrations`` (room = skill or harness) holding the
  redacted evidence, so the nightly review can search and cluster it.
Idle sessions (45 min) also get one ``agent_sessions`` wrap-up drawer.
If the hub is down, writes go to the shared journal and are replayed later.
"""

from __future__ import annotations

import hashlib
import json
import os
import gc
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from distr.core.mempalace import hub
from distr.core.mempalace.paths import default_palace_path
from distr.core.mempalace.selfimprove import config, detect, sources
from distr.core.mempalace.selfimprove.memguard import Limits, MemoryGuard, WorkerWatchdog, apply_rlimits
from distr.core.mempalace.selfimprove.redact import redact

SCHEMA_VERSION = 1
FIRST_RUN_LOOKBACK_S = 48 * 3600
IDLE_WRAPUP_S = 45 * 60
HOST = os.environ.get("DECISIONS_MEMPALACE_HOST_LABEL", "mac")


def _state_path(dry_run: bool):
    # Dry runs keep their own checkpoint so they can be resumed/tested without moving the real cursor.
    return config.state_file().with_name("state.dryrun.json") if dry_run else config.state_file()


def _load_state(dry_run: bool = False) -> dict[str, Any]:
    try:
        return json.loads(_state_path(dry_run).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"files": {}, "sessions": {}, "first_run_done": False}


def _save_state(state: dict[str, Any], dry_run: bool = False) -> None:
    config.ensure_dirs()
    path = _state_path(dry_run)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _ts_epoch(ts: Any, fallback: float) -> float:
    if isinstance(ts, (int, float)):
        return float(ts) / (1000.0 if ts > 1e12 else 1.0)
    if isinstance(ts, str) and ts:
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return fallback


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


class LayaClient:
    """One long-lived Laya worker per run: the model is loaded once and reused for every batch."""

    def __init__(self, guard: MemoryGuard, limits: Limits, timeout_s: float = 180.0) -> None:
        self.guard, self.limits, self.timeout_s = guard, limits, timeout_s
        self.proc: subprocess.Popen | None = None
        self.failed = False
        self.info: dict[str, Any] = {}
        self.skipped = "" if limits.laya_fits else (
            f"memory budget too small for Laya (stop at {limits.stop_mb:.0f} MB < {limits.laya_min_mb:.0f} MB); "
            "regex-only. Raise DECISIONS_CAPTURE_MAX_RSS_MB (e.g. 3072) to enable it")

    def _start(self) -> bool:
        worker = Path(__file__).with_name("laya_worker.py")
        env = {**os.environ, "TOKENIZERS_PARALLELISM": "false", "HF_HUB_OFFLINE": "1", "OMP_NUM_THREADS": "4",
               "LAYA_DEVICE": self.limits.laya_device, "LAYA_MAX_CHARS": str(self.limits.laya_chars),
               "LAYA_BATCH_SIZE": str(self.limits.laya_batch),
               # only used on MPS: torch allocation cap = what is left of the run budget
               "DECISIONS_CAPTURE_LAYA_MAX_MB": str(max(256, int(self.limits.stop_mb * 0.8)))}
        try:
            self.proc = subprocess.Popen([str(config.laya_python()), str(worker), "--serve"], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
            self.guard.worker_pid = self.proc.pid
            line = self._readline()
            self.info = json.loads(line) if line else {}
            return bool(self.info.get("ready"))
        except Exception:
            return False

    def _readline(self) -> str:
        assert self.proc and self.proc.stdout
        box: list[str] = []
        t = threading.Thread(target=lambda: box.append(self.proc.stdout.readline()), daemon=True)
        t.start()
        t.join(self.timeout_s)
        if t.is_alive():
            self.close()
            return ""
        return box[0] if box else ""

    def classify(self, texts: list[str]) -> list[dict] | None:
        if self.skipped or self.failed or not texts or not config.laya_available():
            return None
        try:
            with WorkerWatchdog(self.guard, self.kill) as wd:  # covers model load and the batch
                if self.proc is None and not self._start():
                    self.failed = True
                    self.close()
                    return None
                self.proc.stdin.write(json.dumps({"states": texts, "questions": detect.LAYA_QUESTIONS}) + "\n")
                self.proc.stdin.flush()
                line = self._readline()
            if wd.tripped or not line:
                self.failed = True
                self.close()
                return None
            out = json.loads(line)
            return out if isinstance(out, list) and len(out) == len(texts) else None
        except Exception:
            self.failed = True
            self.close()
            return None

    def kill(self) -> None:
        """Hard stop from the watchdog thread (memory cap crossed)."""
        p = self.proc
        if p is not None:
            try:
                p.kill()
            except Exception:
                pass

    def close(self) -> None:
        p, self.proc = self.proc, None
        self.guard.worker_pid = None
        if p is None:
            return
        try:
            p.stdin.close()
        except Exception:
            pass
        try:
            p.wait(timeout=5)
        except Exception:
            p.kill()


def _ctx(s: dict) -> dict:
    """The few session fields a record needs (never a full session copy)."""
    return {k: s.get(k) for k in ("cwd", "last_tool", "last_skill", "last_assistant")}


def _new_session(t: dict, now: float) -> dict:
    return {"harness": t["harness"], "project": t["project"], "cwd": t.get("cwd"), "users": 0, "frustrations": 0,
            "tool_errors": 0, "err_streak": 0, "prev_users": [], "last_assistant": "", "first_ask": "",
            "last_ts": now, "wrapped": False, "ref": t["ref"]}


def _observe(t: dict, s: dict, *, redact_on: bool, limits: Limits, fallback_ts: float) -> dict | None:
    """Update session ``s`` with turn ``t``; return a compact candidate or None."""
    s["last_ts"] = _ts_epoch(t.get("ts"), fallback_ts)
    s["project"] = t["project"] or s["project"]
    s["wrapped"] = False
    role = t["role"]
    if role == "assistant":
        s["last_assistant"] = redact(t["text"][-1500:], enabled=redact_on)
        s["last_tool"] = t.get("tool") or s.get("last_tool", "")
        sk = detect.skill_from(t["text"])
        if sk:
            s["last_skill"] = sk
        if not t.get("tool"):
            s["err_streak"] = 0
        return None
    if role == "tool":
        if not t["is_error"]:
            s["err_streak"] = 0
            return None
        s["tool_errors"] += 1
        s["err_streak"] += 1
        if s["err_streak"] != 2:
            return None
        t["text"] = t["text"][:limits.max_msg_chars]
        return {"turn": t, "session": _ctx(s), "laya_ok": False,
                "regex": {"tags": ["tool_failure"], "severity": 2, "detector": "tool-errors"}}
    if role != "user" or not t["text"].strip():
        return None
    digest = hashlib.sha1(t["text"].encode()).hexdigest()[:12]
    if s["prev_users"] and s.get("last_user_raw") == digest:
        return None  # same message logged twice (queued/steered input)
    s["last_user_raw"] = digest
    s["users"] += 1
    if not s["first_ask"]:
        s["first_ask"] = redact(t["text"][:300], enabled=redact_on)
    huge = t.get("raw_len", len(t["text"])) > limits.max_msg_chars  # pasted logs/files: not a feeling
    sig = None if huge else detect.regex_signal(t["text"], s["prev_users"])
    s["prev_users"] = (s["prev_users"] + [redact(t["text"][:500], enabled=redact_on)])[-5:]
    if huge:
        return {"skip": True}
    return {"turn": t, "session": _ctx(s), "regex": sig, "laya_ok": True}


def build_record(c: dict, verdict: dict, *, redact_on: bool) -> dict[str, Any]:
    t, s = c["turn"], c["session"]
    skill = detect.skill_from(t["text"]) or s.get("last_skill", "")
    ts = _ts_epoch(t.get("ts"), time.time())
    quote = redact(t["text"][:800], enabled=redact_on)
    meta = {
        "id": hashlib.sha1(f"{t['ref']}|{t['text'][:200]}".encode()).hexdigest()[:16],
        "schema_version": SCHEMA_VERSION,
        "ts": _iso(ts),
        "harness": t["harness"],
        "host": HOST,
        "project": t["project"],
        "cwd": s.get("cwd") or t.get("cwd"),
        "session_id": t["session"],
        "tags": verdict["tags"],
        "severity": verdict["severity"],
        "detector": verdict["detector"],
        "laya_p": verdict.get("laya_p"),
        "skill": skill or None,
        "tool": s.get("last_tool") or t.get("tool") or None,
        "user_quote": quote,
        "agent_action_excerpt": redact(s.get("last_assistant", "")[-600:], enabled=redact_on),
        "error_excerpt": redact(t["text"][:600], enabled=redact_on) if t["role"] == "tool" else None,
        "evidence_ref": t["ref"],
        "redaction_applied": redact_on,
    }
    return meta


def _emit(palace: str, tool: str, args: dict, *, dry_run: bool, hub_info: dict | None) -> str:
    if dry_run:
        return "dry-run"
    if hub_info is not None:
        try:
            hub.call_tool(palace, tool, args, hub=hub_info)
            return "hub"
        except Exception:
            pass
    hub.journal_append(palace, tool, args)
    return "journal"


def file_record(palace: str, rec: dict, *, dry_run: bool, hub_info: dict | None) -> str:
    agent = f"{HOST}:{rec['harness']}:{rec['project']}"
    body = (f"[frustration.v1] {rec['ts']} {agent} sev={rec['severity']} tags={','.join(rec['tags'])}"
            f" skill={rec['skill'] or '-'} tool={rec['tool'] or '-'}\n"
            f"USER: {rec['user_quote']}\n"
            f"PREVIOUS AGENT ACTION: {rec['agent_action_excerpt']}\n"
            + (f"ERROR: {rec['error_excerpt']}\n" if rec.get("error_excerpt") else "")
            + f"EVIDENCE: {rec['evidence_ref']}")
    ev = _emit(palace, "mempalace_event_append", {
        "type": "frustration.v1", "stream": f"frustrations/{rec['project']}", "room": "capture",
        "from_agent": agent, "to_agent": "*", "topic": rec["skill"] or rec["harness"], "status": "open",
        "body": body, "metadata": rec}, dry_run=dry_run, hub_info=hub_info)
    _emit(palace, "mempalace_add_drawer", {
        "wing": "agent_frustrations", "room": (rec["skill"] or rec["harness"]).replace(".", "_"),
        "content": body, "source_file": rec["evidence_ref"], "added_by": agent},
        dry_run=dry_run, hub_info=hub_info)
    return ev


def wrapups(state: dict, palace: str, *, now: float, dry_run: bool, hub_info: dict | None, redact_on: bool) -> list[str]:
    done = []
    for key, s in state.get("sessions", {}).items():
        if s.get("wrapped") or s.get("users", 0) < 2 or now - s.get("last_ts", now) < IDLE_WRAPUP_S:
            continue
        agent = f"{HOST}:{s['harness']}:{s['project']}"
        text = (f"[session.wrapup.v1] {agent} session={key.split(':', 1)[1]} ended~{_iso(s['last_ts'])}\n"
                f"user_turns={s['users']} frustrations={s['frustrations']} tool_errors={s['tool_errors']}"
                f" last_skill={s.get('last_skill') or '-'}\n"
                f"FIRST ASK: {redact(s.get('first_ask', ''), enabled=redact_on)}")
        _emit(palace, "mempalace_add_drawer", {"wing": "agent_sessions", "room": s["harness"], "content": text,
                                               "source_file": s.get("ref", ""), "added_by": agent},
              dry_run=dry_run, hub_info=hub_info)
        if not dry_run:
            s["wrapped"] = True
        done.append(key)
    # forget long-finished sessions
    for key in [k for k, s in state.get("sessions", {}).items() if s.get("wrapped") and now - s.get("last_ts", now) > 7 * 86400]:
        state["sessions"].pop(key, None)
    return done


def run(*, dry_run: bool = False, max_records: int = 200) -> dict[str, Any]:
    """Stream new transcript lines in small batches under a memory budget (see memguard)."""
    if not config.capture_enabled():
        return {"skipped": "frustration capture disabled"}
    limits = Limits()
    guard = MemoryGuard(limits)
    rlimits = apply_rlimits(limits.max_rss_mb)
    palace = str(default_palace_path())
    now = time.time()
    state = _load_state(dry_run)
    files_state = state.setdefault("files", {})
    sessions = state.setdefault("sessions", {})
    first_run = not state.get("first_run_done")
    hub_info = hub.discover(palace)
    replayed = 0 if dry_run or hub_info is None else hub.journal_replay(palace, hub=hub_info, limit=200)
    redact_on = config.redaction_enabled()
    only = {x.strip() for x in (os.environ.get("DECISIONS_CAPTURE_ONLY") or "").split(",") if x.strip()}
    max_files = int(os.environ.get("DECISIONS_CAPTURE_MAX_FILES") or 0)
    laya = LayaClient(guard, limits)
    seen = set(state.setdefault("emitted_ids", []))
    stats = {"user_turns": 0, "batches": 0, "frustrations": 0, "skipped_huge_lines": 0, "skipped_huge_msgs": 0,
             "bytes_read": 0, "files": 0, "laya_batches": 0}
    items: list[dict] = []
    batch: list[dict] = []
    stop_reason = ""

    def flush() -> None:
        nonlocal batch
        if not batch:
            return
        users = [i for i, c in enumerate(batch) if c["laya_ok"]]
        out = laya.classify([batch[i]["turn"]["text"][:limits.laya_chars] for i in users]) if users else None
        if out:
            stats["laya_batches"] += 1
        laya_map = dict(zip(users, out)) if out else {}
        for i, c in enumerate(batch):
            verdict = detect.combine(c["regex"], laya_map.get(i)) if c["laya_ok"] else c["regex"]
            if not verdict:
                continue
            rec = build_record(c, verdict, redact_on=redact_on)
            if rec["id"] in seen or stats["frustrations"] >= max_records:
                continue
            via = file_record(palace, rec, dry_run=dry_run, hub_info=hub_info)  # written as found
            seen.add(rec["id"])
            stats["frustrations"] += 1
            key = f"{rec['harness']}:{rec['session_id']}"
            if key in sessions:
                sessions[key]["frustrations"] += 1
            if len(items) < 25:
                items.append({"id": rec["id"], "harness": rec["harness"], "project": rec["project"],
                              "tags": rec["tags"], "severity": rec["severity"], "detector": rec["detector"],
                              "via": via, "quote": rec["user_quote"][:140]})
        batch = []
        stats["batches"] += 1
        gc.collect()

    def checkpoint() -> None:
        state["emitted_ids"] = list(seen)[-5000:]
        state["last_capture"] = _iso(now)
        _save_state(state, dry_run)

    try:
        for name in sources.SOURCES:
            if stop_reason:
                break
            if only and name not in only:
                continue
            for path in sources.files(name):
                if stop_reason:
                    break
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                fs = files_state.get(path)
                if fs is None:
                    if first_run and now - st.st_mtime > FIRST_RUN_LOOKBACK_S:
                        files_state[path] = {"offset": st.st_size, "meta": {}}
                        continue
                    fs = files_state[path] = {"offset": 0, "meta": {}}
                if st.st_size < fs["offset"]:
                    fs["offset"] = 0  # truncated / rotated
                if st.st_size == fs["offset"]:
                    continue
                if max_files and stats["files"] >= max_files:
                    break
                stats["files"] += 1
                lines = 0
                end = fs["offset"]
                try:
                    for turns, end, nbytes in sources.iter_new(name, path, fs["offset"], fs["meta"],
                                                               max_line_bytes=limits.max_line_bytes):
                        lines += 1
                        stats["bytes_read"] += nbytes
                        if not turns and nbytes > limits.max_line_bytes:
                            stats["skipped_huge_lines"] += 1
                        for t in turns:
                            key = f"{t['harness']}:{t['session']}"
                            s = sessions.get(key) or sessions.setdefault(key, _new_session(t, now))
                            c = _observe(t, s, redact_on=redact_on, limits=limits, fallback_ts=st.st_mtime)
                            if t["role"] == "user" and t["text"].strip():
                                stats["user_turns"] += 1
                            if c is None:
                                continue
                            if c.get("skip"):
                                stats["skipped_huge_msgs"] += 1
                                continue
                            batch.append(c)
                        del turns
                        if len(batch) >= limits.batch_size:
                            flush()
                            fs["offset"] = end
                            checkpoint()
                            if guard.check(f"{path}@{end}") == "stop":
                                stop_reason = "memory cap"
                        elif lines % limits.check_every_lines == 0 and guard.check(f"{path}@{end}") == "stop":
                            flush()
                            fs["offset"] = end
                            checkpoint()
                            stop_reason = "memory cap"
                        if not stop_reason and stats["user_turns"] >= limits.max_turns:
                            stop_reason = f"per-run turn cap ({limits.max_turns})"
                        if not stop_reason and stats["bytes_read"] >= limits.max_bytes_per_run:
                            stop_reason = f"per-run byte cap ({limits.max_bytes_per_run // 2**20} MB)"
                        if stop_reason:
                            break
                except OSError:
                    continue
                flush()
                fs["offset"] = end
                checkpoint()
                if not stop_reason and guard.check(f"{path}@end") == "stop":
                    stop_reason = "memory cap"
        wrapped = [] if stop_reason == "memory cap" else wrapups(
            state, palace, now=now, dry_run=dry_run, hub_info=hub_info, redact_on=redact_on)
        if not stop_reason:
            state["first_run_done"] = True  # only once a full pass completed
        checkpoint()
    finally:
        laya.close()
    return {"host": socket.gethostname().split(".")[0], "hub": bool(hub_info), "journal_replayed": replayed,
            "classifier": "laya+regex" if stats["laya_batches"] else "regex", "dry_run": dry_run,
            "stopped_early": stop_reason or None, "peak_mb": round(guard.peak_mb, 1), "limits": limits.as_dict(),
            "rlimits": rlimits, "laya_worker": laya.info or None,
            "laya_skipped": laya.skipped or ("worker failed or hit the cap" if laya.failed else None), "notifications": guard.notified, **stats,
            "wrapups": len(wrapped), "items": items}
