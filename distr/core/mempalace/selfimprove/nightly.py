"""Nightly self-improvement review (every second night). Proposes; never applies.

Every drafted diff is queued in ``approvals`` (pending) for explicit user sign-off;
only ``approvals.approve`` writes it to the working tree (no commit/push).

1. Pull ``frustration.v1`` events since the stored cursor from the hub.
2. Drop excluded ones (P1 / live-money projects, see ``config.EXCLUDE_PATTERNS``).
3. Cluster by (skill-or-project, primary tag) and merge near-duplicate quotes.
4. Score clusters (count x mean severity x recency); keep the top N.
5. For each cluster pick a target file (the skill's SKILL.md if found, else the
   project's AGENTS.md / CLAUDE.md, else the global AGENTS.md) and ask a local
   reviewer CLI (codex exec -> claude -p -> Decisions LLM) for a small unified diff.
6. Write ``~/.decisions/self-improve/proposals/<date>/NN-<slug>.diff`` + ``summary.md``,
   check each diff with ``git apply --check``, and post one ``patch.ready`` event
   per proposal (status=open) for human approval.
Dry run (``--dry-run``): no reviewer call unless ``--llm``, no events, no cursor
move; output goes to ``proposals/<date>-dryrun``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from distr.core.mempalace import hub
from distr.core.mempalace.paths import default_palace_path
from distr.core.mempalace.selfimprove import approvals, config, detect
from distr.core.mempalace.selfimprove.redact import redact

TOP_N = 5
HOST = os.environ.get("DECISIONS_MEMPALACE_HOST_LABEL", "mac")
AGENT = f"{HOST}:decisions:selfimprove"


def is_review_night(now: float | None = None) -> bool:
    days = int((now or time.time()) // 86400)
    return days % 2 == 0


def _excluded(ev: dict) -> bool:
    meta = ev.get("metadata") or {}
    hay = " ".join(str(meta.get(k) or "") for k in ("project", "skill", "user_quote")) + " " + str(ev.get("stream") or "")
    return any(re.search(p, hay, re.I) for p in config.EXCLUDE_PATTERNS)


def fetch_events(palace: str, cursor: str | None, hub_info: dict) -> list[dict]:
    events: list[dict] = []
    args: dict[str, Any] = {"type": "frustration.v1", "limit": 500, "order": "asc", "from_agent": AGENT}
    if cursor:
        args["since_event_id"] = cursor
    for _ in range(20):
        res = hub.call_tool(palace, "mempalace_event_list", args, hub=hub_info)
        batch = res.get("events") or []
        events.extend(batch)
        if len(batch) < 500:
            break
        args["since_event_id"] = _eid(batch[-1])
    return events


def _eid(ev: dict) -> str:
    return str(ev.get("event_id") or ev.get("id") or "")


def cluster(events: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for ev in events:
        m = ev.get("metadata") or {}
        tags = m.get("tags") or ["correction"]
        key = (m.get("skill") or f"project:{m.get('project') or 'unknown'}", tags[0])
        groups[key].append(m)
    clusters = []
    now = time.time()
    for (scope, tag), items in groups.items():
        merged: list[dict] = []
        for it in items:
            for c in merged:
                if detect.jaccard(c["user_quote"], it.get("user_quote") or "") >= 0.5:
                    c["dupes"] += 1
                    break
            else:
                merged.append({**it, "dupes": 0})
        sev = sum(float(i.get("severity") or 1) for i in items) / len(items)
        newest = max((_age(i, now) for i in items), default=30)
        score = len(items) * sev * (1.0 if newest < 2 else 0.6 if newest < 7 else 0.3)
        clusters.append({"scope": scope, "tag": tag, "count": len(items), "mean_severity": round(sev, 2),
                         "score": round(score, 2), "harnesses": sorted({i.get("harness") for i in items if i.get("harness")}),
                         "projects": sorted({i.get("project") for i in items if i.get("project")}),
                         "examples": merged[:6]})
    return sorted(clusters, key=lambda c: c["score"], reverse=True)


def _age(item: dict, now: float) -> float:
    try:
        return (now - datetime.fromisoformat(item["ts"]).timestamp()) / 86400
    except Exception:
        return 30.0


def _skill_dirs() -> list[Path]:
    h = config.home()
    return [h / ".agents" / "skills", h / ".claude" / "skills", h / ".codex" / "skills", h / ".pi" / "agent" / "skills",
            h / ".cursor" / "skills"]


def target_for(c: dict) -> Path | None:
    scope = c["scope"]
    if not scope.startswith("project:"):
        for d in _skill_dirs():
            p = d / scope / "SKILL.md"
            if p.is_file():
                return p
    for ex in c["examples"]:
        if ex.get("cwd"):
            cwd = Path(ex["cwd"])
            for name in ("AGENTS.md", "CLAUDE.md"):
                if (cwd / name).is_file():
                    return cwd / name
    for p in (config.home() / ".codex" / "AGENTS.md", config.home() / ".claude" / "CLAUDE.md"):
        if p.is_file():
            return p
    return None


PROMPT = """You are reviewing recurring user frustrations captured from AI coding sessions.
Propose ONE small, conservative edit to the instruction file below so the agent stops causing this frustration.
Rules:
- Output ONLY a unified diff against the file, using headers `--- a/{name}` and `+++ b/{name}`, with correct @@ hunk headers and 3 lines of context. No prose, no code fences.
- Change at most ~15 lines. Prefer adding a short, specific rule or fixing a wrong instruction. Do not rewrite or reformat.
- Never add secrets, personal data, or anything about payments, trading or live money.
- If the evidence does not justify a change, output exactly: NO_CHANGE

Cluster: scope={scope} tag={tag} count={count} mean_severity={sev} harnesses={harnesses} projects={projects}

Evidence (redacted user quotes and the agent action just before):
{evidence}

File: {path}
-----8<-----
{content}
-----8<-----
"""


def build_prompt(c: dict, target: Path) -> str:
    content = target.read_text(encoding="utf-8", errors="replace")[:12000]
    ev = "\n".join(
        f"- [{e.get('harness')}/{e.get('project')} sev={e.get('severity')} x{1 + e.get('dupes', 0)}] USER: "
        f"{redact(str(e.get('user_quote') or ''))[:400]}\n  AGENT BEFORE: {redact(str(e.get('agent_action_excerpt') or ''))[:300]}"
        for e in c["examples"])
    return PROMPT.format(name=target.name, scope=c["scope"], tag=c["tag"], count=c["count"], sev=c["mean_severity"],
                         harnesses=",".join(c["harnesses"]), projects=",".join(c["projects"]), evidence=ev,
                         path=target, content=content)


def _extract_diff(text: str) -> str | None:
    if "NO_CHANGE" in text and "@@" not in text:
        return None
    text = re.sub(r"^```[a-z]*\n|```\s*$", "", text.strip(), flags=re.M)
    start = text.find("--- a/")
    return (text[start:].rstrip() + "\n") if start >= 0 and "@@" in text else None


def _check(diff: str, target: Path) -> bool:
    r = subprocess.run(["git", "apply", "--check", "--unsafe-paths", "-"], input=diff, text=True,
                       capture_output=True, cwd=str(target.parent), timeout=60)
    return r.returncode == 0


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "cluster"


def run(*, dry_run: bool = False, force: bool = False, use_llm: bool | None = None, top_n: int = TOP_N) -> dict[str, Any]:
    if not config.capture_enabled():
        return {"skipped": "frustration capture disabled"}
    if not (dry_run or force or is_review_night()):
        return {"skipped": "not a review night (runs every second night)"}
    use_llm = (not dry_run) if use_llm is None else use_llm
    palace = str(default_palace_path())
    hub_info = hub.discover(palace)
    if hub_info is None:
        return {"error": "MemPalace hub is not running; nothing reviewed"}
    state_path = config.root() / "nightly-state.json"
    try:
        st = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    events = fetch_events(palace, st.get("cursor"), hub_info)
    excluded = [e for e in events if _excluded(e)]
    kept = [e for e in events if not _excluded(e)]
    clusters = cluster(kept)
    stamp = datetime.now().strftime("%Y-%m-%d")
    out_dir = config.proposals_dir() / (stamp + ("-dryrun" if dry_run else ""))
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    reviewer, _ = config.pick_reviewer()
    proposals = []
    for n, c in enumerate(clusters[:top_n], 1):
        target = target_for(c)
        item = {"n": n, "scope": c["scope"], "tag": c["tag"], "count": c["count"], "score": c["score"],
                "target": str(target) if target else None, "status": "no-target"}
        if target:
            prompt = build_prompt(c, target)
            (out_dir / f"{n:02d}-{_slug(c['scope'])}.prompt.md").write_text(prompt, encoding="utf-8")
            item["status"] = "prompt-only"
            if use_llm:
                try:
                    who, text = config.run_reviewer(prompt)
                    item["reviewer"] = who
                    diff = _extract_diff(text)
                    if diff is None:
                        item["status"] = "no-change"
                    else:
                        path = out_dir / f"{n:02d}-{_slug(c['scope'])}.diff"
                        path.write_text(diff, encoding="utf-8")
                        item.update({"status": "proposed", "diff": str(path), "applies_cleanly": _check(diff, target)})
                        if not dry_run:
                            rec = approvals.enqueue(
                                target=str(target), diff=diff, scope=c["scope"], tag=c["tag"],
                                reason=f"{c['count']} frustrations, mean severity {c['mean_severity']}, score {c['score']}",
                                meta={"reviewer": who, "applies_cleanly": item["applies_cleanly"], "diff_file": str(path)})
                            item.update({"approval_id": rec["id"], "risk": rec["risk"]})
                except Exception as exc:
                    item["status"] = f"reviewer-failed: {exc}"
        proposals.append(item)
    lines = [f"# Self-improvement proposals {stamp}{' (DRY RUN)' if dry_run else ''}", "",
             f"Events reviewed: {len(kept)} (excluded P1/live-money: {len(excluded)}); clusters: {len(clusters)}; "
             f"reviewer: {reviewer if use_llm else 'none (dry run)'}", "",
             "Nothing here has been applied. Each proposal is queued for approval; to accept one run",
             "`python -m distr.core.mempalace.selfimprove approve <id>` (or POST /api/selfimprove/proposals/<id>/approve).",
             "It lands as an uncommitted working-tree edit; revert with git or the revert_cmd in the record.", ""]
    for c, p in zip(clusters[:top_n], proposals):
        lines += [f"## {p['n']}. {c['scope']} - {c['tag']} (count {c['count']}, mean severity {c['mean_severity']}, score {c['score']})",
                  f"- harnesses: {', '.join(c['harnesses'])}; projects: {', '.join(c['projects'])}",
                  f"- target: `{p['target']}`", f"- approval id: {p.get('approval_id', '-')} (risk {p.get('risk', '-')})", f"- status: {p['status']}" + (f" (applies cleanly: {p.get('applies_cleanly')})" if 'applies_cleanly' in p else ""),
                  "- examples:"]
        lines += [f"  - [{e.get('harness')}] \"{str(e.get('user_quote') or '')[:160]}\"" for e in c["examples"][:3]]
        lines.append("")
    if excluded:
        lines += ["## Excluded (P1 / live money; never proposed)",
                  *[f"- {(e.get('metadata') or {}).get('project')}: {str((e.get('metadata') or {}).get('user_quote'))[:100]}" for e in excluded[:10]], ""]
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    if not dry_run:
        for p in proposals:
            if p["status"] == "proposed":
                try:
                    hub.call_tool(palace, "mempalace_event_append", {
                        "type": "patch.ready", "stream": "selfimprove/proposals", "room": "patches", "from_agent": AGENT,
                        "to_agent": "*", "status": "open", "topic": p["scope"],
                        "body": f"Proposed edit to {p['target']} for {p['scope']}/{p['tag']} ({p['count']} frustrations). "
                                f"Review {p['diff']} - never auto-applied; approve id {p.get('approval_id')}.",
                        "metadata": {k: p.get(k) for k in ("target", "diff", "applies_cleanly", "score", "count", "reviewer",
                                                           "approval_id", "risk")}},
                        hub=hub_info)
                except Exception:
                    pass
        if events:
            st["cursor"] = _eid(events[-1])
        st["last_run"] = datetime.now().astimezone().isoformat(timespec="seconds")
        state_path.write_text(json.dumps(st, indent=1), encoding="utf-8")
    return {"dry_run": dry_run, "events": len(events), "excluded": len(excluded), "clusters": len(clusters),
            "reviewer": reviewer if use_llm else None, "out_dir": str(out_dir), "proposals": proposals}
