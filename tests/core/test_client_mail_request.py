"""A mail-to-proposal request from chat goes straight to the client-mail path (no tool-loop wandering),
and the chat's own tool call no longer fails argument validation.

Fake Gmail, fake Mailshot, fake model, fake Incoming, fake Telegram, scratch state dir. Nothing is sent
to a client, mail, Telegram or Tensology. Run with ``-s`` to print the harness sequence.
"""
import json
import time
from datetime import datetime, timezone

import pytest

import distr.core.initiative.client_mail_request as cmr
import distr.core.initiative.client_mail_watch as w
import distr.core.initiative.outcome_flow as of
from distr.core.initiative import job_progress as jp
from tests.core.test_job_progress_flow import Relay, _wait, world  # noqa: F401  (world is a fixture)

NOW = datetime(2026, 11, 28, 9, 0, tzinfo=timezone.utc)
SETTINGS = {"initiative_allow_telegram": True, "initiative_level": "operate"}
GMAIL_ID = "1a221ad0032d80df"
BODY = ("Hi Paul, thanks for all of this. Your checkout asked for the banking details: EFT goes to Examplebank, "
        "Acme Print and Co, account 000111222, branch 123456, proof of payment to accounts@acme-print.example. "
        "Please change the checkout so customers see these details. Go ahead.")


class FakeGmail:
    def __init__(self, mails):
        self.mails, self.calls = mails, []

    def get_email(self, message_id):
        self.calls.append(("get_email", message_id))
        return next((m for m in self.mails if m["id"] == message_id), None)

    def check_inbox(self, max_results=10, query="in:inbox"):
        self.calls.append(("check_inbox", query))
        return list(self.mails)


GMAIL_MAIL = {"id": GMAIL_ID, "threadId": "t-9", "from": "Dana <dana@acme-print.example>",
              "subject": "RE: FW: Acme Print Statement SO283 - ordering experience", "date": "Sat, 28 Nov 2026 08:12:00 +0200",
              "body": BODY, "snippet": "Hi Paul thanks for all of this", "attachments": []}


class FakeMailshot:
    def __init__(self, rows=(), bodies=None):
        self.rows, self.bodies, self.reads = list(rows), bodies or {}, []

    def get(self, path, query=None):
        if path == "mail/messages":
            rows = self.rows if (query or {}).get("folder", "inbox") == "inbox" else []
            return {"emails": rows, "total": len(rows), "total_pages": 1}
        self.reads.append(path.rsplit("/", 1)[-1])
        return {"email": {"body": self.bodies.get(path.rsplit("/", 1)[-1], "")}}


def model(**over):
    def complete(provider, name, messages, settings):
        data = {"actionable": False, "kind": "other", "urgency": "high", "client": "Acme Print",
                "summary": "Acme Print approved the checkout change and wants the EFT banking details shown at checkout.",
                "fix_plan": ["Add the EFT banking details to the checkout payment step",
                             "Re-check the order confirmation shows them", "Draft a reply from what changed"],
                "draft_reply": "", "needs_info": ""}
        data.update(over)
        return json.dumps(data)
    return complete


@pytest.fixture
def env(request, tmp_path, monkeypatch):
    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    monkeypatch.setattr("distr.core.incoming.telegram_reply_intent.strong_model_candidates", lambda s: [("p", "m")])
    monkeypatch.setattr(of, "audit", lambda *a, **k: None)
    steps, published, cards = [], [], []
    real_report = jp.report
    live = "world" in request.fixturenames       # the harness test passes steps on to the real narration

    def record(job, kind, text="", **kw):
        steps.append((kind, text, kw.get("parent")))
        return real_report(job, kind, text, **kw) if live else True

    monkeypatch.setattr(jp, "report", record)

    def publish(proposal):
        published.append(proposal)
        return {"id": 77, "created": True}

    def send(text, reply_markup=None, settings=None, interaction_token=None):
        cards.append({"text": text, "token": interaction_token})
        return True

    return type("Env", (), {"steps": steps, "published": published, "cards": cards, "publish": staticmethod(publish),
                            "send": staticmethod(send)})


def run(env, **kw):
    kw = {"settings": SETTINGS, "complete": model(), "publish_fn": env.publish, "send": env.send,
          "existing_fn": lambda: [], "now": NOW, **kw}
    return cmr.handle_request(**kw)


# --------------------------------------------------------------------------- 1. the invalid arguments

def test_request_tool_accepts_the_query_in_any_shape_the_model_sends():
    from distr.core.agent.services.llm.base_service import BaseLLMService
    from distr.core.agent.tools.request_tool import RequestToolTool

    tool = RequestToolTool(on_tool_requested=lambda q: (True, "got:" + q))
    shapes = [{"text": "client_mail_proposal"},
              {"transcription": "check if that's a proposal"},                        # bare string: the 3 failures
              {"transcription": '["check if that\'s a proposal", "ticket"]'},        # JSON-encoded list
              {"text": "", "transcription": ["a", "b"]}]
    for args in shapes:
        out = tool._run(**BaseLLMService._normalize_tool_kwargs(tool, args))
        assert out.startswith("got:") and len(out) > 4, args
    assert "transcription" not in tool.args_schema.model_json_schema()["properties"]


def test_every_action_tool_tolerates_a_string_transcription():
    from distr.core.agent.tools.base import BaseActionInput

    assert BaseActionInput(transcription="hello").transcription == ["hello"]
    assert BaseActionInput(transcription='["a", "b"]').transcription == ["a", "b"]
    assert BaseActionInput(transcription=["a"]).transcription == ["a"]
    assert BaseActionInput().transcription is None


# --------------------------------------------------------------------------- 2. routing to the tool

@pytest.mark.parametrize("text", [
    "Can you check that that's a proposal just yet, and if it's not, then create a proposal, push that as a ticket, and then implement it, please.",
    "You can go ahead and check if that's a proposal. Otherwise, if it's not a proposal, go and create it as a proposal and then push it through as a thread.",
    "turn that email into a thread",
])
def test_these_requests_put_the_tool_in_front_of_the_model(text):
    from distr.core.agent.tool_intents import forced_tool_names_for_text

    assert "client_mail_proposal" in forced_tool_names_for_text(text)


def test_ordinary_requests_do_not(monkeypatch):
    from distr.core.agent.tool_intents import forced_tool_names_for_text

    assert "client_mail_proposal" not in forced_tool_names_for_text("what's the weather like")


def test_tool_is_registered():
    from distr.core.agent.tools.loader import TOOL_DESCRIPTIONS, TOOL_REGISTRY

    assert "ClientMailProposalTool" in TOOL_REGISTRY and "ClientMailProposalTool" in TOOL_DESCRIPTIONS


# --------------------------------------------------------------------------- 3. the path

def test_gmail_mail_becomes_one_proposal_with_each_step_reported(env):
    out = run(env, message_id=GMAIL_ID, gmail=FakeGmail([GMAIL_MAIL]), mailshot=FakeMailshot())
    assert out["status"] == "proposed" and out["proposal"]["id"] == 77
    kinds = [k for k, _t, _p in env.steps]
    assert kinds == ["picked_up", "step", "step", "step", "step", "step", "step", "step", "proposal", "waiting"]
    texts = " | ".join(t for _k, t, _p in env.steps)
    for part in ("looking for the mail", "found:", "already a proposal", "assessing", "investigating the records",
                 "no records check applies", "filing the proposal", "Accept/Decline card sent to Telegram", "your approval"):
        assert part in texts, part
    (proposal,) = env.published
    assert proposal["source"] == {"channel": "gmail", "source_thread_id": "t-9", "message_ids": [GMAIL_ID]}
    assert proposal["curated"] and proposal["kind"] == "client_issue" and len(proposal["tasks"]) == 3
    assert "Not investigated yet" in proposal["description"]          # no invented root cause
    assert proposal["reply_to"] == "dana@acme-print.example"
    assert env.cards == []                                            # Incoming carries the Telegram card
    assert w._load_state()["seen"][GMAIL_ID]["status"] == "proposed"


def test_asking_again_finds_the_existing_proposal_and_files_nothing(env):
    run(env, message_id=GMAIL_ID, gmail=FakeGmail([GMAIL_MAIL]), mailshot=FakeMailshot())
    env.steps.clear()
    again = run(env, message_id=GMAIL_ID, gmail=FakeGmail([GMAIL_MAIL]), mailshot=FakeMailshot())
    assert again["status"] == "already_proposed" and len(env.published) == 1
    assert env.steps[-1][0] == "done" and "already a proposal" in env.steps[-1][1]


def test_pending_proposal_in_incoming_is_found_too(env):
    pending = [{"id": 5, "source": {"message_ids": [GMAIL_ID]}}]
    out = run(env, message_id=GMAIL_ID, gmail=FakeGmail([GMAIL_MAIL]), mailshot=FakeMailshot(), existing_fn=lambda: pending)
    assert out["status"] == "already_proposed" and out["proposal"]["id"] == 5 and env.published == []


def test_found_by_words_in_mailshot_first_and_reads_only(env):
    row = {"_id": "m-1", "from": "Dana <dana@acme-print.example>", "subject": "Ordering experience", "date": "2026-11-28T07:00:00.000Z",
           "attachments": [], "preview": ""}
    shot = FakeMailshot([row], {"m-1": BODY})
    out = run(env, query="dana ordering", mailshot=shot, gmail=FakeGmail([]))
    assert out["status"] == "proposed" and env.published[0]["source"]["channel"] == "mailshot"
    assert shot.reads == ["m-1"]


def test_query_falls_through_to_gmail(env):
    gm = FakeGmail([GMAIL_MAIL])
    out = run(env, query="dana", mailshot=FakeMailshot(), gmail=gm)
    assert out["status"] == "proposed" and gm.calls[0][0] == "check_inbox"


def test_mail_not_found_says_so_and_stops(env):
    out = run(env, query="nobody", mailshot=FakeMailshot(), gmail=FakeGmail([]))
    assert out["status"] == "not_found" and env.published == []
    assert env.steps[-1][0] == "blocked"


def test_when_incoming_refuses_the_telegram_card_goes_instead(env):
    def refuse(_proposal):
        raise RuntimeError("HTTP 422")
    out = run(env, message_id=GMAIL_ID, gmail=FakeGmail([GMAIL_MAIL]), mailshot=FakeMailshot(), publish_fn=refuse)
    assert out["status"] == "card_only" and len(env.cards) == 1
    assert env.cards[0]["token"].startswith(of.OC_CARD_PREFIX)
    assert any("did not accept" in t for _k, t, _p in env.steps) and env.steps[-1][0] == "waiting"


def test_nothing_delivered_is_reported_as_blocked_not_success(env):
    def refuse(_proposal):
        raise RuntimeError("down")
    out = run(env, message_id=GMAIL_ID, gmail=FakeGmail([GMAIL_MAIL]), mailshot=FakeMailshot(), publish_fn=refuse,
              send=lambda *a, **k: False)
    assert out["status"] == "failed" and env.steps[-1][0] == "blocked"


def test_a_billing_mail_uses_the_records_probe(env, monkeypatch):
    row = {"_id": "m-2", "from": "Una <una@client-a.example>", "subject": "Correction on invoice I0740", "date": "2026-11-28T07:00:00.000Z",
           "attachments": [], "preview": ""}
    shot = FakeMailshot([row], {"m-2": "On invoice I0740 the first item is R850, it should be R605."})
    import distr.core.initiative.client_issue_pipeline as pipe

    def investigate(_text):
        return {"stated": {}, "probe": {}, "analysis": {"findings": [{
            "invoice": "I0740", "line_id": 1, "line": "Filling", "billed": "850", "agreed": "605", "amount": "850",
            "amount_should_be": "605", "customer": "Client A", "agreed_basis": "agreed rate", "is_sent": False,
            "cause": {"summary": "The project rate was never set."}}], "project_findings": []}}
    out = run(env, query="una invoice", mailshot=shot, gmail=FakeGmail([]), investigate_fn=investigate,
              complete=model(kind="billing_error"))
    assert out["status"] == "proposed"
    assert env.published[0]["root_cause"] == ["The project rate was never set."]
    assert any("found the cause on the records" in t for _k, t, _p in env.steps)


def test_nothing_in_the_path_sends_to_a_client(env, monkeypatch):
    sent = []
    monkeypatch.setattr(FakeGmail, "send_email", lambda *a, **k: sent.append(a), raising=False)
    monkeypatch.setattr(FakeGmail, "reply_to_email", lambda *a, **k: sent.append(a), raising=False)
    run(env, message_id=GMAIL_ID, gmail=FakeGmail([GMAIL_MAIL]), mailshot=FakeMailshot())
    assert sent == []
    assert {c for c, _ in FakeGmail([GMAIL_MAIL]).calls} <= {"get_email", "check_inbox"}


def test_mail_reference_comes_from_the_chat_that_just_read_it(monkeypatch):
    class Q:
        def __init__(self, rows): self.rows = rows
        def filter(self, *a): return self
        def order_by(self, *a): return self
        def limit(self, n): return self
        def all(self): return self.rows

    class S:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def query(self, *a): return Q([("Message ID: 1a221ad0032d80df From: Dana <dana@acme-print.example> Subject: x",)])

    monkeypatch.setattr("distr.core.db.get_session", lambda: S())
    assert cmr.mail_reference_from_chat(1381) == GMAIL_ID
    assert cmr.mail_reference_from_chat(None) == ""


# --------------------------------------------------------------------------- 4. the original request, end to end

def test_original_request_through_the_tool_narrates_to_telegram(world, env, monkeypatch, tmp_path):  # noqa: F811
    """Paul, from Telegram: 'check that that's a proposal ... create it ... push it as a thread.'
    The previous turn read the mail in Gmail, so 'that' is resolved from the chat. Prints the sequence."""
    relay, _remote = world
    from distr.core.agent.tools.system.client_mail_proposal import ClientMailProposalTool
    from distr.core.chat_manager import bind_active_chat_turn

    gmail = FakeGmail([GMAIL_MAIL])
    orig = cmr.handle_request
    monkeypatch.setattr(cmr, "mail_reference_from_chat", lambda chat_id: GMAIL_ID if chat_id == 1381 else "")
    monkeypatch.setattr(cmr, "handle_request", lambda *a, **k: orig(*a, **k, settings=SETTINGS, gmail=gmail,
                        mailshot=FakeMailshot(), complete=model(), publish_fn=env.publish, send=env.send,
                        existing_fn=lambda: []))

    def turn(etype, title, summary=""):
        jp.on_chat_turn_event({"chat_id": 1381, "turn_id": 2472, "event_type": etype, "title": title, "summary": summary})
        relay.pump()

    from distr.core.orchestration_events import emit_orchestration_event
    emit_orchestration_event(source="chat", event_type="chat_message_added", status="added", summary="user message",
                             payload={"subtype": "chat_user_message", "surface": "telegram", "chat_id": 1381,
                                      "chat_row_id": 2472, "content_preview": "Check that Dana's mail is a proposal, create it, push it as a thread"})
    turn("turn_started", "Request received")
    turn("acknowledgment", "Working on it.")
    turn("tool_started", "Client Mail Proposal")
    bind_active_chat_turn(1381, 2472)
    result = ClientMailProposalTool()._run(request="check that's a proposal")
    for _ in range(40):
        relay.pump()
        time.sleep(0.12)
    turn("tool_completed", "Client Mail Proposal", result)
    turn("synthesis_started", "Preparing the answer")
    turn("turn_completed", "Answer ready", "Filed it.")
    assert _wait(lambda: (relay.pump(), "answer sent" in relay.log[-1][2])[1], 8)
    time.sleep(0.5)
    relay.pump()
    print("\n--- Telegram: original request through client_mail_proposal ---")
    for stamp, route, text in relay.log:
        print(f"[{stamp}] {route}\n" + "\n".join("    " + line for line in text.splitlines()))
    assert result.startswith("Proposal filed in Incoming and sent to Telegram")
    assert len([e for e in relay.log if "send" in e[1]]) == 1            # ONE status message, edited
    final = relay.log[-1][2]
    assert final.startswith("Check that Dana's mail is a proposal, create it, push it as a thread - waiting for you")
    assert "answer sent; still waiting for your approval" in final     # the chat answer is done, the job is not
    seen = {line for _s, _r, text in relay.log for line in text.splitlines()}
    for expected in ("found: ", "assessing the problem", "Created proposal: filed in Incoming #77; Accept/Decline card sent to Telegram"):
        assert any(expected in line for line in seen), expected      # milestones reach Paul (the message keeps the last 8 lines)
    own = [(k, t, p) for k, t, p in env.steps if p is not None]       # the other lines come from the chat-turn bridge
    assert len(own) == 10 and {p for _k, _t, p in own} == {"turn:1381:2472"}   # the path joins the chat turn's one message   # the path joins the chat turn's one message
    assert len(env.published) == 1 and env.cards == [] and gmail.calls == [("get_email", GMAIL_ID)]

