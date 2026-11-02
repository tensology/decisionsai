"""Normalize and match WhatsApp JIDs across @lid / @s.whatsapp.net / bare phone forms."""

from __future__ import annotations

def whatsapp_jid_local_part(value: str | None) -> str:
    """Return the local part of a JID/phone (before @), stripping device :suffix."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    local = raw.split("@", 1)[0]
    return local.split(":", 1)[0].strip()


def is_whatsapp_group_jid(value: str | None) -> bool:
    raw = str(value or "").strip().lower()
    return raw.endswith("@g.us") or ("-" in whatsapp_jid_local_part(raw) and "@" not in raw)


def whatsapp_jid_match_keys(*values: str | None) -> list[str]:
    """Candidate keys for equating one contact/chat across JID forms.

    Private chats may appear as phone@s.whatsapp.net, phone@lid, or bare phone.
    Group chats stay on their @g.us identity (and bare group id).
    """
    keys: list[str] = []
    seen: set[str] = set()

    def add(item: str) -> None:
        item = str(item or "").strip()
        if not item or item in seen:
            return
        seen.add(item)
        keys.append(item)

    locals_found: list[str] = []
    groupish = False
    privateish = False
    for value in values:
        raw = str(value or "").strip()
        if not raw:
            continue
        add(raw)
        local = whatsapp_jid_local_part(raw)
        if not local:
            continue
        add(local)
        locals_found.append(local)
        lower = raw.lower()
        if lower.endswith("@g.us") or "-" in local:
            groupish = True
            if not lower.endswith("@g.us"):
                add(f"{local}@g.us")
        if lower.endswith("@s.whatsapp.net") or lower.endswith("@lid"):
            privateish = True

    # Only invent private JID variants when we already know this is a DM identity.
    # Bare numeric ids are shared by groups and phones — do not cross-map them.
    if privateish and not groupish:
        for local in locals_found:
            add(f"{local}@s.whatsapp.net")
            add(f"{local}@lid")
    return keys


def prefer_whatsapp_jid(stored_jid: str | None, *observed: str | None) -> str:
    """Prefer an observed message JID when it matches the stored link identity."""
    stored = str(stored_jid or "").strip()
    stored_keys = set(whatsapp_jid_match_keys(stored))
    for value in observed:
        raw = str(value or "").strip()
        if not raw:
            continue
        if raw in stored_keys or (set(whatsapp_jid_match_keys(raw)) & stored_keys):
            if "@" in raw:
                return raw
    return stored


def find_existing_whatsapp_link(session, phone_jid: str = "", phone_number: str = ""):
    """Return the single existing WhatsAppPhoneLink matching any JID variant, if any."""
    from sqlalchemy import or_

    from distr.core.db import WhatsAppPhoneLink

    keys = whatsapp_jid_match_keys(phone_jid, phone_number)
    if not keys:
        return None
    return (
        session.query(WhatsAppPhoneLink)
        .filter(
            or_(
                WhatsAppPhoneLink.phone_jid.in_(keys),
                WhatsAppPhoneLink.phone_number.in_(keys),
            )
        )
        .order_by(WhatsAppPhoneLink.id.asc())
        .first()
    )


def upsert_whatsapp_phone_link(
    session,
    *,
    board_id: int,
    phone_jid: str,
    phone_number: str | None = None,
    contact_name: str | None = None,
    auto_snapshot: bool | None = False,
    move_board: bool = True,
):
    """Create or reuse a WhatsAppPhoneLink so one chat identity maps to one row.

    If a variant of the JID already exists, update that row (optionally moving board)
    instead of inserting a second link.
    """
    from distr.core.db import WhatsAppPhoneLink

    jid = str(phone_jid or "").strip()
    if not jid:
        raise ValueError("phone_jid is required")
    number = str(phone_number or "").strip() or whatsapp_jid_local_part(jid)
    existing = find_existing_whatsapp_link(session, phone_jid=jid, phone_number=number)
    if existing is not None:
        if move_board:
            existing.board_id = int(board_id)
        # Prefer the caller JID when it is a full JID; keep richer contact names.
        if "@" in jid:
            existing.phone_jid = jid
        if number:
            existing.phone_number = number
        if contact_name is not None and str(contact_name).strip():
            existing.contact_name = str(contact_name).strip()
        if auto_snapshot is not None:
            existing.auto_snapshot = bool(auto_snapshot)
        session.flush()
        return existing, False

    link = WhatsAppPhoneLink(
        board_id=int(board_id),
        phone_jid=jid,
        phone_number=number,
        contact_name=(str(contact_name).strip() if contact_name is not None else ""),
        auto_snapshot=bool(auto_snapshot) if auto_snapshot is not None else False,
    )
    session.add(link)
    session.flush()
    return link, True
