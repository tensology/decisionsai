from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, WhatsAppMessage, WhatsAppPhoneLink
from distr.core.db.kanban import KanbanBoard
from distr.core.incoming import service as development_incoming


def test_development_incoming_returns_whatsapp_and_readable_gmail_threads(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    @contextmanager
    def session_scope():
        session = factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    monkeypatch.setattr(development_incoming, "get_session", session_scope)
    monkeypatch.setattr(
        development_incoming,
        "_load_gmail_inbox",
        lambda max_results: ([{
            "id": "gmail-1",
            "threadId": "gmail-thread-1",
            "subject": "Menu launch review",
            "from": "Client <client@example.com>",
            "to": "Paul <paul@example.com>",
            "date": "Tue, 25 Aug 2026 11:00:00 +0000",
            "snippet": "Please review the launch copy",
            "body": "<p>Please review the <strong>launch copy</strong>.</p>",
            "labels": ["INBOX", "UNREAD"],
            "attachments": [],
        }], {"connected": True, "error": ""}),
    )
    monkeypatch.setattr(
        development_incoming,
        "_load_mailshot_inbox",
        lambda max_results: ([{
            "id": "mailshot-1",
            "thread_id": "mailshot-thread-1",
            "subject": "Tensology deployment",
            "from": "Ops <ops@tensology.com>",
            "to": "Paul <paul@tensology.com>",
            "date": "2026-08-25T08:30:00Z",
            "snippet": "The deployment report is ready",
            "body": "The deployment report is ready.",
            "read": False,
            "attachments": [{"filename": "report.pdf"}],
        }], {"connected": True, "error": ""}),
    )
    monkeypatch.setattr(
        "distr.core.work_intake.get_work_intake_service",
        lambda: type("Intake", (), {"list_inbox": lambda self, limit: [{"event_id": 9, "source": "gmail", "text": "Approve the revised menu copy", "created_at": "2026-08-25T09:00:00Z", "needs_attention": True}]})(),
    )

    with session_scope() as session:
        board = KanbanBoard(name="Menu Project")
        second_board = KanbanBoard(name="Website Refresh")
        session.add_all([board, second_board])
        session.flush()
        session.add(WhatsAppPhoneLink(board_id=board.id, phone_jid="menu@g.us", contact_name="Menu client", auto_snapshot=False))
        session.add(WhatsAppMessage(message_id="wa-1", jid="menu@g.us", sender_push_name="Client", text="Add vegetarian filters", whatsapp_timestamp=100, from_me=False, processed=False, raw_data='{"id": 901}'))
        session.add(WhatsAppMessage(message_id="wa-out", jid="menu@g.us", sender_push_name="Paul", text="I will handle it", whatsapp_timestamp=99, from_me=True, processed=True, media_type="audio", media_mime_type="audio/ogg"))
        session.add(WhatsAppMessage(message_id="wa-2", jid="new-group@g.us", chat_type="group", sender_push_name="Member", text="Review the launch copy", raw_data='{"group_name":"Launch group"}', whatsapp_timestamp=200, from_me=False, processed=False))

    result = development_incoming.list_development_incoming(limit=1)

    assert result["counts"] == {"whatsapp": 3, "gmail": 2, "mailshot": 1}
    assert all(item["source"] != "telegram" for item in result["items"])
    whatsapp = next(item for item in result["items"] if item["source_thread_id"] == "menu@g.us")
    assert whatsapp["board_name"] == "Menu Project"
    assert whatsapp["can_snapshot"] is True
    assert whatsapp["source_sequence"] == 901
    assert whatsapp["conversation_label"] == "Menu client"
    assert [link["board_name"] for link in whatsapp["links"]] == ["Menu Project"]
    outgoing = next(item for item in result["items"] if item.get("source_message_id") == "wa-out")
    assert outgoing["from_me"] is True
    assert outgoing["direction"] == "outbound"
    assert outgoing["media_mime_type"] == "audio/ogg"
    group = next(item for item in result["items"] if item["source_thread_id"] == "new-group@g.us")
    assert group["conversation_label"] == "Launch group"
    assert group["chat_type"] == "group"
    gmail = next(item for item in result["items"] if item["key"] == "gmail:gmail-1")
    assert gmail["source_thread_id"] == "gmail-thread-1"
    assert gmail["conversation_label"] == "Menu launch review"
    assert gmail["text"] == "Please review the launch copy."
    assert gmail["unread"] is True
    assert result["channels"]["gmail"] == {"connected": True, "error": ""}
    mailshot = next(item for item in result["items"] if item["key"] == "mailshot:mailshot-1")
    assert mailshot["conversation_label"] == "Tensology deployment"
    assert mailshot["attachments"] == [{"filename": "report.pdf", "message_id": "mailshot-1", "provider": "mailshot"}]
    assert result["channels"]["mailshot"] == {"connected": True, "error": ""}
    assert result["links"][0]["label"] == "Menu client"


def test_mail_list_snippet_strips_html_for_gmail_and_mailshot():
    """Incoming list preview must be plain text for both mail tabs."""
    from distr.core.incoming.service import _email_list_snippet, _email_text, _mail_plain_and_html

    dirty = (
        "<html><head><style>.x{color:red}</style><title>Ignore</title></head>"
        "<body><script>alert(1)</script><p>Hi&nbsp;<b>Paul</b> &amp; team</p></body></html>"
    )
    assert "<" not in _email_text(dirty)
    assert "alert" not in _email_text(dirty)
    assert "color:red" not in _email_text(dirty)
    assert _email_text(dirty) == "Hi Paul & team"
    assert _email_list_snippet(dirty) == "Hi Paul & team"

    gmail_text, gmail_html, gmail_snippet = _mail_plain_and_html({
        "body": dirty,
        "snippet": "<div>Gmail&nbsp;preview</div>",
    })
    assert gmail_html.startswith("<html>")
    assert gmail_text == "Hi Paul & team"
    assert gmail_snippet == "Gmail preview"
    assert "<" not in gmail_snippet

    mailshot_text, mailshot_html, mailshot_snippet = _mail_plain_and_html({
        "text_body": dirty,
        "body_html": dirty,
        "snippet": dirty,
    })
    assert mailshot_text == "Hi Paul & team"
    assert mailshot_snippet == "Hi Paul & team"
    assert "<style" not in mailshot_snippet


def test_whatsapp_source_sequence_uses_relay_id_not_reverse_import_id():
    import json

    # Same-second messages imported from the relay's newest-first listing.
    reply = WhatsAppMessage(id=1, message_id="reply", jid="demo@lid", raw_data=json.dumps({"id": 102}))
    question = WhatsAppMessage(id=2, message_id="question", jid="demo@lid", raw_data=json.dumps({"id": 101}))
    assert development_incoming._whatsapp_source_sequence(question) == 101
    assert development_incoming._whatsapp_source_sequence(reply) == 102
    for raw in ("", "not json", "[]", '{"id": true}', '{"id": "invalid"}', '{"id": -1}'):
        question.raw_data = raw
        assert development_incoming._whatsapp_source_sequence(question) is None


def test_whatsapp_ordering_uses_instants_and_original_relay_sequence():
    from pathlib import Path
    import subprocess

    source = Path('distr/gui/web/static/development/incoming/index.js').resolve().as_uri()
    loader = Path('tests/ui/strip-query-loader.mjs').resolve().as_uri()
    script = '''
import assert from 'node:assert/strict';
const {compareIncomingMessagesNewestFirst: compare} = await import(SOURCE);
const question = {source:'whatsapp', database_id:2, source_sequence:101, created_at:'2026-11-05T11:10:00Z', text:'question'};
const reply = {source:'whatsapp', database_id:1, source_sequence:102, created_at:'2026-11-05T11:10:00Z', text:'reply'};
// This is the API's current newest-first tie ordering by local DB id.
assert.deepEqual([question,reply].sort(compare).reverse().map(row=>row.text), ['question','reply']);
// Real instants, not their timezone spelling, decide chronological order.
const early={...question,created_at:'2026-11-05T12:00:00+02:00',text:'early'};
const late={...reply,created_at:'2026-11-05T11:30:00Z',text:'late'};
assert.deepEqual([early,late].sort(compare).reverse().map(row=>row.text), ['early','late']);
// A realtime pair without relay sequence still follows its actual timestamp.
assert.ok(compare({...question,source_sequence:null,created_at:'2026-11-05T11:09:59Z'}, {...reply,source_sequence:null}) > 0);
'''.replace('SOURCE', repr(source))
    result = subprocess.run(['node', '--experimental-loader', loader, '--input-type=module', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_unlinked_private_whatsapp_label_uses_contact_not_latest_outgoing_sender():
    from pathlib import Path
    import subprocess

    source = Path('distr/gui/web/static/development/incoming/index.js').resolve().as_uri()
    loader = Path('tests/ui/strip-query-loader.mjs').resolve().as_uri()
    script = '''
import assert from 'node:assert/strict';
const {incomingConversationLabel: label,compareIncomingMessagesNewestFirst: compare} = await import(SOURCE);
const question={source:'whatsapp',source_sequence:47068,created_at:'2026-11-05T11:10:00Z',from_me:false,chat_type:'private',conversation_label:'Miriam von Hoesslin',sender:'Miriam von Hoesslin'};
const reply={...question,source_sequence:47069,from_me:true,conversation_label:'Paul Hoft von Hoesslin',sender:'Paul Hoft von Hoesslin'};
const messages=[question,reply].sort(compare);
const conversation={source:'whatsapp',threadId:'contact@lid',messages};
assert.equal(messages[0],reply);
assert.equal(label(conversation),'Miriam von Hoesslin');
// An explicit linked name and a saved link label remain authoritative.
assert.equal(label({...conversation,messages:[{...reply,link_id:1,conversation_label:'Saved contact label'},question]}),'Saved contact label');
assert.equal(label({...conversation,syntheticLinks:[{id:1,label:'Saved link label'}]}),'Saved link label');
// Group names must not become the most recent incoming participant's name.
assert.equal(label({...conversation,threadId:'group@g.us',messages:[{...reply,chat_type:'group',conversation_label:'Project group'},question]}),'Project group');
// Gmail subject labels and an outgoing-only fallback keep their old behavior.
assert.equal(label({...conversation,source:'gmail'}),'Paul Hoft von Hoesslin');
assert.equal(label({...conversation,messages:[reply]}),'Paul Hoft von Hoesslin');
'''.replace('SOURCE', repr(source))
    result = subprocess.run(['node', '--experimental-loader', loader, '--input-type=module', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
