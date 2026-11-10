"""Bounded, exact-conversation context for project inference, never new work."""


def earlier_context(conversation):
    if conversation.get('source') != 'whatsapp':
        return []
    from distr.core.db import WhatsAppMessage, get_session
    from .source_context import safe_source_body
    ids = [str(row.get('source_message_id')) for row in conversation['items'] if row.get('source_message_id')]
    try:
        with get_session() as db:
            selected = db.query(WhatsAppMessage).filter(WhatsAppMessage.jid == conversation['thread_id'],
                        WhatsAppMessage.message_id.in_(ids)).all()
            times = [row.whatsapp_timestamp for row in selected if row.whatsapp_timestamp]
            if not times:
                return []
            rows = db.query(WhatsAppMessage).filter(WhatsAppMessage.jid == conversation['thread_id'],
                WhatsAppMessage.whatsapp_timestamp < min(times)).order_by(
                    WhatsAppMessage.whatsapp_timestamp.desc(), WhatsAppMessage.id.desc()).limit(80).all()
            return [{'id': row.message_id, 'body': safe_source_body(str(row.text or row.caption or ''))[:8000],
                     'direction': 'outbound' if row.from_me else 'inbound', 'context_only': True}
                    for row in reversed(rows)]
    except Exception:
        # No history is evidence of uncertainty, never permission to infer a link.
        return []


def inferred_project(project, candidates, data, conversation, context):
    if len(candidates) == 1:
        return candidates[0]
    evidence = data.get('project_evidence_message_ids')
    known = {str(row.get('source_message_id')) for row in conversation['items'] if row.get('source_message_id')}
    known.update(str(row['id']) for row in context if row.get('id'))
    try:
        confident = .85 <= float(data.get('confidence') or 0) <= 1
    except (ValueError, TypeError):
        confident = False
    grounded = isinstance(evidence, list) and 0 < len(evidence) <= 20 and all(isinstance(v, str) and v in known for v in evidence)
    return project if project and confident and grounded and not data.get('needs_project') else None
