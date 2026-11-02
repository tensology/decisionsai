from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, WhatsAppPhoneLink
from distr.core.kanban.whatsapp_jid import (
    upsert_whatsapp_phone_link,
    whatsapp_jid_match_keys,
)


def test_private_jid_variants_include_lid_and_snet():
    keys = whatsapp_jid_match_keys("158961051389998@lid")
    assert "158961051389998@lid" in keys
    assert "158961051389998@s.whatsapp.net" in keys
    assert "158961051389998" in keys


def test_group_jid_does_not_invent_private_variants():
    keys = whatsapp_jid_match_keys("120363339060471282@g.us", "120363339060471282")
    assert "120363339060471282@g.us" in keys
    assert "120363339060471282@lid" not in keys
    assert "120363339060471282@s.whatsapp.net" not in keys


def test_upsert_collapses_lid_and_snet_to_one_link(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'wa_jid.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        first, created = upsert_whatsapp_phone_link(
            session,
            board_id=7,
            phone_jid="158961051389998@s.whatsapp.net",
            phone_number="158961051389998",
            contact_name="Miriam",
            auto_snapshot=False,
        )
        assert created is True
        second, created2 = upsert_whatsapp_phone_link(
            session,
            board_id=15,
            phone_jid="158961051389998@lid",
            phone_number="158961051389998",
            contact_name="Miriam von Hoesslin",
            auto_snapshot=False,
        )
        session.commit()
        assert created2 is False
        assert second.id == first.id
        assert second.board_id == 15
        assert second.phone_jid == "158961051389998@lid"
        assert session.query(WhatsAppPhoneLink).count() == 1
