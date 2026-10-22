"""Pending-approval queue for self-improvement proposals. Nothing is ever auto-applied.

Flow: capture -> nightly scores clusters and drafts a diff -> ``enqueue`` puts it in
``~/.decisions/self-improve/pending/<id>.json`` (status=pending) -> Paul approves
(``python -m distr.core.mempalace.selfimprove approve <id>`` or
``POST /api/selfimprove/proposals/<id>/approve``) -> ``git apply`` writes it to the
working tree. No commit, no push, no snapshots: git (or the kept patch file with
``git apply -R``) is the rollback. Every enqueue/approve/reject is logged as JSONL in
``~/.decisions/logs/selfimprove-approvals.jsonl``.

Risk is classified for display only (Paul still approves every item).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from distr.core.mempalace.selfimprove import config

HIGH_RISK_PATH = re.compile(
    r"(rout(er|ing)|websocket|\bws\b|/db/|migration|\.sql$|auth|token|secret|security|"
    r"infra|deploy|docker|launchd|\.plist$|server\.py$|settings\.db)", re.I)
LOW_RISK_SUFFIX = (".md", ".txt", ".mdc", ".yaml", ".yml", ".toml", ".json")
CODE_SUFFIX = (".py", ".js", ".ts", ".tsx", ".sh", ".swift", ".rs", ".go")


def pending_dir() -> Path:
    return config.root() / "pending"


def log_path() -> Path:
    return config.home() / ".decisions" / "logs" / "selfimprove-approvals.jsonl"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log_event(action: str, **fields: Any) -> None:
    p = log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": _now(), "action": action, **fields}, ensure_ascii=False, default=str) + "\n")


def _diff_paths(diff: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"^\+\+\+ b/(\S+)", diff, re.M)]


def classify(target: str, diff: str) -> tuple[str, str]:
    """Return (risk, reason). Informational: every proposal still needs approval."""
    paths = [target] + _diff_paths(diff)
    for p in paths:
        if HIGH_RISK_PATH.search(p):
            return "high", f"touches sensitive area: {p}"
    if any(p.endswith(CODE_SUFFIX) for p in paths):
        return "high", "code change"
    if all(p.endswith(LOW_RISK_SUFFIX) for p in paths):
        return "low", "instruction/skill/config text only"
    return "medium", "unrecognised file type"


def _pid(target: str, diff: str) -> str:
    return datetime.now().strftime("%Y%m%d") + "-" + hashlib.sha1((target + diff).encode()).hexdigest()[:8]


def _rec_path(pid: str) -> Path:
    if not re.fullmatch(r"[0-9]{8}-[0-9a-f]{8}", pid or ""):
        raise ValueError(f"bad proposal id: {pid!r}")
    return pending_dir() / f"{pid}.json"


def _save(rec: dict) -> None:
    d = pending_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    tmp = _rec_path(rec["id"]).with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _rec_path(rec["id"]))


def get(pid: str) -> dict:
    try:
        return json.loads(_rec_path(pid).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise KeyError(pid)


def enqueue(*, target: str, diff: str, scope: str = "", tag: str = "", reason: str = "",
            meta: dict | None = None) -> dict:
    pid = _pid(target, diff)
    try:
        existing = get(pid)
        return existing
    except KeyError:
        pass
    risk, why = classify(target, diff)
    rec = {"id": pid, "status": "pending", "created": _now(), "target": target, "scope": scope, "tag": tag,
           "risk": risk, "risk_reason": why, "reason": reason, "diff": diff, **({"meta": meta} if meta else {})}
    _save(rec)
    log_event("proposed", id=pid, target=target, risk=risk, risk_reason=why, reason=reason, diff=diff)
    return rec


def list_proposals(status: str | None = "pending") -> list[dict]:
    out = []
    for f in sorted(pending_dir().glob("*.json")) if pending_dir().is_dir() else []:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if status in (None, rec.get("status")):
            out.append(rec)
    return out


def _git(args: list[str], cwd: Path, diff: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], input=diff, text=True, capture_output=True, cwd=str(cwd), timeout=60)


def _dirty(target: Path) -> bool:
    r = _git(["status", "--porcelain", "--", target.name], target.parent)
    return r.returncode == 0 and bool(r.stdout.strip())


def approve(pid: str, *, by: str = "paul", allow_dirty: bool = False) -> dict:
    """Apply an approved proposal to the working tree. Never commits or pushes."""
    rec = get(pid)
    if rec["status"] != "pending":
        raise ValueError(f"{pid} is {rec['status']}, not pending")
    target = Path(rec["target"])
    if not target.is_file():
        raise ValueError(f"target missing: {target}")
    if _dirty(target) and not allow_dirty:
        raise ValueError(f"{target} has uncommitted edits; pass allow_dirty to apply on top")
    chk = _git(["apply", "--check", "--unsafe-paths", "-"], target.parent, rec["diff"])
    if chk.returncode != 0:
        log_event("approve-failed", id=pid, by=by, error=chk.stderr.strip()[:500])
        raise ValueError(f"diff no longer applies: {chk.stderr.strip()[:300]}")
    patch = config.root() / "applied" / f"{pid}.patch"
    patch.parent.mkdir(parents=True, exist_ok=True)
    patch.write_text(rec["diff"], encoding="utf-8")
    r = _git(["apply", "--unsafe-paths", "-"], target.parent, rec["diff"])
    if r.returncode != 0:
        log_event("approve-failed", id=pid, by=by, error=r.stderr.strip()[:500])
        raise ValueError(f"git apply failed: {r.stderr.strip()[:300]}")
    revert = f"git -C {target.parent} apply -R --unsafe-paths {patch}"
    rec.update({"status": "applied", "approved_by": by, "applied_at": _now(), "patch": str(patch), "revert_cmd": revert})
    _save(rec)
    log_event("approved-applied", id=pid, by=by, target=str(target), risk=rec["risk"], patch=str(patch), revert=revert)
    return rec


def reject(pid: str, *, by: str = "paul", note: str = "") -> dict:
    rec = get(pid)
    if rec["status"] != "pending":
        raise ValueError(f"{pid} is {rec['status']}, not pending")
    rec.update({"status": "rejected", "rejected_by": by, "rejected_at": _now(), "note": note})
    _save(rec)
    log_event("rejected", id=pid, by=by, note=note)
    return rec
