"""Client-mail watch: triage rules, dry-run replay of the 28 Nov invoice complaints, card flow.

Fake Mailshot, fake model, fake Telegram. Nothing is sent to Telegram, WhatsApp, mail or a client.
"""

import json
from datetime import datetime, timezone

import pytest

import distr.core.initiative.client_mail_watch as w
import distr.core.initiative.outcome_flow as of

NOW = datetime(2026, 11, 28, 10, 0, tzinfo=timezone.utc)
SETTINGS = {"initiative_allow_telegram": True, "initiative_level": "operate"}

SENT = [
    {"from": "dev@own.example", "to": "Una <una@client-a.example>", "subject": "Invoices", "date": "2026-11-25T09:00:00.000Z"},
    {"from": "dev@own.example", "to": "Mia <mia@client-b.example>", "subject": "Invoice", "date": "2026-11-20T09:00:00.000Z"},
    {"from": "dev@own.example", "to": "x@gmail.com", "subject": "x", "date": "2026-11-01T09:00:00.000Z"},
]
URSULA = {"_id": "m-una", "from": "Una Example <una@client-a.example>", "subject": "Correction on Invoice I0740",
          "date": "2026-11-26T12:59:52.000Z", "attachments": [{"filename": "I0740.pdf"}], "preview": ""}
MICHELLE = {"_id": "m-mia", "from": "Mia <mia@client-b.example>", "subject": "Intricode Invoice I0738",
            "date": "2026-11-27T07:20:00.000Z", "attachments": [{"filename": "I0738.pdf"}], "preview": ""}
NEWSLETTER = {"_id": "m-news", "from": "Penguin <hello@penguinmagic.com>", "subject": "Our best Black Friday EVER, 40% off",
              "date": "2026-11-27T13:43:00.000Z", "preview": ""}
TRADER = {"_id": "m-trader", "from": "bot@own.example", "subject": "[TRADER] improvement deployed: error fixed", "date": "2026-11-27T13:00:00.000Z"}
ALERT = {"_id": "m-alert", "from": "alerts@spoortrack.com", "subject": "[Django] ERROR (EXTERNAL IP): Internal Server Error: /admin/devices",
         "date": "2026-11-27T10:00:00.000Z"}
BODIES = {
    "m-una": "Hi Paul, thanks for the invoices. On invoice I0740 the filling cost on the first item is R850, it should be R605. Please correct and resend.",
    "m-mia": "Hi Paul, on invoice I0738 page 3, last line Spoortrack (Old), the unit cost is R 850.00 and should be R 550.00. Please correct and resubmit.",
    "m-alert": "Internal Server Error: /admin/devices/device/ ...",
}


class FakeMailshot:
    def __init__(self, folders):
        self.folders, self.reads = folders, []

    def get(self, path, query=None):
        if path == "mail/messages":
            rows = self.folders.get((query or {}).get("folder", "inbox"), [])
            return {"emails": rows, "total": len(rows), "total_pages": 1}
        mid = path.rsplit("/", 1)[-1]
        self.reads.append(mid)
        return {"email": {"body": BODIES.get(mid, "")}}


def model(actionable=True, **over):
    def complete(provider, name, messages, settings):
        data = {"actionable": actionable, "kind": "billing_error", "urgency": "high", "client": "Una",
                "summary": "Invoice I0740 shows R850 on the first item; it should be R605.",
                "fix_plan": ["Find where R850 came from", "Use the agreed rate on file", "Draft the corrected invoice", "Re-check every line"],
                "draft_reply": "Una, sorry about that. The corrected invoice follows.", "needs_info": ""}
        data.update(over)
        return json.dumps(data)
    return complete


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    monkeypatch.setattr(w, "_last_run", 0.0)
    monkeypatch.setattr("distr.core.incoming.telegram_reply_intent.strong_model_candidates", lambda s: [("p", "m")])
    sent = []
    monkeypatch.setattr(of, "audit", lambda *a, **k: None)
    # No test may reach the Tensology host: an investigation that cannot run falls back to the plain card.
    import distr.core.initiative.client_issue_pipeline as pipe

    def no_host(*a, **k):
        raise pipe.PipelineError("no host in tests")
    monkeypatch.setattr(pipe, "investigate", no_host)

    def send(text, reply_markup=None, settings=None, interaction_token=None):
        sent.append({"text": text, "markup": reply_markup, "token": interaction_token})
        return True
    return type("Env", (), {"sent": sent, "send": staticmethod(send), "tmp": tmp_path})


def client(*rows, folder="inbox", extra=None):
    return FakeMailshot({folder: list(rows), "sent": SENT, **(extra or {})})


# --------------------------------------------------------------------------- triage rules

def test_classify_the_28_nov_complaints_and_the_noise():
    known = w.known_correspondents(SENT)
    assert w.classify(URSULA, known, folder="inbox")["important"]
    m = w.classify(MICHELLE, known, folder="inbox")  # nothing in the subject or preview: the sender decides
    assert m["category"] == "client" and m["important"]
    assert w.classify(NEWSLETTER, known)["category"] == "bulk"
    assert w.classify(TRADER, known)["category"] == "self"
    assert w.classify(ALERT, known, folder="bin")["category"] == "client_alert"


def test_client_mail_is_never_bulk_because_of_folder_or_address():
    known = w.known_correspondents(SENT)
    spammed = {**URSULA, "from": "info@client-a.example", "subject": "Wrong amount on invoice"}
    assert w.classify(spammed, known, folder="spam")["important"]
    assert w.classify({**URSULA, "from": "stranger@elsewhere.com", "subject": "hello"}, known, folder="spam")["category"] == "bulk"


def test_unknown_human_needs_a_problem_in_the_subject_and_is_not_binned():
    known = w.known_correspondents(SENT)
    row = {"_id": "x", "from": "Dana <dana@newco.com>", "subject": "Your site is down", "date": "2026-11-27T10:00:00Z"}
    assert w.classify(row, known, folder="inbox")["category"] == "unknown_human"
    assert not w.classify(row, known, folder="bin")["important"]
    assert not w.classify({**row, "subject": "Catching up"}, known, folder="inbox")["important"]


def test_own_addresses_are_not_clients():
    sent = SENT + [{"from": "agent@freemail.example", "to": "agent@freemail.example", "subject": "n", "date": "2026-11-01T00:00:00Z"}]
    known = w.known_correspondents(sent)
    assert w.classify({"_id": "n", "from": "agent@freemail.example", "subject": "report"}, known)["category"] == "self"


# --------------------------------------------------------------------------- the 28 Nov replay (dry run)

def test_dry_run_catches_both_invoice_complaints_and_sends_nothing(env):
    mail = client(URSULA, MICHELLE, NEWSLETTER, TRADER)
    out = w.run_once(SETTINGS, dry_run=True, ignore_state=True, client=mail, complete=model(), send=env.send, now=NOW)
    assert [c["id"] for c in out["cards"]] == ["m-una", "m-mia"]
    assert env.sent == [] and not any(c["delivered"] for c in out["cards"])
    assert not (env.tmp / "client_mail_watch.json").exists() and not (env.tmp / "initiative_outcome_flows.json").exists()
    assert set(mail.reads) == {"m-una", "m-mia"}  # newsletter and trader mail are never opened
    card = out["cards"][0]["text"]
    assert "R605" in card and "My plan:" in card and "Waiting on you:" in card and "Nothing goes to the client until you tap send" in card
    assert "Draft reply" not in card  # the reply is drafted after the fix, from what changed
    assert [b["text"] for b in out["cards"][0]["markup"]["inline_keyboard"][0]] == ["Start the fix", "Not now"]


def test_client_buried_in_archive_or_bin_still_surfaces(env):
    mail = client(folder="archived", extra={"bin": [ALERT]}, *[{**URSULA}])
    out = w.run_once(SETTINGS, dry_run=True, ignore_state=True, client=mail, complete=model(kind="bug"), now=NOW)
    folders = {c["id"]: c["folder"] for c in out["cards"]}
    assert folders == {"m-una": "archived", "m-alert": "bin"}
    assert "not your inbox" in out["cards"][0]["text"]


def test_not_actionable_mail_raises_no_card(env):
    out = w.run_once(SETTINGS, dry_run=True, ignore_state=True, client=client(URSULA), complete=model(actionable=False), now=NOW)
    assert out["cards"] == [] and out["skipped"][0]["why"] == "not actionable"


def test_no_model_falls_back_to_rules_without_inventing_a_reply(env):
    def broken(*a, **k):
        raise RuntimeError("down")
    out = w.run_once(SETTINGS, dry_run=True, ignore_state=True, client=client(URSULA), complete=broken, now=NOW)
    assert out["cards"][0]["assessment"]["model"] == "rules"
    assert out["cards"][0]["assessment"]["draft_reply"] == ""


# --------------------------------------------------------------------------- live run: baseline, dedupe, answered

def test_first_live_run_only_sets_the_baseline(env):
    out = w.run_once(SETTINGS, client=client(URSULA, MICHELLE), complete=model(), send=env.send, now=NOW)
    assert out["cards"] == [] and env.sent == []
    again = w.run_once(SETTINGS, client=client(URSULA, MICHELLE), complete=model(), send=env.send, now=NOW)
    assert again["cards"] == []


def test_new_client_mail_after_the_baseline_raises_one_card_once(env):
    w.run_once(SETTINGS, client=client(), complete=model(), send=env.send, now=datetime(2026, 11, 26, 0, 0, tzinfo=timezone.utc))
    mail = client(URSULA)
    first = w.run_once(SETTINGS, client=mail, complete=model(), send=env.send, now=NOW)
    second = w.run_once(SETTINGS, client=mail, complete=model(), send=env.send, now=NOW)
    assert len(first["cards"]) == 1 and first["cards"][0]["delivered"] and second["cards"] == []
    assert len(env.sent) == 1 and env.sent[0]["token"].startswith(of.OC_CARD_PREFIX)
    flow = of.get_flow(first["cards"][0]["key"])
    assert flow["kind"] == "client_issue" and flow["status"] == "awaiting_decision"


def test_mail_paul_already_answered_raises_no_card(env):
    answered = SENT + [{"from": "dev@own.example", "to": "una@client-a.example", "subject": "Re: Correction", "date": "2026-11-26T15:00:00.000Z"}]
    w.run_once(SETTINGS, client=client(), complete=model(), send=env.send, now=datetime(2026, 11, 26, 0, 0, tzinfo=timezone.utc))
    mail = FakeMailshot({"inbox": [URSULA], "sent": answered})
    out = w.run_once(SETTINGS, client=mail, complete=model(), send=env.send, now=NOW)
    assert out["cards"] == [] and env.sent == []


def test_failed_telegram_delivery_is_retried_not_lost(env):
    w.run_once(SETTINGS, client=client(), complete=model(), send=env.send, now=datetime(2026, 11, 26, 0, 0, tzinfo=timezone.utc))
    mail = client(URSULA)
    out = w.run_once(SETTINGS, client=mail, complete=model(), send=lambda *a, **k: False, now=NOW)
    assert out["cards"][0]["delivered"] is False
    # not marked seen, so the next run raises it again
    assert "m-una" not in _seen()


def _seen():
    return (w._load_state().get("seen") or {})


def test_maybe_run_respects_the_switch_and_throttle(env, monkeypatch):
    calls = []
    monkeypatch.setattr(w, "run_once", lambda s: calls.append(1) or {"cards": []})
    w.maybe_run({**SETTINGS, w.SETTING_KEY: False})
    assert calls == []
    w.maybe_run(SETTINGS)
    w.maybe_run(SETTINGS)
    assert calls == [1]
    w.maybe_run({"initiative_allow_telegram": False, "initiative_level": "operate"})
    assert calls == [1]


# --------------------------------------------------------------------------- taps

def _raise_card(env):
    w.run_once(SETTINGS, client=client(), complete=model(), send=env.send, now=datetime(2026, 11, 26, 0, 0, tzinfo=timezone.utc))
    out = w.run_once(SETTINGS, client=client(URSULA), complete=model(), send=env.send, now=NOW)
    return out["cards"][0]["key"]


def test_not_now_dismisses_and_sends_nothing(env, monkeypatch):
    key = _raise_card(env)
    monkeypatch.setattr(of, "edit_telegram", lambda *a, **k: True)
    out = of.handle_telegram_text(f"oc:{key}:cancel", chat_id=1, telegram_message_id=5)
    assert "Nothing was sent" in out["text"] and of.get_flow(key)["status"] == "dismissed"


def test_start_the_fix_opens_one_ticket_and_contacts_no_client(env, monkeypatch):
    key = _raise_card(env)
    started = []
    monkeypatch.setattr(w, "start_fix", lambda flow: started.append(flow["message_id"]) or {"ticket_id": 77})
    monkeypatch.setattr(of, "edit_telegram", lambda *a, **k: True)
    out = of.handle_telegram_text(f"oc:{key}:start", chat_id=1, telegram_message_id=5)
    again = of.handle_telegram_text(f"oc:{key}:start", chat_id=1, telegram_message_id=5)
    assert started == ["m-una"] and of.get_flow(key)["status"] == "started"
    assert "Nothing has gone to the client" in out["text"] and "Already started" in again["text"]


def test_start_failure_keeps_the_card_live(env, monkeypatch):
    key = _raise_card(env)
    def boom(flow):
        raise RuntimeError("no db")
    monkeypatch.setattr(w, "start_fix", boom)
    monkeypatch.setattr(of, "edit_telegram", lambda *a, **k: True)
    out = of.handle_telegram_text(f"oc:{key}:start", chat_id=1, telegram_message_id=5)
    assert "tap Start the fix to try again" in out["text"] and of.get_flow(key)["status"] == "awaiting_decision"


def test_ticket_text_forbids_sending_and_carries_the_email():
    flow = {"client": "Una", "sender": "u@x.co", "subject": "Correction", "body": "R850 should be R605",
            "assessment": {"summary": "wrong line", "fix_plan": ["a", "b"]}}
    text = w.ticket_text(flow)
    assert "R850 should be R605" in text and "Send nothing to the client" in text and "draft only" in text.lower()
    assert "only then draft the reply from what actually changed" in text


def test_card_goes_through_the_real_telegram_sender_with_figures_and_buttons_intact(env, monkeypatch):
    class Manager:
        last_telegram_message_id, last_telegram_chat_id = 321, 9

        def __init__(self):
            self.sent = []

        def send_to_telegram(self, text, reply_markup=None, interaction_token=None, **_):
            self.sent.append((text, reply_markup, interaction_token))
            return True

    manager = Manager()
    monkeypatch.setattr(of, "_telegram_manager", lambda: manager)
    w.run_once(SETTINGS, client=client(), complete=model(), now=datetime(2026, 11, 26, 0, 0, tzinfo=timezone.utc))
    out = w.run_once(SETTINGS, client=client(URSULA), complete=model(), now=NOW)  # default sender = outcome_flow.send_telegram
    text, markup, token = manager.sent[0]
    assert out["cards"][0]["delivered"] and len(manager.sent) == 1
    assert "I0740" in text and "R850" in text and "R605" in text
    key = out["cards"][0]["key"]
    assert token == f"{of.OC_CARD_PREFIX}{key}"
    assert [b["callback_data"] for b in markup["inline_keyboard"][0]] == [f"oc:{key}:start", f"oc:{key}:cancel"]
