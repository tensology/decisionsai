from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, WhatsAppMessage, WhatsAppPhoneLink
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.gui.web.routes.kanban import (
    _build_whatsapp_ticket_draft,
    _whatsapp_media_items,
    _resolve_board_whatsapp_snapshot,
    _whatsapp_snapshot_group_filter,
    _whatsapp_snapshot_group_for_ticket,
    _validate_whatsapp_ticket_quality,
)


def _message(**kwargs):
    defaults = {
        "id": 1,
        "message_id": "wa_1",
        "jid": "27710000001@s.whatsapp.net",
        "jid_phone": "27710000001",
        "sender_phone": "27710000001",
        "sender_push_name": "Client",
        "from_me": False,
        "text": "Please fix the checkout error when card payments fail.",
        "caption": "",
        "media_type": None,
        "media_mime_type": None,
        "media_filename": None,
        "media_local_path": None,
        "whatsapp_timestamp": 1_700_000_000,
        "created_date": datetime.now(timezone.utc).replace(tzinfo=None),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_whatsapp_draft_has_client_ready_quality_sections():
    messages = [
        _message(text="Please fix the checkout error when card payments fail."),
        _message(
            id=2,
            message_id="wa_2",
            text="It happens after the user clicks pay.",
            media_type="voice",
            media_filename="note.ogg",
            caption="[Transcription] The customer sees a blank page after payment fails.",
        ),
    ]

    draft = _build_whatsapp_ticket_draft(messages)
    quality = _validate_whatsapp_ticket_quality(
        draft["title"],
        draft["description"],
        messages,
        {"counts": {"media": 1, "analyzed": 1, "missing": 0, "failed": 0}},
    )

    assert "Client request:" in draft["description"]
    assert "Transcript:" in draft["description"]
    assert "Media evidence:" in draft["description"]
    assert "Acceptance criteria:" in draft["description"]
    assert "Questions / ambiguities:" in draft["description"]
    assert quality["passed"] is True


def test_whatsapp_image_ocr_noise_is_not_written_into_ticket_description():
    messages = [
        _message(text="Please review the cabinet photo and confirm the dimensions."),
        _message(
            id=2,
            message_id="wa_2",
            text="",
            caption="[OCR] approximate location: left 10 top 20 bounding box 300x200\n\nPhoto of the cabinet front.",
            media_type="image",
            media_filename="cabinet.jpg",
            media_local_path="whatsapp/cabinet.jpg",
        ),
    ]

    draft = _build_whatsapp_ticket_draft(messages)

    assert "Photo of the cabinet front." in draft["description"]
    assert "cabinet.jpg" in draft["description"]
    assert "OCR" not in draft["description"]
    assert "bounding box" not in draft["description"]
    assert "approximate location" not in draft["description"]


def test_whatsapp_media_items_use_stable_message_preview_urls_without_local_cache():
    messages = [
        _message(
            id=42,
            message_id="WA_KEY_42",
            text="",
            media_type="image",
            media_filename="photo.jpg",
            media_mime_type="image/jpeg",
            media_local_path=None,
        ),
        _message(
            id=43,
            message_id="WA_KEY_43",
            text="",
            media_type="document",
            media_filename="brief.pdf",
            media_mime_type="application/pdf",
            media_local_path="whatsapp_media/brief.pdf",
        ),
    ]

    items = _whatsapp_media_items(messages)

    assert items[0]["preview_url"] == "/api/tickets/whatsapp/relay-media/42?wa_key=WA_KEY_42"
    assert items[0]["download_url"] == items[0]["preview_url"]
    assert items[0]["local_preview_url"] == ""
    assert items[0]["media_mime_type"] == "image/jpeg"
    assert items[1]["preview_url"] == "/api/tickets/whatsapp/relay-media/43?wa_key=WA_KEY_43"
    assert items[1]["local_preview_url"] == "/api/tickets/whatsapp/media?path=brief.pdf"


def test_missing_whatsapp_media_is_negatively_cached_with_expiry(monkeypatch):
    from distr.gui.web.routes import kanban_whatsapp

    monkeypatch.setattr(kanban_whatsapp, "_wa_media_negative_cache", {})
    key = kanban_whatsapp._wa_media_cache_key(42, "WA_KEY_42", "")

    assert kanban_whatsapp._wa_media_negative_cached(key, now=100.0) is False
    kanban_whatsapp._remember_missing_wa_media(
        key,
        ttl_seconds=300,
        now=100.0,
    )
    assert kanban_whatsapp._wa_media_negative_cached(key, now=399.9) is True
    assert kanban_whatsapp._wa_media_negative_cached(key, now=400.1) is False


def test_resolving_reviewed_whatsapp_messages_rejects_changed_batch(tmp_path):
    import distr.core.db.kanban  # noqa: F401

    db_path = tmp_path / "wa_intake.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    session = Session()
    try:
        board = KanbanBoard(name="WhatsApp Board")
        session.add(board)
        session.flush()
        lane = KanbanLane(board_id=board.id, name="Backlog", position=0)
        session.add(lane)
        link = WhatsAppPhoneLink(
            board_id=board.id,
            phone_jid="27710000001@s.whatsapp.net",
            phone_number="27710000001",
            contact_name="Client",
        )
        session.add(link)
        msg = WhatsAppMessage(
            message_id="wa_1",
            jid="27710000001@s.whatsapp.net",
            jid_phone="27710000001",
            sender_phone="27710000001",
            sender_push_name="Client",
            text="Build the thing from WhatsApp.",
            whatsapp_timestamp=1_700_000_000,
            snapshot_group="board_1_ticket_99",
        )
        session.add(msg)
        session.flush()

        with pytest.raises(Exception) as exc:
            _resolve_board_whatsapp_snapshot(session, board.id, link_id=link.id, message_ids=[msg.id])

        assert getattr(exc.value, "status_code", None) == 409
    finally:
        session.close()


def test_board_whatsapp_snapshot_uses_latest_two_visible_unticketed_message_days(tmp_path):
    import distr.core.db.kanban  # noqa: F401

    db_path = tmp_path / "wa_two_visible_days.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def ts(day: str) -> int:
        return int(datetime.fromisoformat(f"{day}T09:00:00+00:00").timestamp())

    session = Session()
    try:
        board = KanbanBoard(name="Linked Board")
        session.add(board)
        session.flush()
        session.add(KanbanLane(board_id=board.id, name="Backlog", position=0))
        link = WhatsAppPhoneLink(
            board_id=board.id,
            phone_jid="27710000001@s.whatsapp.net",
            phone_number="27710000001",
            contact_name="Client",
        )
        session.add(link)
        session.add_all([
            WhatsAppMessage(
                message_id="old_day",
                jid="27710000001@s.whatsapp.net",
                jid_phone="27710000001",
                text="old visible day",
                whatsapp_timestamp=ts("2026-06-06"),
                created_date=datetime(2026, 6, 6, tzinfo=timezone.utc),
            ),
            WhatsAppMessage(
                message_id="second_day",
                jid="27710000001@s.whatsapp.net",
                jid_phone="27710000001",
                text="second latest visible day",
                whatsapp_timestamp=ts("2026-06-08"),
                created_date=datetime(2026, 6, 8, tzinfo=timezone.utc),
            ),
            WhatsAppMessage(
                message_id="latest_day",
                jid="27710000001@s.whatsapp.net",
                jid_phone="27710000001",
                text="latest visible day",
                whatsapp_timestamp=ts("2026-06-09"),
                created_date=datetime(2026, 6, 9, tzinfo=timezone.utc),
            ),
        ])
        session.flush()

        snapshot = _resolve_board_whatsapp_snapshot(session, board.id, link_id=link.id)

        assert [m.message_id for m in snapshot["messages"]] == ["second_day", "latest_day"]
        assert snapshot["intake_stats"]["scope"] == "latest_two_visible_days"
    finally:
        session.close()


def test_whatsapp_snapshot_group_uses_ticket_id_and_cleanup_filter_matches_current_pattern(tmp_path):
    import distr.core.db.kanban  # noqa: F401

    db_path = tmp_path / "wa_snapshot_group.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    session = Session()
    try:
        current = _whatsapp_snapshot_group_for_ticket(7, 123)
        assert current == "board_7_ticket_123"

        rows = [
            WhatsAppMessage(message_id="wa_current", jid="27710000001@s.whatsapp.net", text="current", snapshot_group=current, processed=True),
            WhatsAppMessage(message_id="wa_legacy", jid="27710000001@s.whatsapp.net", text="legacy", snapshot_group="123_old", processed=True),
            WhatsAppMessage(message_id="wa_other", jid="27710000001@s.whatsapp.net", text="other", snapshot_group="board_7_ticket_124", processed=True),
        ]
        session.add_all(rows)
        session.flush()

        matched = session.query(WhatsAppMessage).filter(_whatsapp_snapshot_group_filter(123)).all()

        assert {m.message_id for m in matched} == {"wa_current", "wa_legacy"}
    finally:
        session.close()
