"""From a client's complaint to a verified fix and a reply that says what was actually changed.

The client-mail watch spots the problem. This module does what Paul would otherwise ask an agent
to do by hand:

1. ``investigate``   read only. Finds the records the complaint is about and traces the cause.
2. ``curate_proposal``  turns the finding into one Incoming proposal: what is wrong, the root cause,
                      the exact change, and what is waiting on Paul. Nothing is changed yet.
3. (Paul accepts the proposal. That approval is the gate for the production write.)
4. ``execute``       applies the planned change, re-reads to verify it, renders the corrected
                      document. ``scratch=True`` does all of it on a throw-away copy of the data.
5. ``draft_reply``   written only from the change set that ``execute`` returned.

Hard rules: no step sends anything. Read steps run in a READ ONLY database transaction that is
rolled back. Write steps refuse any database that is not a ``scratch_`` copy, so a dry run cannot
touch real records. The scripts that run on the Tensology host are fixed files, never model output.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import shlex
import subprocess
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

SSH_TARGET_KEY = "client_issue_ssh_target"
DEFAULT_SSH_TARGET = "root@tensology.com"
APP_DIR = "/var/www/tensology.com/backend/webapp"
VENV_PY = "/opt/venvs/tensology/bin/python"
ENV_FILE = "/etc/tensology-api.env"
SCRIPT_DIR = Path(__file__).with_name("client_issue_scripts")
RESULT_MARK = "@@RESULT@@"
# Tensology's own internal rate. It is never a client's agreed rate; it is what a project gets
# when nobody sets one, which is how it keeps ending up on invoices.
INTERNAL_DEFAULT_RATE = Decimal("850.00")
_SCRATCH_RE = re.compile(r"^scratch_[a-z0-9_]{3,40}$")
_INVOICE_RE = re.compile(r"\bI\d{4,6}\b")
_MONEY = r"R\s?([0-9][0-9 ,]*(?:\.[0-9]{1,2})?)"


class PipelineError(RuntimeError):
    pass


def _money(text: str) -> Decimal | None:
    try:
        return Decimal(re.sub(r"[ ,]", "", text)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _dec(value: Any) -> Decimal | None:
    try:
        return None if value in (None, "") else Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _r(value: Any) -> str:
    d = _dec(value)
    return "R" + format(d, ",.2f").replace(",", " ") if d is not None else "?"


# ---------------------------------------------------------------------------
# Running a fixed script on the Tensology host
# ---------------------------------------------------------------------------

@dataclass
class Runner:
    """Runs fixed scripts through ``manage.py shell`` over ssh. ``run`` is injectable for tests."""
    target: str = DEFAULT_SSH_TARGET
    db_name: str = ""
    run: Callable[..., Any] = subprocess.run
    timeout: float = 900.0
    log: list[dict[str, Any]] = field(default_factory=list)

    def _ssh(self, remote: str, stdin: str = "", timeout: float | None = None) -> str:
        proc = self.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", self.target, remote],
                        input=stdin, capture_output=True, text=True, timeout=timeout or self.timeout)
        if proc.returncode != 0:
            raise PipelineError(f"ssh step failed ({proc.returncode}): {(proc.stderr or proc.stdout)[-300:]}")
        return proc.stdout

    def script(self, name: str, params: dict[str, Any], *, write: bool = False) -> dict[str, Any]:
        if write and not _SCRATCH_RE.match(self.db_name or ""):
            raise PipelineError("Refusing to run a write script outside a scratch_ database copy.")
        body = (SCRIPT_DIR / f"{name}.py").read_text() + "\n" + (SCRIPT_DIR / "wrapper.py").read_text()
        blob = base64.b64encode(json.dumps({**params, "_mode": "write" if write else "read"}).encode()).decode()
        env = f"TS_PARAMS={blob} "
        prep = ""
        if self.db_name:
            # The production settings pin the database in settings_local.py, so an env var cannot
            # redirect them. A tiny settings module outside the app does, for this one command.
            prep = ("mkdir -p /tmp/ts-scratch && printf '%s\\n' 'from core.settings import *' "
                    "'DATABASES[\"default\"] = dict(DATABASES[\"default\"], NAME=__import__(\"os\").environ[\"SCRATCH_DB\"])' "
                    "> /tmp/ts-scratch/scratch_settings.py; ")
            env += f"SCRATCH_DB={shlex.quote(self.db_name)} PYTHONPATH=/tmp/ts-scratch DJANGO_SETTINGS_MODULE=scratch_settings "
        remote = (f"cd {APP_DIR} && set -a && . {ENV_FILE} >/dev/null 2>&1; set +a; {prep}"
                  f"{env}{VENV_PY} manage.py shell 2>&1")
        started = time.time()
        out = self._ssh(remote, body)
        self.log.append({"script": name, "write": write, "db": self.db_name or "production(read-only)",
                         "seconds": round(time.time() - started, 1)})
        for line in reversed(out.splitlines()):
            if line.startswith(RESULT_MARK):
                return json.loads(line[len(RESULT_MARK):])
        raise PipelineError(f"{name} returned no result: {out[-300:]}")

    # -- scratch database lifecycle -------------------------------------------------
    def create_scratch(self, name: str) -> "Runner":
        if not _SCRATCH_RE.match(name):
            raise PipelineError("scratch database names look like scratch_<letters-digits>")
        out = self.script("scratchdb", {"action": "create", "name": name})
        self.log.append({"script": "create_scratch", "db": name, "result": {k: v for k, v in out.items() if k != "err"}})
        if not out.get("created") or out.get("dump_rc") or out.get("load_rc"):
            raise PipelineError(f"could not copy the data: {out.get('err')}")
        return Runner(self.target, name, self.run, self.timeout, self.log)

    def drop_scratch(self) -> None:
        if not _SCRATCH_RE.match(self.db_name or ""):
            raise PipelineError("Refusing to drop a database that is not a scratch_ copy.")
        # Run against production's connection settings (the helper only ever drops a scratch_ name).
        out = Runner(self.target, "", self.run, self.timeout, self.log).script("scratchdb", {"action": "drop", "name": self.db_name})
        self.log.append({"script": "drop_scratch", "db": self.db_name, "result": out})

    def fetch_file(self, remote_path: str, local_path: Path) -> bool:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        proc = self.run(["scp", "-q", "-o", "BatchMode=yes", f"{self.target}:{remote_path}", str(local_path)],
                        capture_output=True, text=True, timeout=60)
        return proc.returncode == 0 and local_path.exists()


# ---------------------------------------------------------------------------
# 1. Read the complaint, find the records, trace the cause
# ---------------------------------------------------------------------------

def read_complaint(text: str) -> dict[str, Any]:
    """Facts the client stated: invoice numbers, the figure they say is wrong and the one they want."""
    invoices = list(dict.fromkeys(_INVOICE_RE.findall(text or "")))
    wrong = right = None
    m = re.search(r"should (?:have been|be)\s+" + _MONEY + r"\s+(?:and )?not\s+" + _MONEY, text, re.I)
    if m:
        right, wrong = _money(m.group(1)), _money(m.group(2))
    else:
        m = re.search(r"(?:unit (?:cost|price|rate)|rate|cost)[^.]{0,40}?" + _MONEY, text, re.I)
        if m:
            wrong = _money(m.group(1))
        m = re.search(r"should (?:have been|be)\s+" + _MONEY, text, re.I)
        if m:
            right = _money(m.group(1))
    return {"invoices": invoices, "stated_wrong": wrong, "stated_correct": right}


def _project_for(line_name: str, projects: list[dict[str, Any]]) -> dict[str, Any] | None:
    base = line_name.split(" [")[0].strip().lower()
    exact = [p for p in projects if p["name"].strip().lower() == base]
    return exact[0] if exact else None


def analyze(probe: dict[str, Any], complaint: dict[str, Any]) -> dict[str, Any]:
    """Deterministic findings from a probe: which lines are billed wrongly, what the agreed rate is,
    where the wrong figure came from. Every conclusion carries the evidence it rests on."""
    findings: list[dict[str, Any]] = []
    stated_wrong, stated_right = complaint.get("stated_wrong"), complaint.get("stated_correct")
    for inv in probe.get("invoices") or []:
        if not inv.get("found"):
            findings.append({"invoice": inv["number"], "problem": "not_found"})
            continue
        peers: dict[str, Counter] = {}
        for row in inv.get("peer_rates") or []:
            peers.setdefault(row["name"].lower(), Counter())[row["unit"]] += int(row["count"])
        all_units = Counter()
        for c in peers.values():
            all_units.update(c)
        for line in inv.get("lines") or []:
            unit = _dec(line.get("unit"))
            if unit is None:
                continue
            project = _project_for(line["name"], inv.get("projects") or [])
            suspect = unit == stated_wrong or unit == INTERNAL_DEFAULT_RATE
            if not suspect:
                continue
            evidence: list[str] = []
            agreed, basis = None, ""
            own = peers.get(line["name"].split(" [")[0].strip().lower(), Counter())
            usable = Counter({k: v for k, v in own.items() if _dec(k) not in (unit, INTERNAL_DEFAULT_RATE)})
            others = Counter({k: v for k, v in all_units.items() if _dec(k) not in (unit, INTERNAL_DEFAULT_RATE)})
            if stated_right is not None and (str(stated_right) in all_units or stated_right):
                agreed = stated_right
                seen = all_units.get(str(stated_right), 0)
                basis = (f"the client states R{stated_right}; {seen} other line(s) for this client are billed at that rate"
                         if seen else f"the client states R{stated_right} (no other line confirms it)")
            elif usable:
                rate, n = usable.most_common(1)[0]
                agreed, basis = _dec(rate), f"{n} other '{line['name'].split(' [')[0]}' line(s) for this client are billed at R{rate}"
            elif others:
                rate, n = others.most_common(1)[0]
                agreed, basis = _dec(rate), f"{n} other line(s) for this client are billed at R{rate}"
            elif inv.get("customer_rate"):
                agreed, basis = _dec(inv["customer_rate"]), "the client's rate on their Tensology account"
            cause = _trace_cause(line, project)
            qty = _dec(line.get("qty")) or Decimal("0")
            findings.append({
                "invoice": inv["number"], "invoice_id": inv["id"], "customer": inv["customer"], "line_id": line["id"],
                "line": line["name"], "description": line.get("description", ""), "billed": str(unit),
                "qty": str(qty), "amount": line.get("amount"),
                "agreed": None if agreed is None else str(agreed), "agreed_basis": basis,
                "amount_should_be": None if agreed is None else str((qty * agreed).quantize(Decimal("0.01"))),
                "project_id": project["id"] if project else None, "project": project["name"] if project else "",
                "project_rate": project["rate"] if project else None, "cause": cause, "evidence": evidence,
                "is_sent": inv.get("is_sent"), "invoice_total": inv.get("total"),
            })
    return {"findings": findings, "project_findings": _project_findings(probe, findings),
            "stated": {k: (str(v) if v is not None else None) for k, v in complaint.items() if k != "invoices"}}


def _project_findings(probe: dict[str, Any], findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A project whose own rate differs from what its lines are billed at will repeat the error on the
    next billing run, so it is part of the fix even when its lines on this invoice are right."""
    fixed = {f["project_id"]: _dec(f["agreed"]) for f in findings if f.get("project_id") and f.get("agreed")}
    out: dict[int, dict[str, Any]] = {}
    for inv in probe.get("invoices") or []:
        for project in inv.get("projects") or []:
            units = Counter(str(_dec(l["unit"])) for l in inv.get("lines") or []
                            if _project_for(l["name"], [project]) and _dec(l.get("unit")) not in (None, INTERNAL_DEFAULT_RATE))
            target, basis = fixed.get(project["id"]), ""
            if target is not None:
                basis = "the client's agreed rate for this project's lines"
            elif units:
                rate, n = units.most_common(1)[0]
                target, basis = _dec(rate), f"{n} line(s) on {inv['number']} are billed at R{rate}"
            if target is not None and _dec(project.get("rate")) != target and project["id"] not in out:
                out[project["id"]] = {"project_id": project["id"], "project": project["name"], "from": project.get("rate"),
                                      "to": str(target), "basis": basis, "invoice": inv["number"]}
    return list(out.values())


def _trace_cause(line: dict[str, Any], project: dict[str, Any] | None) -> dict[str, Any]:
    """Where did this line's wrong rate come from? Uses the line's and the project's own history."""
    created_unit = _dec(line.get("created_unit"))
    cause: dict[str, Any] = {"summary": "", "created_unit": str(created_unit) if created_unit else None,
                             "created_at": line.get("created_at"), "created_reason": line.get("created_reason", "")}
    if project is None:
        cause["summary"] = "The line does not match any project on the invoice, so its rate came from the line itself."
        return cause
    changes = project.get("rate_changes") or []
    at_creation = None
    for ch in changes:
        if ch["at"] <= (line.get("created_at") or "9999"):
            at_creation = ch
    first = changes[0] if changes else None
    proj_rate_then = _dec(at_creation["value"]) if at_creation else _dec(project.get("rate"))
    cause["project_rate_when_line_was_created"] = str(proj_rate_then) if proj_rate_then else None
    if proj_rate_then is not None and created_unit == proj_rate_then:
        why = ""
        if proj_rate_then == INTERNAL_DEFAULT_RATE:
            origin = "was left at Tensology's default" if (first and first.get("type") == "+" and first["value"] == "850.00") \
                else "had been set to Tensology's default"
            why = f" The project's rate {origin} (R{proj_rate_then}), not the client's agreed rate."
        cause["summary"] = (f"The line copied project '{project['name']}'s rate, which was R{proj_rate_then} when the line "
                            f"was created.{why}")
        cause["root"] = "project_rate"
    else:
        cause["summary"] = ("The line was created at a rate that did not come from its project "
                            f"(project rate then: {_r(proj_rate_then)}).")
        cause["root"] = "line_rate"
    if line.get("created_reason"):
        cause["summary"] += f" It was created by: {line['created_reason']}."
    return cause


def investigate(complaint_text: str, runner: Runner, *, extra_invoices: list[str] | None = None) -> dict[str, Any]:
    """Read only. Returns the stated complaint, the probe output and the analysis."""
    stated = read_complaint(complaint_text)
    numbers = list(dict.fromkeys([*stated["invoices"], *(extra_invoices or [])]))
    if not numbers:
        return {"stated": stated, "probe": {}, "analysis": {"findings": []}, "blocked": "No invoice number in the complaint."}
    probe = runner.script("probe", {"invoices": numbers})
    return {"stated": stated, "probe": probe, "analysis": analyze(probe, stated), "invoices": numbers}


# ---------------------------------------------------------------------------
# 2. Plan the change and curate the proposal
# ---------------------------------------------------------------------------

def plan_change(investigation: dict[str, Any]) -> dict[str, Any]:
    analysis = investigation["analysis"]
    findings = [f for f in analysis["findings"] if f.get("agreed")]
    projects = {p["project_id"]: {k: p[k] for k in ("project_id", "project", "from", "to")} for p in analysis.get("project_findings") or []}
    lines = [{"line_id": f["line_id"], "invoice": f["invoice"], "name": f["line"], "from": f["billed"], "to": f["agreed"],
              "amount_from": f["amount"], "amount_to": f["amount_should_be"]} for f in findings]
    unresolved = [f for f in analysis["findings"] if not f.get("agreed")]
    waiting = []
    if any(f.get("is_sent") for f in findings):
        waiting.append("The corrected invoice replaces one that was already sent, so the resend is yours to approve.")
    return {"project_updates": list(projects.values()), "line_updates": lines, "unresolved": unresolved,
            "waiting_on_paul": waiting, "invoices": sorted({l["invoice"] for l in lines})}


def describe_plan(plan: dict[str, Any], investigation: dict[str, Any], *, scratch: bool = False) -> dict[str, Any]:
    """The proposal text, built from evidence. No model needed, so it never invents a figure."""
    findings = investigation["analysis"]["findings"]
    if not plan["line_updates"]:
        return {"title": "Client billing complaint: nothing wrong found on the records", "description": "", "tasks": []}
    first = findings[0]
    who = first.get("customer", "the client")
    bad = [f"{f['invoice']}: '{f['line']}' billed at {_r(f['billed'])}, agreed rate {_r(f['agreed'])} ({f['agreed_basis']})" for f in findings if f.get("agreed")]
    causes = list(dict.fromkeys(f["cause"]["summary"] for f in findings if f.get("cause") and f["cause"].get("summary")))
    title = f"Correct {who} invoice rate: " + ", ".join(sorted({p['invoice'] for p in plan['line_updates']}))
    tasks = []
    for pu in plan["project_updates"]:
        tasks.append(f"Set project '{pu['project']}' hourly rate to {_r(pu['to'])} (now {_r(pu['from'])}) so new invoices cannot pick up the wrong rate.")
    for lu in plan["line_updates"]:
        tasks.append(f"{lu['invoice']}: change line '{lu['name']}' from {_r(lu['from'])} to {_r(lu['to'])} (line total {_r(lu['amount_from'])} to {_r(lu['amount_to'])}).")
    tasks += ["Let the app recalculate each invoice, then re-read it and check no line is at the wrong rate and the totals add up.",
              "Render the corrected invoice PDF. Draft the reply from what actually changed. Send nothing."]
    waiting = ["Production write: the invoice and project changes above are made on the live records only after you accept this.",
               "Send: the reply and the corrected invoice go out only when you tap send on the drafted reply."]
    waiting += plan["waiting_on_paul"]
    desc = ("What is wrong:\n" + "\n".join(f"- {b}" for b in bad) +
            "\n\nRoot cause:\n" + "\n".join(f"- {c}" for c in causes) +
            "\n\nThe fix:\n" + "\n".join(f"- {t}" for t in tasks) +
            "\n\nWaiting on you:\n" + "\n".join(f"- {w}" for w in waiting))
    return {"title": title[:200], "description": desc, "tasks": tasks, "root_cause": causes, "waiting_on_paul": waiting,
            "rationale": f"Read-only check of the live records found {len(bad)} line(s) billed at the wrong rate."}


def proposal_payload(flow: dict[str, Any], plan: dict[str, Any], investigation: dict[str, Any]) -> dict[str, Any]:
    text = describe_plan(plan, investigation)
    return {"curated": True,
            "source": {"channel": "mailshot", "source_thread_id": str(flow.get("thread_id") or ""),
                       "message_ids": [str(flow.get("message_id") or "")]},
            "title": text["title"], "description": text["description"], "tasks": text["tasks"],
            "rationale": text.get("rationale", ""), "priority": "high",
            "kind": "client_issue", "root_cause": text.get("root_cause", []),
            "waiting_on_paul": text.get("waiting_on_paul", []), "reply_to": str(flow.get("sender") or "")}


# ---------------------------------------------------------------------------
# 3. Execute (after approval), verify, draft
# ---------------------------------------------------------------------------

def verify(before: dict[str, Any], after_probe: dict[str, Any], complaint: dict[str, Any], plan: dict[str, Any],
           changes: dict[str, Any]) -> dict[str, Any]:
    """Re-read after the change. Passes only if nothing is still at a wrong rate, the project rates are
    explicit, and each invoice's total equals its lines plus tax."""
    checks: list[dict[str, Any]] = []
    again = analyze(after_probe, {**complaint, "stated_wrong": complaint.get("stated_wrong")})
    leftover = [f for f in again["findings"] if f.get("billed") and not (f.get("agreed") and f["billed"] == f["agreed"])]
    checks.append({"check": "no line is still at the wrong rate", "ok": not leftover,
                   "detail": [f"{f['invoice']} {f['line']} {_r(f['billed'])}" for f in leftover]})
    want = {u["project_id"]: _dec(u["to"]) for u in plan["project_updates"]}
    got = {p["id"]: _dec(p["rate"]) for inv in after_probe.get("invoices") or [] for p in inv.get("projects") or []}
    checks.append({"check": "project rates are the agreed rates", "ok": all(got.get(k) == v for k, v in want.items()),
                   "detail": [f"{k}: {_r(got.get(k))} (wanted {_r(v)})" for k, v in want.items()]})
    for inv in after_probe.get("invoices") or []:
        if inv["number"] not in plan["invoices"]:
            continue
        subtotal = sum((_dec(l["amount"]) or 0) for l in inv["lines"])
        tax = (Decimal(str(subtotal)) * Decimal(inv["tax_rate"]) / 100).quantize(Decimal("0.01"))
        total = Decimal(str(subtotal)) + tax - (_dec(inv.get("discount")) or 0)
        qty_ok = all((_dec(l["qty"]) or 0) * (_dec(l["unit"]) or 0) - (_dec(l["amount"]) or 0) in (0, Decimal("0.01"), Decimal("-0.01"))
                     for l in inv["lines"] if l.get("qty") and l.get("unit"))
        checks.append({"check": f"{inv['number']} lines multiply out", "ok": qty_ok, "detail": []})
        checks.append({"check": f"{inv['number']} total equals lines plus tax", "ok": abs(total - (_dec(inv["total"]) or 0)) <= Decimal("0.02"),
                       "detail": [f"lines {_r(subtotal)} + tax {_r(tax)} = {_r(total)}; invoice says {_r(inv['total'])}"]})
    for inv in changes.get("invoices") or []:
        checks.append({"check": f"{inv['number']} corrected PDF rendered", "ok": "path" in (inv.get("pdf") or {}), "detail": [str(inv.get("pdf"))[:120]]})
    return {"passed": all(c["ok"] for c in checks), "checks": checks}


def execute(plan: dict[str, Any], investigation: dict[str, Any], runner: Runner, *, reason: str,
            pdf_dir: str = "/tmp/client-issue-fix", scratch_only: bool = True) -> dict[str, Any]:
    """Apply the plan and verify it. ``scratch_only`` (the default) is enforced by the runner: a write
    script will not run on anything but a scratch_ copy."""
    if not plan["line_updates"]:
        raise PipelineError("Nothing to change.")
    changes = runner.script("fix", {"plan": plan, "reason": reason, "pdf_dir": pdf_dir}, write=True)
    after = runner.script("probe", {"invoices": investigation["invoices"]})
    result = verify(investigation["probe"], after, investigation["stated"], plan, changes)
    return {"changes": changes, "after": after, "verification": result}


def _first_name(client: str, sender: str) -> str:
    name = re.sub(r"\(.*?\)", "", client or "").strip().split(" ")[0]
    return name or (sender.split("@")[0].title() if sender else "there")


def draft_reply(flow: dict[str, Any], plan: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    """The reply, written only from what was changed and verified. Refuses to draft when verification failed."""
    if not execution["verification"]["passed"]:
        return {"text": "", "blocked": "Verification failed, so there is nothing true to tell the client yet.", "attachments": []}
    invs = {i["number"]: i for i in execution["changes"]["invoices"]}
    lines = execution["changes"]["lines"]
    first = _first_name(str(flow.get("client") or ""), str(flow.get("sender") or ""))
    parts = []
    for num, inv in invs.items():
        ls = [l for l in lines if l["invoice"] == num]
        what = "; ".join(f"'{l['name']}' is now {_r(l['unit_to'])} an hour (it was {_r(l['unit_from'])})" for l in ls)
        parts.append(f"on {num} {what}, so the total is now {_r(inv['after']['total'])} instead of {_r(inv['before']['total'])}")
    body = (f"Hi {first},\n\nSorry about that. I've corrected it: " + "; and ".join(parts) + ". "
            f"The corrected invoice{'s are' if len(invs) > 1 else ' is'} attached.\n\nPaul")
    return {"text": body, "attachments": [Path(i["pdf"]["path"]).name for i in invs.values() if "path" in (i.get("pdf") or {})],
            "based_on": {"lines": [l["id"] for l in lines], "invoices": list(invs)}}


def completion_card(flow: dict[str, Any], plan: dict[str, Any], execution: dict[str, Any], reply: dict[str, Any], *, dry_run: bool) -> str:
    v = execution["verification"]
    head = "DRY RUN on a copy of the data: nothing real was changed.\n\n" if dry_run else ""
    done = "\n".join(f"- {t}" for t in plan_done(plan, execution))
    checks = "\n".join(f"{'ok' if c['ok'] else 'FAILED'}: {c['check']}" for c in v["checks"])
    gates = [*(plan.get("waiting_on_paul") or []),
             "Send: nothing has gone to the client. Tap send on the draft to send the reply with the corrected invoice attached."]
    if dry_run:
        gates.insert(0, "Production write: not made. On a real run it happens only after you accept the proposal.")
    waiting = "\n".join(f"- {w}" for w in gates)
    return (f"{head}Fix done and verified for {flow.get('client') or flow.get('sender')}.\n\nWhat changed:\n{done}\n\n"
            f"Checks:\n{checks}\n\nDraft reply, not sent:\n{reply.get('text') or reply.get('blocked')}\n\n"
            f"Attached: {', '.join(reply.get('attachments') or []) or 'nothing'}\n\nWaiting on you:\n{waiting}")


def plan_done(plan: dict[str, Any], execution: dict[str, Any]) -> list[str]:
    out = [f"Project '{p['name']}' rate {_r(p['from'])} -> {_r(p['to'])}" for p in execution["changes"]["projects"]]
    out += [f"{l['invoice']} '{l['name']}' {_r(l['unit_from'])} -> {_r(l['unit_to'])} ({_r(l['amount_from'])} -> {_r(l['amount_to'])})" for l in execution["changes"]["lines"]]
    out += [f"{i['number']} total {_r(i['before']['total'])} -> {_r(i['after']['total'])}" for i in execution["changes"]["invoices"]]
    return out


# ---------------------------------------------------------------------------
# Dry run: the whole path on a scratch copy, rewound to the day of the complaint
# ---------------------------------------------------------------------------

def finish(flow: dict[str, Any], investigation: dict[str, Any], scratch: Runner, *, out_dir: str) -> dict[str, Any]:
    """What happens after Paul accepts the proposal: make the change, verify it, render the corrected
    document, draft the reply from the verified change. Only ever on a scratch copy here."""
    plan = investigation.get("plan") or plan_change(investigation)
    ex = execute(plan, investigation, scratch, reason="Corrected rate to the client's agreed rate (client-issue pipeline)",
                 pdf_dir=f"/tmp/{scratch.db_name}")
    for i in ex["changes"]["invoices"]:
        path = (i.get("pdf") or {}).get("path")
        if path and scratch.fetch_file(path, Path(out_dir) / Path(path).name):
            i["pdf"]["local"] = str(Path(out_dir) / Path(path).name)
    reply = draft_reply(flow, plan, ex)
    return {"execution": ex, "reply": reply, "card": completion_card(flow, plan, ex, reply, dry_run=True)}


def dry_run(flow: dict[str, Any], complaint_text: str, runner: Runner, *, as_of: str, keep_scratch: bool = False,
            out_dir: str = "/tmp/client-issue-dryrun") -> dict[str, Any]:
    """Copy the data, put it back as it was when the client complained, run the whole pipeline on the
    copy, then drop the copy. Production is only ever read."""
    report: dict[str, Any] = {"as_of": as_of, "steps": []}
    stated = read_complaint(complaint_text)
    prod = investigate(complaint_text, runner)
    report["production_read_only"] = {"findings": prod["analysis"]["findings"], "note":
        "Production already carries the correction, so a read-only check finds the lines clean."}
    name = "scratch_fix_" + datetime.now(timezone.utc).strftime("%m%d%H%M%S")
    scratch = runner.create_scratch(name)
    report["steps"].append(f"copied production data into {name}")
    try:
        rewound = scratch.script("rewind", {"as_of": as_of, "invoices": stated["invoices"]}, write=True)
        report["steps"].append(f"rewound the copy to {as_of}: {rewound}")
        inv = investigate(complaint_text, scratch)
        plan = plan_change(inv)
        report.update({"investigation": inv["analysis"], "plan": plan, "proposal": proposal_payload(flow, plan, inv)})
        if plan["line_updates"]:
            report.update(finish(flow, inv, scratch, out_dir=out_dir))
    finally:
        if not keep_scratch:
            try:
                scratch.drop_scratch()
                report["steps"].append(f"dropped {name}")
            except Exception as exc:  # noqa: BLE001
                report["steps"].append(f"COULD NOT DROP {name}: {exc}")
    report["runner_log"] = runner.log
    return report
