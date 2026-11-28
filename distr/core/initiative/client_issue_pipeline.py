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


class ApprovalError(PipelineError):
    """The proposal was not accepted, or is not the one the plan was made for."""


_APPROVAL_KEY = object()


@dataclass(frozen=True)
class Approval:
    """Proof that Paul accepted this exact proposal. Only ``check_approval`` makes one."""
    proposal_id: int
    plan_hash: str
    status: str
    _key: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._key is not _APPROVAL_KEY:
            raise ApprovalError("An Approval can only come from check_approval().")


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

    def script(self, name: str, params: dict[str, Any], *, write: bool = False, _mode: str = "") -> dict[str, Any]:
        if write and not _SCRATCH_RE.match(self.db_name or ""):
            raise PipelineError("Refusing to run a write script outside a scratch_ database copy.")
        body = (SCRIPT_DIR / f"{name}.py").read_text() + "\n" + (SCRIPT_DIR / "wrapper.py").read_text()
        blob = base64.b64encode(json.dumps({**params, "_mode": _mode or ("write" if write else "read")}).encode()).decode()
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

    def approved_script(self, name: str, params: dict[str, Any], approval: "Approval") -> dict[str, Any]:
        """The only way to run a write on a real database: needs an Approval that was checked against
        the accepted proposal, and only the two fixed live scripts."""
        if not isinstance(approval, Approval):
            raise ApprovalError("A live write needs a verified approval for the accepted proposal.")
        if name not in ("apply_live", "revert_live"):
            raise ApprovalError(f"{name} is not an approved live script.")
        params = {**params, "proposal_id": approval.proposal_id, "plan_hash": approval.plan_hash,
                  "confirm": f"{'APPLY' if name == 'apply_live' else 'REVERT'} {approval.proposal_id} {approval.plan_hash}"}
        return self.script(name, params, _mode="approved")

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
    waiting = ["Production write: the invoice and project changes above are made on the live records only after you accept this, "
               "by Decisions in one guarded transaction (current values checked first, backed up, verified, rolled back on any failure). "
               "The development thread started by Accept must not change these records by hand.",
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
    fingerprint = plan_hash(plan)
    record = {"flow": {k: flow.get(k) for k in ("thread_id", "message_id", "sender", "client")}, "plan": _plain(plan),
              "tasks": list(text["tasks"]), "stated": investigation.get("stated") or {},
              "invoices": list(investigation.get("invoices") or plan.get("invoices") or [])}
    return {"curated": True, "_record": record,
            "source": {"channel": "mailshot", "source_thread_id": str(flow.get("thread_id") or ""),
                       "message_ids": [str(flow.get("message_id") or "")]},
            "title": text["title"], "description": text["description"], "tasks": text["tasks"],
            "rationale": (text.get("rationale", "") + f" Plan fingerprint {fingerprint}.").strip(), "priority": "high",
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


# ---------------------------------------------------------------------------
# 4. The approved live write (only after Paul accepts that specific proposal)
# ---------------------------------------------------------------------------
#
# Gate:   check_approval() reads the proposal back from Tensology: it must be accepted, carry this
#         plan's fingerprint, and list exactly the tasks Paul saw. One accept applies one proposal:
#         the record is claimed once (state published -> applying -> applied/aborted/reverted).
# Safety: a fresh read of the live records must still match what the plan expected; the client-rate
#         rule is checked (R850 only where it is the client's agreed rate); the whole database is
#         dumped; the write runs in ONE transaction that checks again, backs the rows up, applies,
#         re-reads, renders the documents and logs the rates (billing.rates and the client history).
#         Any failure rolls back. A check that fails after the commit restores the saved rows.
# Output: the reply is drafted from the verified live result and stays a draft on the card.



def _store_dir() -> Path:
    import os

    return Path(os.environ.get("DECISIONS_DB_DIR") or Path.home() / ".decisions") / "client_issue"


PROD_DB = "tensology"
BACKUP_DIR = "/var/backups/tensology"
ACCEPTED_STATUSES = {"sent_to_decisions", "approved"}


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def plan_hash(plan: dict[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(_plain(plan), sort_keys=True).encode()).hexdigest()[:16]


def _record_path(proposal_id: int) -> Path:
    return _store_dir() / f"proposal-{int(proposal_id)}.json"


def save_plan_record(proposal_id: int, record: dict[str, Any]) -> dict[str, Any]:
    """Remember what was proposed, so that accepting proposal N can only ever apply plan N."""
    _store_dir().mkdir(parents=True, exist_ok=True)
    path = _record_path(proposal_id)
    if path.exists():
        return json.loads(path.read_text())
    rec = {**record, "proposal_id": int(proposal_id), "plan_hash": plan_hash(record["plan"]), "state": "published",
           "created_at": datetime.now(timezone.utc).isoformat()}
    path.write_text(json.dumps(rec, indent=1, default=str))
    path.chmod(0o600)
    return rec


def load_plan_record(proposal_id: int) -> dict[str, Any] | None:
    try:
        return json.loads(_record_path(proposal_id).read_text())
    except (OSError, ValueError):
        return None


def _set_state(rec: dict[str, Any], state: str, **extra: Any) -> None:
    rec.update({"state": state, "updated_at": datetime.now(timezone.utc).isoformat(), **extra})
    _record_path(rec["proposal_id"]).write_text(json.dumps(rec, indent=1, default=str))


def _claim(proposal_id: int) -> bool:
    import os

    try:
        os.close(os.open(str(_record_path(proposal_id).with_suffix(".lock")), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        return True
    except FileExistsError:
        return False


def fetch_proposal(proposal_id: int) -> dict[str, Any]:
    from distr.core.tensology_client import configured_tensology_client

    return configured_tensology_client(source="client-issue").get(f"work-requests/{int(proposal_id)}") or {}


def check_approval(proposal_id: int, record: dict[str, Any], fetch: Callable[[int], dict[str, Any]] | None = None) -> Approval:
    """Raise unless Tensology says this proposal was accepted and is the one this plan belongs to."""
    p = (fetch or fetch_proposal)(int(proposal_id))
    status = str(p.get("stored_status") or p.get("status") or "").strip().lower()
    if status not in ACCEPTED_STATUSES:
        raise ApprovalError(f"proposal #{proposal_id} is '{status or 'unknown'}', not accepted")
    if plan_hash(record["plan"]) != record["plan_hash"] or record["plan_hash"] not in str(p.get("rationale") or ""):
        raise ApprovalError(f"proposal #{proposal_id} does not carry this plan's fingerprint")
    if [str(t) for t in p.get("tasks") or []] != [str(t) for t in record["tasks"]]:
        raise ApprovalError(f"proposal #{proposal_id} was edited after it was filed; the plan is not what was approved")
    return Approval(int(proposal_id), record["plan_hash"], status, _APPROVAL_KEY)


def guard_rates(plan: dict[str, Any], probe: dict[str, Any]) -> list[str]:
    """The client-rate rule. R850 is Tensology's own rate: never a target unless it is the client's
    explicit agreed rate (their account rate). Targets must be positive."""
    problems: list[str] = []
    cust = {inv["number"]: _dec(inv.get("customer_rate")) for inv in probe.get("invoices") or [] if inv.get("found")}
    for u in plan["line_updates"]:
        to = _dec(u["to"])
        if to is None or to <= 0:
            problems.append(f"{u['invoice']} '{u['name']}': no valid target rate")
        elif to == INTERNAL_DEFAULT_RATE and cust.get(u["invoice"]) != INTERNAL_DEFAULT_RATE:
            problems.append(f"{u['invoice']} '{u['name']}': {_r(to)} is Tensology's own rate, not this client's agreed rate")
    allowed = any(c == INTERNAL_DEFAULT_RATE for c in cust.values())
    for u in plan["project_updates"]:
        to = _dec(u["to"])
        if to is None or to <= 0:
            problems.append(f"project '{u['project']}': no valid target rate")
        elif to == INTERNAL_DEFAULT_RATE and not allowed:
            problems.append(f"project '{u['project']}': {_r(to)} is Tensology's own rate, not the client's agreed rate")
    return problems


def check_current_values(plan: dict[str, Any], probe: dict[str, Any]) -> list[str]:
    """The live records must still be exactly what the proposal described."""
    problems: list[str] = []
    lines = {l["id"]: l for inv in probe.get("invoices") or [] for l in inv.get("lines") or []}
    projects = {p["id"]: p for inv in probe.get("invoices") or [] for p in inv.get("projects") or []}
    for u in plan["line_updates"]:
        l = lines.get(u["line_id"])
        if l is None:
            problems.append(f"line {u['line_id']} ({u['invoice']}) was not found")
        elif _dec(l.get("unit")) != _dec(u["from"]) or _dec(l.get("amount")) != _dec(u["amount_from"]):
            problems.append(f"{u['invoice']} '{u['name']}' is now {_r(l.get('unit'))} / {_r(l.get('amount'))}, "
                            f"the proposal expected {_r(u['from'])} / {_r(u['amount_from'])}")
    for u in plan["project_updates"]:
        p = projects.get(u["project_id"])
        if p is None or _dec(p.get("rate")) != _dec(u["from"]):
            problems.append(f"project '{u['project']}' is now {_r((p or {}).get('rate'))}, the proposal expected {_r(u['from'])}")
    return problems


def backup_database(runner: Runner, approval: Approval, *, backup_dir: str = BACKUP_DIR) -> dict[str, Any]:
    """Full PostgreSQL dump (custom format, mode 600) before the write; checked by listing it."""
    if not isinstance(approval, Approval):
        raise ApprovalError("A backup for a live write needs the verified approval.")
    db = runner.db_name or PROD_DB
    if not re.match(r"^[a-z0-9_]{3,40}$", db):
        raise PipelineError("unexpected database name")
    path = f"{backup_dir}/client-issue-{approval.proposal_id}-{time.strftime('%Y%m%d-%H%M%S')}.dump"
    out = runner._ssh(f"set -e; umask 077; mkdir -p {backup_dir}; sudo -u postgres pg_dump -Fc {db} > {path}; "
                      f"test -s {path}; echo SIZE=$(stat -c %s {path}); echo ITEMS=$(pg_restore -l {path} | wc -l)")
    size = int(re.search(r"SIZE=(\d+)", out).group(1)) if "SIZE=" in out else 0
    items = int(re.search(r"ITEMS=(\d+)", out).group(1)) if "ITEMS=" in out else 0
    if size < 1000 or items < 10:
        raise PipelineError("the database backup looks empty; not writing anything")
    runner.log.append({"script": "backup_database", "db": db, "path": path, "bytes": size, "items": items})
    return {"path": path, "bytes": size, "items": items, "db": db}


def apply_approved(proposal_id: int, *, runner: Runner | None = None, fetch: Callable | None = None,
                   say: Callable[[str, str], Any] | None = None, notify: Callable[[str], Any] | None = None,
                   settings: dict[str, Any] | None = None, out_dir: str | None = None,
                   backup_dir: str = BACKUP_DIR) -> dict[str, Any]:
    """Apply the plan of ONE accepted proposal to the live records. Never raises; returns
    {status, ...}. Statuses: not_a_client_issue, already_<state>, not_approved, drifted, blocked,
    aborted, reverted, applied. Sends nothing to a client."""
    record = load_plan_record(proposal_id)
    if record is None:
        return {"status": "not_a_client_issue"}
    flow = record.get("flow") or {}
    job = f"mail:{str(flow.get('message_id') or proposal_id)[:40]}"
    if say is None:
        from distr.core.initiative import job_progress

        say = lambda kind, text: job_progress.report(job, kind, text, title="Mail to proposal")  # noqa: E731

    def tell(text: str) -> None:
        try:
            if notify is not None:
                notify(text)
            else:
                from distr.core.initiative import outcome_flow as of

                of.send_telegram(text, settings=settings)
        except Exception:  # noqa: BLE001
            logger.warning("client issue: could not tell Paul", exc_info=True)

    if record.get("state") != "published" or not _claim(proposal_id):
        return {"status": "already_" + str(record.get("state") or "claimed")}
    plan = record["plan"]
    runner = runner or Runner(target=str((settings or {}).get(SSH_TARGET_KEY) or DEFAULT_SSH_TARGET))
    out_dir = out_dir or str(_store_dir() / "out" / f"proposal-{int(proposal_id)}")
    report: dict[str, Any] = {"proposal_id": int(proposal_id), "steps": []}

    def stop(status: str, kind: str, why: str, **extra: Any) -> dict[str, Any]:
        _set_state(record, status, why=why, **extra)
        say(kind, why)
        tell(f"Proposal #{proposal_id}: I did not change anything on the live records. {why}" if status != "reverted"
             else f"Proposal #{proposal_id}: {why}")
        return {**report, "status": status, "why": why, **extra}

    try:
        _set_state(record, "applying")
        say("picked_up", f"your accepted proposal #{proposal_id}: applying its fix to the live records")
        say("step", "checking the proposal you accepted is the one I planned")
        approval = check_approval(proposal_id, record, fetch)
        say("step", "reading the live records again (read only)")
        probe = runner.script("probe", {"invoices": record["invoices"]})
        problems = guard_rates(plan, probe)
        if problems:
            return stop("blocked", "blocked", "Client-rate rule: " + "; ".join(problems))
        problems = check_current_values(plan, probe)
        if problems:
            return stop("drifted", "blocked", "The records changed since the proposal: " + "; ".join(problems)
                        + ". Nothing was written; ask me to look again.")
        say("step", "backing up the database before the write")
        backup = backup_database(runner, approval, backup_dir=backup_dir)
        report["database_backup"] = backup
        say("step", "writing in one transaction: check, back up rows, change, verify, render")
        pdf_dir = f"/tmp/client-issue-{int(proposal_id)}"
        changes = runner.approved_script("apply_live", {"plan": plan, "reason": "Corrected to the client's agreed rate",
                                                         "pdf_dir": pdf_dir, "backup_dir": backup_dir}, approval)
        if not changes.get("ok"):
            return stop("aborted", "failed", f"The write was refused or rolled back at '{changes.get('stage')}': "
                        + "; ".join(changes.get("problems") or ["unknown"]), changes=changes, database_backup=backup)
        report["changes"] = changes
        say("step", "re-reading the committed records to verify")
        after = runner.script("probe", {"invoices": record["invoices"]})
        stated = {**(record.get("stated") or {}), "stated_wrong": _dec((record.get("stated") or {}).get("stated_wrong")),
                  "stated_correct": _dec((record.get("stated") or {}).get("stated_correct"))}
        verification = verify(probe, after, stated, plan, changes)
        if not verification["passed"]:
            reverted = runner.approved_script("revert_live", {"backup": changes.get("backup")}, approval)
            failed = "; ".join(c["check"] for c in verification["checks"] if not c["ok"])
            return stop("reverted" if reverted.get("ok") else "failed", "failed",
                        f"The check after the write failed ({failed}); " + ("I restored the saved rows." if reverted.get("ok")
                        else f"restoring failed ({'; '.join(reverted.get('problems') or [])}). Restore from {backup['path']}."),
                        changes=changes, database_backup=backup, restore=reverted)
        execution = {"changes": changes, "after": after, "verification": verification}
        for i in changes["invoices"]:
            path = (i.get("pdf") or {}).get("path")
            if path and runner.fetch_file(path, Path(out_dir) / Path(path).name):
                i["pdf"]["local"] = str(Path(out_dir) / Path(path).name)
        reply = draft_reply(flow, plan, execution)
        card = (f"Live fix applied for accepted proposal #{proposal_id}.\nBackups: database {backup['path']} ({backup['bytes']} bytes); "
                f"rows {changes.get('backup')}. Logged in billing.rates and the client's history.\n\n"
                + completion_card(flow, plan, execution, reply, dry_run=False))
        _set_state(record, "applied", reply=reply, card=card, database_backup=backup, changes=changes, verification=verification)
        say("done", f"fixed and verified: {len(changes['lines'])} line(s), {len(changes['invoices'])} invoice(s); draft reply is on your card, nothing sent")
        tell(card)
        return {**report, "status": "applied", "execution": execution, "reply": reply, "card": card}
    except ApprovalError as exc:
        return stop("not_approved", "blocked", f"Not applied: {exc}.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("client issue: live fix failed (%s)", type(exc).__name__, exc_info=True)
        return stop("failed", "failed", f"The live fix stopped with {type(exc).__name__}: {str(exc)[:160]}. "
                    "If a write had started it ran in a transaction and was rolled back.")


def on_proposal_accepted(proposal_id: Any, *, background: bool = True) -> bool:
    """Called when Paul accepts a proposal (Telegram or Incoming). Does nothing unless the proposal
    is one this pipeline filed. Returns True when it started the live fix."""
    try:
        pid = int(str(proposal_id).lstrip("#"))
    except (TypeError, ValueError):
        return False
    record = load_plan_record(pid)
    if record is None or record.get("state") != "published":
        return False
    if not background:
        apply_approved(pid)
        return True
    import threading

    threading.Thread(target=apply_approved, args=(pid,), name=f"client-issue-fix-{pid}", daemon=True).start()
    return True
