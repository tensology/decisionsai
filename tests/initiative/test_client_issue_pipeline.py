"""Client issue pipeline: investigate read only, curate the proposal, fix on a copy, verify, draft.

Everything runs against a fake Tensology host. No ssh, no database, no mail, no Telegram.
"""

import json
from decimal import Decimal

import pytest

import distr.core.initiative.client_issue_pipeline as pipe
import distr.core.initiative.client_mail_watch as w
import distr.core.initiative.outcome_flow as of
from tests.initiative.test_client_mail_watch import env, BODIES, MICHELLE, NOW, SENT, SETTINGS, URSULA, client, model  # noqa: F401

SYNTH_COMPLAINT = ("Acme Invoice I0738\nGood morning, on page 3 the last line for \"Trackly (Old)\" "
                   "shows a unit cost of R 850.00. Please correct this line item and resubmit.")
URSULA_TEXT = "Correction on Invoice I0740\nPlease correct the filling cost of the 1st item. It should be R605 and not R850."


def _line(i, name, unit, qty, amount, created_unit=None, reason="", created_at="2026-11-25T11:35:12+00:00"):
    return {"id": i, "name": name, "description": "work", "unit": unit, "qty": qty, "amount": amount, "created_at": created_at,
            "created_unit": created_unit or unit, "created_reason": reason, "unit_changes": []}


def michelle_probe(*, fixed=False):
    rate = "550.00" if fixed else "850.00"
    lines = [_line(1, "Trackly", "550.00", "1.00", "550.00"), _line(2, "Trackly (Old)", "550.00", "0.52", "286.00"),
             _line(3, "Trackly (Old)", rate, "0.31", "170.50" if fixed else "263.50", created_unit="850.00",
                   reason="Reconciled unsent billing")]
    sub = sum(Decimal(l["amount"]) for l in lines)
    return {"invoices": [{
        "number": "I0738", "found": True, "id": 11990, "customer": "Client B", "customer_id": 52, "customer_rate": None,
        "status": "unpaid", "is_sent": False, "issue_date": "2026-11-25", "subtotal": str(sub), "tax_rate": "0", "tax_amount": "0.00",
        "discount": "0.00", "total": str(sub),
        "projects": [
            {"id": 168, "name": "Trackly", "rate": "550.00", "rate_changes": [{"at": "2026-01-25T08:05:00+00:00", "value": "550.00", "reason": "", "type": "+"}]},
            {"id": 194, "name": "Trackly (Old)", "rate": "550.00" if fixed else "850.00",
             "rate_changes": [{"at": "2026-11-09T07:19:00+00:00", "value": "850.00", "reason": "", "type": "+"}]}],
        "lines": lines,
        "peer_rates": [{"name": "Trackly", "unit": "550.00", "count": 9}, {"name": "Trackly (Old)", "unit": "550.00", "count": 2}],
    }]}


class FakeHost:
    """Stands in for ssh. Records every call and answers from canned results."""
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, argv, input="", **kw):
        self.calls.append({"argv": argv, "stdin": input})
        text = input or ""
        for needle, result in self.answers.items():
            if needle in text or needle in " ".join(argv):
                out = result if isinstance(result, str) else "@@RESULT@@" + json.dumps(result)
                return type("P", (), {"returncode": 0, "stdout": out, "stderr": ""})()
        return type("P", (), {"returncode": 1, "stdout": "", "stderr": "no answer"})()


# --------------------------------------------------------------------------- reading the complaint

def test_the_complaints_are_read_for_invoice_and_figures():
    m = pipe.read_complaint(SYNTH_COMPLAINT)
    assert m["invoices"] == ["I0738"] and m["stated_wrong"] == Decimal("850.00") and m["stated_correct"] is None
    u = pipe.read_complaint(URSULA_TEXT)
    assert u["invoices"] == ["I0740"] and (u["stated_correct"], u["stated_wrong"]) == (Decimal("605.00"), Decimal("850.00"))


# --------------------------------------------------------------------------- tracing the cause

def test_a_line_at_the_default_is_traced_to_the_project_rate_and_the_agreed_rate_comes_from_peers():
    a = pipe.analyze(michelle_probe(), pipe.read_complaint(SYNTH_COMPLAINT))
    (f,) = a["findings"]
    assert (f["invoice"], f["billed"], f["agreed"], f["amount_should_be"]) == ("I0738", "850.00", "550.00", "170.50")
    assert "2 other 'Trackly (Old)' line" in f["agreed_basis"] and "R550.00" in f["agreed_basis"]
    assert f["cause"]["root"] == "project_rate" and "default" in f["cause"]["summary"] and "R850.00" in f["cause"]["summary"]
    assert [p["project_id"] for p in a["project_findings"]] == [194]  # the project would repeat it next run


def test_a_clean_invoice_yields_no_findings_and_no_plan():
    inv = {"stated": pipe.read_complaint(SYNTH_COMPLAINT), "probe": michelle_probe(fixed=True)}
    inv["analysis"] = pipe.analyze(inv["probe"], inv["stated"])
    assert inv["analysis"]["findings"] == [] and pipe.plan_change(inv)["line_updates"] == []


def test_the_clients_own_figure_is_used_and_labelled_when_nothing_corroborates_it():
    probe = michelle_probe()
    probe["invoices"][0]["peer_rates"] = []
    a = pipe.analyze(probe, {"invoices": ["I0738"], "stated_wrong": Decimal("850.00"), "stated_correct": Decimal("550.00")})
    assert a["findings"][0]["agreed"] == "550.00" and "no other line confirms it" in a["findings"][0]["agreed_basis"]


def test_with_no_evidence_for_a_rate_nothing_is_guessed():
    probe = michelle_probe()
    probe["invoices"][0]["peer_rates"] = []
    a = pipe.analyze(probe, pipe.read_complaint(SYNTH_COMPLAINT))
    assert a["findings"][0]["agreed"] is None
    inv = {"stated": {}, "probe": probe, "analysis": a, "invoices": ["I0738"]}
    assert pipe.plan_change(inv)["line_updates"] == [] and pipe.plan_change(inv)["unresolved"]


# --------------------------------------------------------------------------- the proposal

def _investigation(probe=None):
    probe = probe or michelle_probe()
    stated = pipe.read_complaint(SYNTH_COMPLAINT)
    inv = {"stated": stated, "probe": probe, "analysis": pipe.analyze(probe, stated), "invoices": ["I0738"]}
    inv["plan"] = pipe.plan_change(inv)
    return inv


def test_the_proposal_states_cause_exact_change_and_what_waits_on_paul():
    inv = _investigation()
    payload = pipe.proposal_payload({"thread_id": "t1", "message_id": "m1", "sender": "mia@client-b.example"}, inv["plan"], inv)
    assert payload["curated"] and payload["source"] == {"channel": "mailshot", "source_thread_id": "t1", "message_ids": ["m1"]}
    d = payload["description"]
    assert "What is wrong:" in d and "Root cause:" in d and "The fix:" in d and "Waiting on you:" in d
    assert "R850.00" in d and "R550.00" in d and "Production write" in d and "Send:" in d
    assert any("hourly rate to R550.00" in t for t in payload["tasks"])


# --------------------------------------------------------------------------- guards

def test_write_scripts_refuse_anything_but_a_scratch_copy():
    host = FakeHost({})
    with pytest.raises(pipe.PipelineError):
        pipe.Runner(run=host).script("fix", {"plan": {}}, write=True)
    with pytest.raises(pipe.PipelineError):
        pipe.Runner(db_name="tensology", run=host).script("fix", {"plan": {}}, write=True)
    with pytest.raises(pipe.PipelineError):
        pipe.Runner(db_name="tensology", run=host).drop_scratch()
    assert host.calls == []


def test_the_read_script_runs_in_a_read_only_transaction_and_names_a_scratch_database_for_copies():
    wrapper = (pipe.SCRIPT_DIR / "wrapper.py").read_text()
    assert "SET TRANSACTION READ ONLY" in wrapper and "set_rollback(True)" in wrapper and "scratch_" in wrapper
    host = FakeHost({"def main": {"invoices": []}})
    pipe.Runner(run=host).script("probe", {"invoices": ["I1"]})
    cmd = host.calls[0]["argv"][-1]
    assert "SCRATCH_DB" not in cmd and "manage.py shell" in cmd
    scratch = pipe.Runner(db_name="scratch_fix_1", run=host)
    scratch.script("probe", {"invoices": ["I1"]})
    assert "SCRATCH_DB=scratch_fix_1" in host.calls[1]["argv"][-1]


def test_the_scripts_never_carry_a_secret_or_a_send():
    for path in pipe.SCRIPT_DIR.glob("*.py"):
        text = path.read_text().lower()
        for banned in ("send_mail", "emailmessage", "smtp", "urlopen", "requests.", "print(settings", "print(d"):
            assert banned not in text, (path.name, banned)
    # The one script that starts programs only starts the database tools, and only on a scratch_ name.
    db = (pipe.SCRIPT_DIR / "scratchdb.py").read_text()
    assert all(tool in db for tool in ("createdb", "dropdb", "pg_dump", "psql")) and "REFUSED" in db


# --------------------------------------------------------------------------- execute, verify, draft

FIX_RESULT = {"projects": [{"id": 194, "name": "Trackly (Old)", "from": "850.00", "to": "550.00"}],
              "lines": [{"id": 3, "invoice": "I0738", "name": "Trackly (Old)", "unit_from": "850.00", "unit_to": "550.00",
                         "amount_from": "263.50", "amount_to": "170.50"}],
              "invoices": [{"number": "I0738", "before": {"subtotal": "1000.00", "tax": "0.00", "total": "1000.00"},
                            "after": {"subtotal": "906.50", "tax": "0.00", "total": "906.50"}, "is_sent": False,
                            "pdf": {"path": "/tmp/scratch_fix_1/I0738-corrected.pdf", "bytes": 100}}]}


def _after_probe(fixed=True):
    p = michelle_probe(fixed=fixed)
    inv = p["invoices"][0]
    inv["subtotal"] = inv["total"] = str(sum(Decimal(l["amount"]) for l in inv["lines"]))
    return p


def test_execute_applies_on_the_copy_re_reads_and_passes_only_when_everything_checks():
    inv = _investigation()
    host = FakeHost({"def main(P):\n    \"\"\"SCRATCH ONLY. Apply": FIX_RESULT, "def main(P):\n    from collections": _after_probe()})
    ex = pipe.execute(inv["plan"], inv, pipe.Runner(db_name="scratch_fix_1", run=host), reason="test")
    assert ex["verification"]["passed"], ex["verification"]
    names = [c["check"] for c in ex["verification"]["checks"]]
    assert "no line is still at the wrong rate" in names and "I0738 total equals lines plus tax" in names


def test_verification_fails_when_a_line_is_still_wrong_and_no_reply_is_drafted():
    inv = _investigation()
    host = FakeHost({"SCRATCH ONLY. Apply": FIX_RESULT, "def main(P):\n    from collections": michelle_probe(fixed=False)})
    ex = pipe.execute(inv["plan"], inv, pipe.Runner(db_name="scratch_fix_1", run=host), reason="test")
    assert not ex["verification"]["passed"]
    reply = pipe.draft_reply({"client": "Mia", "sender": "mia@x"}, inv["plan"], ex)
    assert reply["text"] == "" and "Verification failed" in reply["blocked"]


def test_the_reply_is_built_only_from_what_changed_and_attaches_the_corrected_invoice():
    inv = _investigation()
    ex = {"changes": FIX_RESULT, "verification": {"passed": True, "checks": [{"check": "x", "ok": True, "detail": []}]}}
    reply = pipe.draft_reply({"client": "Mia (Client B)", "sender": "mia@client-b.example"}, inv["plan"], ex)
    assert reply["text"].startswith("Hi Mia,") and "R550.00 an hour (it was R850.00)" in reply["text"]
    assert "R906.50 instead of R1 000.00" in reply["text"] and reply["attachments"] == ["I0738-corrected.pdf"]
    card = pipe.completion_card({"client": "Mia"}, inv["plan"], ex, reply, dry_run=True)
    assert "nothing real was changed" in card and "Send: nothing has gone to the client" in card and "Production write: not made" in card


# --------------------------------------------------------------------------- the watch feeds the proposal flow

def _flow_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    monkeypatch.setattr(w, "_last_run", 0.0)
    monkeypatch.setattr("distr.core.incoming.telegram_reply_intent.strong_model_candidates", lambda s: [("p", "m")])
    monkeypatch.setattr(of, "audit", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(w, "_flow_key", lambda mid: "k-" + mid)

    def send(text, reply_markup=None, settings=None, interaction_token=None):
        sent.append(text)
        return True
    return sent, send


def _fake_investigate(text):
    inv = _investigation()
    inv["stated"] = pipe.read_complaint(text)
    return inv


def test_an_investigated_complaint_becomes_a_proposal_and_no_separate_card(monkeypatch, tmp_path):
    sent, send = _flow_env(monkeypatch, tmp_path)
    published = []
    out = w.run_once(SETTINGS, ignore_state=True, client=client(MICHELLE), complete=model(client="Mia"), send=send, now=NOW,
                     investigate_fn=_fake_investigate, publish_fn=lambda p: published.append(p) or {"id": 77, "created": True})
    assert len(published) == 1 and published[0]["curated"] and "Root cause:" in published[0]["description"]
    assert sent == [] and out["cards"][0]["via"] == "proposal" and out["cards"][0]["delivered"]
    again = w.run_once(SETTINGS, client=client(MICHELLE), complete=model(), send=send, now=NOW, investigate_fn=_fake_investigate,
                       publish_fn=lambda p: published.append(p) or {"id": 77})
    assert len(published) == 1 and again["cards"] == []  # raised once


def test_dry_run_shows_the_proposal_but_publishes_nothing(monkeypatch, tmp_path):
    sent, send = _flow_env(monkeypatch, tmp_path)
    def boom(p):
        raise AssertionError("dry run must not publish")
    out = w.run_once(SETTINGS, dry_run=True, ignore_state=True, client=client(MICHELLE), complete=model(), send=send, now=NOW,
                     investigate_fn=_fake_investigate, publish_fn=boom)
    card = out["cards"][0]
    assert card["proposal"]["curated"] and "Root cause:" in card["text"] and "Waiting on you:" in card["text"]
    assert "Draft reply" not in card["text"] and sent == []


def test_if_the_proposal_cannot_be_published_the_telegram_card_carries_the_same_plan(monkeypatch, tmp_path):
    sent, send = _flow_env(monkeypatch, tmp_path)
    def down(p):
        raise RuntimeError("Tensology unreachable")
    out = w.run_once(SETTINGS, ignore_state=True, client=client(MICHELLE), complete=model(), send=send, now=NOW,
                     investigate_fn=_fake_investigate, publish_fn=down)
    assert len(sent) == 1 and "Root cause:" in sent[0] and "Start the fix" in sent[0]
    assert out["cards"][0]["delivered"] and out["cards"][0]["published"]["ok"] is False


def test_a_failed_investigation_falls_back_to_the_plain_card(monkeypatch, tmp_path):
    sent, send = _flow_env(monkeypatch, tmp_path)
    def broken(text):
        raise pipe.PipelineError("ssh down")
    out = w.run_once(SETTINGS, ignore_state=True, client=client(MICHELLE), complete=model(), send=send, now=NOW, investigate_fn=broken)
    assert len(sent) == 1 and "My plan:" in sent[0] and out["cards"][0]["proposal"] is None


# --------------------------------------------------------------------------- failed vendor payments

DO_FAIL = {"_id": "m-do", "from": "DigitalOcean Billing <no-reply@digitalocean.com>", "subject": "[DigitalOcean] Your payment failed",
           "date": "2026-11-27T09:00:00.000Z", "preview": ""}
DO_INVOICE = {**DO_FAIL, "_id": "m-do2", "subject": "[DigitalOcean] Your 2026-10 invoice is available"}
DO_QUIET_SUBJECT = {**DO_FAIL, "_id": "m-do3", "subject": "Billing update for your account"}
LOOKALIKE = {**DO_FAIL, "_id": "m-fake", "from": "billing <no-reply@digitalocean.com.pay-secure.example>"}


def test_vendor_payment_failures_are_recognised_before_the_bulk_rules():
    known = w.known_correspondents(SENT)
    t = w.classify(DO_FAIL, known, folder="spam")
    assert t["category"] == "vendor_payment" and t["important"] and t["vendor"] == "digitalocean.com"
    assert w.classify(DO_INVOICE, known)["important"] is False
    maybe = w.classify(DO_QUIET_SUBJECT, known)
    assert maybe["category"] == "vendor_payment" and maybe["confirm"]  # the body decides
    assert w.classify(LOOKALIKE, known)["category"] != "vendor_payment"  # exact domain or its subdomain only


def test_a_vendor_failure_raises_a_card_with_a_plan_and_no_reply_and_sends_nothing(env):
    mail = client(DO_FAIL, DO_QUIET_SUBJECT, extra={})
    mail_bodies = {"m-do": "We were unable to charge your card for invoice 123.", "m-do3": "Your balance is fine. Thanks for being a customer."}
    mail.get = (lambda orig: lambda path, query=None: {"email": {"body": mail_bodies[path.rsplit("/", 1)[-1]]}} if path.startswith("mail/messages/") else orig(path, query))(mail.get)
    out = w.run_once(SETTINGS, dry_run=True, ignore_state=True, client=mail, complete=None, send=env.send, now=NOW)
    assert [c["id"] for c in out["cards"]] == ["m-do"]  # the routine one is dropped after its body is read
    card = out["cards"][0]
    assert card["assessment"]["kind"] == "vendor_payment" and card["assessment"]["draft_reply"] == ""
    assert "typing its address" in card["text"] and "Nothing is paid or sent from here" in card["text"]
    assert env.sent == []
