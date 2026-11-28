"""The guarded live write of the client-issue pipeline, against a fake Tensology host.

No ssh, no database, no Telegram, no mail. Proves the gates: nothing is written unless the accepted
proposal is the one the plan was made for, the live values still match, the client-rate rule holds,
the database is backed up first, and a failed check rolls back / restores. The end-to-end test on a
real scratch database copy lives in the dispatch report (it needs the Tensology host).
"""

import json

import pytest

import distr.core.initiative.client_issue_pipeline as pipe
import distr.core.initiative.client_mail_watch as w
from tests.initiative.test_client_issue_pipeline import SYNTH_COMPLAINT, michelle_probe
from tests.initiative.test_client_mail_watch import env  # noqa: F401

REAL_INVESTIGATE = pipe.investigate  # the shared env fixture blocks it; this file feeds it a fake host
FLOW = {"thread_id": "t1", "message_id": "m1", "sender": "b@example.com", "client": "Client B"}
BACKUP_OUT = "SIZE=50000\nITEMS=1600\n"


class Host:
    """ssh stand-in. Probe answers come from a queue (before the write, then after it)."""
    def __init__(self, probes, apply_result=None, revert_result=None):
        self.probes, self.calls = list(probes), []
        self.apply_result, self.revert_result = apply_result, revert_result or {"ok": True, "restored": {"projects": 2, "lines": 3, "invoices": 1}}

    def __call__(self, argv, input="", **kw):
        text = input or ""
        self.calls.append(self._tag(text, argv))
        if "scp" in argv[0]:
            return type("P", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        if "pg_dump" in " ".join(argv):
            return self._ok(BACKUP_OUT)
        if "peer_rates" in text:
            return self._ok("@@RESULT@@" + json.dumps(self.probes.pop(0)))
        if "APPROVED LIVE WRITE" in text:
            return self._ok("@@RESULT@@" + json.dumps(self.apply_result))
        if "Put back the rows" in text:
            return self._ok("@@RESULT@@" + json.dumps(self.revert_result))
        return type("P", (), {"returncode": 1, "stdout": "", "stderr": "no answer"})()

    @staticmethod
    def _ok(out):
        return type("P", (), {"returncode": 0, "stdout": out, "stderr": ""})()

    @staticmethod
    def _tag(text, argv):
        if "pg_dump" in " ".join(argv):
            return "backup"
        return "apply" if "APPROVED LIVE WRITE" in text else "revert" if "Put back the rows" in text else "probe" if "peer_rates" in text else "other"

    def tags(self):
        return [c for c in self.calls if c in ("apply", "revert", "probe", "backup")]


def live_changes():
    return {"ok": True, "written": True, "backup": "/var/backups/tensology/client-issue-501-rows.json",
            "projects": [{"id": 194, "name": "Trackly (Old)", "from": "850.00", "to": "550.00"}],
            "lines": [{"id": 3, "invoice": "I0738", "name": "Trackly (Old)", "unit_from": "850.00", "unit_to": "550.00",
                       "amount_from": "263.50", "amount_to": "170.50"}],
            "invoices": [{"number": "I0738", "id": 11990, "before": {"subtotal": "1000.00", "tax": "0.00", "total": "1000.00"},
                          "after": {"subtotal": "907.00", "tax": "0.00", "total": "907.00"}, "is_sent": False,
                          "pdf": {"path": "/tmp/client-issue-501/I0738-corrected.pdf", "bytes": 4000}}]}


@pytest.fixture
def filed(env, monkeypatch):  # noqa: F811
    """A proposal as the watch files it: plan from the live (wrong) records, record saved under id 501."""
    monkeypatch.setattr(pipe, "investigate", REAL_INVESTIGATE)
    host = Host([michelle_probe()])
    inv = pipe.investigate(SYNTH_COMPLAINT, pipe.Runner(run=host))
    plan = pipe.plan_change(inv)
    payload = pipe.proposal_payload(FLOW, plan, inv)
    seen = {}
    published = w.publish_proposal(payload, settings={}, publish=lambda p: seen.update(body=p) or {"id": 501, "created": True})
    assert published["ok"]
    return type("Filed", (), {"plan": plan, "payload": payload, "sent_to_tensology": seen["body"], "inv": inv})


def proposal_row(filed, **over):
    row = {"id": 501, "status": "sent_to_decisions", "stored_status": "sent_to_decisions",
           "rationale": filed.payload["rationale"], "tasks": filed.payload["tasks"]}
    return {**row, **over}


def run(filed, host, fetch=None, **kw):
    told, said = [], []
    out = pipe.apply_approved(501, runner=pipe.Runner(run=host), fetch=fetch or (lambda i: proposal_row(filed)),
                              say=lambda k, t: said.append((k, t)), notify=told.append, **kw)
    return out, told, said


# --------------------------------------------------------------------------- filing
def test_the_private_record_is_saved_but_never_sent_to_tensology(filed):
    assert "_record" not in filed.sent_to_tensology
    rec = pipe.load_plan_record(501)
    assert rec["state"] == "published" and rec["plan_hash"] == pipe.plan_hash(filed.plan)
    assert rec["plan_hash"] in filed.payload["rationale"]


def test_a_replayed_filing_does_not_overwrite_the_record(filed):
    first = pipe.load_plan_record(501)
    again = pipe.save_plan_record(501, {**filed.payload["_record"], "tasks": ["changed"]})
    assert again["tasks"] == first["tasks"]


# --------------------------------------------------------------------------- the approval gate
def test_approval_needs_an_accepted_proposal(filed):
    rec = pipe.load_plan_record(501)
    assert pipe.check_approval(501, rec, lambda i: proposal_row(filed)).proposal_id == 501
    for status in ("pending", "dismissed", "failed", ""):
        with pytest.raises(pipe.ApprovalError):
            pipe.check_approval(501, rec, lambda i, s=status: proposal_row(filed, status=s, stored_status=s))


def test_approval_needs_this_plan_and_the_tasks_paul_saw(filed):
    rec = pipe.load_plan_record(501)
    with pytest.raises(pipe.ApprovalError):
        pipe.check_approval(501, rec, lambda i: proposal_row(filed, rationale="Plan fingerprint 0000000000000000."))
    with pytest.raises(pipe.ApprovalError):
        pipe.check_approval(501, rec, lambda i: proposal_row(filed, tasks=filed.payload["tasks"] + ["also delete everything"]))


def test_an_approval_cannot_be_forged_and_live_scripts_need_one():
    with pytest.raises(pipe.ApprovalError):
        pipe.Approval(1, "x", "approved")
    runner = pipe.Runner(run=Host([]))
    with pytest.raises(pipe.ApprovalError):
        runner.approved_script("apply_live", {}, object())
    with pytest.raises(pipe.PipelineError):  # the old write path still refuses anything that is not a scratch copy
        runner.script("fix", {}, write=True)


def test_only_the_two_live_scripts_can_run_approved(filed):
    approval = pipe.check_approval(501, pipe.load_plan_record(501), lambda i: proposal_row(filed))
    with pytest.raises(pipe.ApprovalError):
        pipe.Runner(run=Host([])).approved_script("fix", {}, approval)


# --------------------------------------------------------------------------- client-rate rule and drift
def test_r850_is_never_a_target_unless_it_is_the_clients_agreed_rate():
    probe = michelle_probe()
    plan = {"line_updates": [{"invoice": "I0738", "name": "X", "to": "850.00", "from": "550.00", "line_id": 1, "amount_from": "1", "amount_to": "1"}],
            "project_updates": [{"project": "P", "to": "850.00", "from": "550.00", "project_id": 1}]}
    assert len(pipe.guard_rates(plan, probe)) == 2
    probe["invoices"][0]["customer_rate"] = "850.00"
    assert pipe.guard_rates(plan, probe) == []
    plan["line_updates"][0]["to"] = "0"
    assert pipe.guard_rates(plan, probe)


def test_current_values_must_match_the_plan(filed):
    plan = pipe.load_plan_record(501)["plan"]
    assert pipe.check_current_values(plan, michelle_probe()) == []
    assert pipe.check_current_values(plan, michelle_probe(fixed=True))


# --------------------------------------------------------------------------- the run
def test_happy_path_backs_up_writes_verifies_and_drafts_from_the_live_result(filed):
    host = Host([michelle_probe(), michelle_probe(fixed=True)], apply_result=live_changes())
    out, told, said = run(filed, host)
    assert out["status"] == "applied"
    assert host.tags() == ["probe", "backup", "apply", "probe"]  # the database dump comes before the write
    assert out["database_backup"]["bytes"] == 50000
    assert "I0738" in out["reply"]["text"] and "R550.00" in out["reply"]["text"].replace(" ", "")  or "550.00" in out["reply"]["text"]
    assert "907.00" in out["reply"]["text"]
    assert "Waiting on you" in out["card"] and "nothing has gone to the client" in out["card"]
    assert told and told[-1] == out["card"]
    assert [k for k, _ in said][:1] == ["picked_up"] and said[-1][0] == "done"
    assert pipe.load_plan_record(501)["state"] == "applied"


def test_each_accept_applies_once(filed):
    host = Host([michelle_probe(), michelle_probe(fixed=True)], apply_result=live_changes())
    run(filed, host)
    again, _, _ = run(filed, Host([]))
    assert again["status"] == "already_applied"
    assert pipe.on_proposal_accepted(501, background=False) is False


def test_nothing_is_written_when_the_proposal_was_not_accepted(filed):
    host = Host([michelle_probe()], apply_result=live_changes())
    out, told, _ = run(filed, host, fetch=lambda i: proposal_row(filed, status="pending", stored_status="pending"))
    assert out["status"] == "not_approved"
    assert "apply" not in host.tags() and "backup" not in host.tags()
    assert "did not change anything" in told[-1]


def test_nothing_is_written_when_the_records_changed_since_the_proposal(filed):
    host = Host([michelle_probe(fixed=True)], apply_result=live_changes())
    out, _, _ = run(filed, host)
    assert out["status"] == "drifted" and "apply" not in host.tags()
    assert "backup" not in host.tags()


def test_the_rate_rule_blocks_before_any_backup_or_write(filed, monkeypatch):
    monkeypatch.setattr(pipe, "guard_rates", lambda plan, probe: ["R850 is not this client's agreed rate"])
    host = Host([michelle_probe()], apply_result=live_changes())
    out, _, _ = run(filed, host)
    assert out["status"] == "blocked" and "apply" not in host.tags()


def test_a_write_that_rolls_back_leaves_no_draft(filed):
    refused = {"ok": False, "stage": "precheck", "written": False, "problems": ["line 3 rate is now 700.00"]}
    host = Host([michelle_probe()], apply_result=refused)
    out, told, _ = run(filed, host)
    assert out["status"] == "aborted" and "reply" not in out
    assert "700.00" in out["why"] and pipe.load_plan_record(501)["state"] == "aborted"
    assert host.tags() == ["probe", "backup", "apply"]


def test_a_failed_check_after_the_commit_restores_the_saved_rows(filed):
    still_wrong = michelle_probe()  # the committed state does not show the fix
    host = Host([michelle_probe(), still_wrong], apply_result=live_changes())
    out, told, _ = run(filed, host)
    assert out["status"] == "reverted" and "restored the saved rows" in out["why"]
    assert host.tags() == ["probe", "backup", "apply", "probe", "revert"]
    assert "reply" not in out


def test_a_failed_restore_says_where_the_backup_is(filed):
    host = Host([michelle_probe(), michelle_probe()], apply_result=live_changes(), revert_result={"ok": False, "problems": ["boom"]})
    out, _, _ = run(filed, host)
    assert out["status"] == "failed" and "client-issue-501" in out["why"]


def test_a_backup_that_looks_empty_stops_the_write(filed):
    class Empty(Host):
        def __call__(self, argv, input="", **kw):
            if "pg_dump" in " ".join(argv):
                return self._ok("SIZE=10\nITEMS=0\n")
            return super().__call__(argv, input, **kw)
    host = Empty([michelle_probe()], apply_result=live_changes())
    out, _, _ = run(filed, host)
    assert out["status"] == "failed" and "apply" not in host.tags()


def test_other_proposals_are_ignored(env):  # noqa: F811
    assert pipe.on_proposal_accepted(999, background=False) is False
    assert pipe.on_proposal_accepted("nope") is False
    assert pipe.apply_approved(999)["status"] == "not_a_client_issue"


def test_accepting_a_proposal_calls_the_pipeline_only_when_work_starts(monkeypatch):
    from distr.core.incoming import proposals

    calls = []
    monkeypatch.setattr(pipe, "on_proposal_accepted", lambda pid: calls.append(pid))
    proposals._client_issue_accepted(7, False)
    proposals._client_issue_accepted(None, True)
    proposals._client_issue_accepted(7, True)
    assert calls == [7]
