"""Check explicitly linked mail in Decisions AI using the original message IDs."""
from email.utils import parseaddr
import logging
import re
import threading

from .project_links import mail_project_links
from .expiry import ACTIVE_STATUSES

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_status = {}


def mail_check_status():
    with _lock:
        return dict(_status)


def start_mail_check():
    from distr.core.initiative.attention import address_links

    with _lock:
        if _status.get("source_state") in {"queued", "running"}:
            return dict(_status)
        links = mail_project_links()
        from .project_links import project_links
        discover = bool(address_links()) or any(row.get('source') == 'mail_sender' and row.get('projects') for row in project_links())
        if not links and not discover:
            return {}
        _status.update(source_state="queued", recalibration={"updated": 0})
        threading.Thread(target=_run, args=(links, discover), name="incoming-linked-mail", daemon=True).start()
        return dict(_status)


def _run(links, discover):
    from .events import publish_incoming_change
    from .proposals import invalidate_proposals_list_cache

    with _lock:
        _status["source_state"] = "running"
    updated, failed = 0, False
    if discover:
        try:
            discovered, failed = _discover_linked_mail()
            links = {**discovered, **links}
        except Exception:
            failed = True
            logger.exception("Could not discover mail linked by sender")
    try:
        if links:
            updated, check_failed = check_linked_mail(links)
            failed |= check_failed
    except Exception:
        failed = True
        logger.exception("Could not check linked incoming mail")
    invalidate_proposals_list_cache()
    with _lock:
        _status.update(source_state="failed" if failed else "complete", recalibration={"updated": updated})
    publish_incoming_change(status="proposal_sync")


def _discover_linked_mail():
    from distr.core.initiative.attention import address_project_ids
    from .service import list_development_incoming
    from .project_links import project_links, _sender_projects
    senders = _sender_projects(project_links())

    incoming = list_development_incoming(limit=150)
    links = {}
    # Inbox items arrive newest first. Use the newest matching inbound item as
    # the anchor, then read the verified original from its provider.
    for item in incoming.get("items", []):
        source = item.get("source")
        thread, message, project = (item.get(key) for key in ("source_thread_id", "source_message_id", "project_id"))
        if source not in {"gmail", "mailshot"} or item.get("from_me") or not (thread and message and project):
            continue
        _, sender = parseaddr(str(item.get("sender") or ""))
        if sender.lower() in senders:
            targets = {row.get('decisions_project_id') for row in senders[sender.lower()]}
        else:
            targets, _ = address_project_ids(sender)
        if targets == {project}:
            links.setdefault((source, thread), (project, message))
    failed = any((incoming.get("channels", {}).get(source) or {}).get("error") for source in ("gmail", "mailshot"))
    return links, failed


def _django_failure(item):
    subject = str(item.get("conversation_label") or "").strip()
    text = str(item.get("text") or "")
    return (not item.get("from_me") and subject.lower().startswith("[django]")
            and re.search(r"\b(?:failed|failure|error|exception)\b", subject, re.I)
            and re.search(r"\btraceback\b", text, re.I))


def check_linked_mail(links):
    from distr.core.settings import load_settings_from_db
    from .proposal_conversation import _mail_conversation
    from .proposal_sync import fetch_remote_proposals
    from . import proposals

    settings = load_settings_from_db()
    projects = {row["id"]: row for row in proposals._candidate_projects(project_ids={link[0] for link in links.values()})}
    saved = proposals.apply_project_corrections(proposals._saved_local_proposals())
    accepted = proposals._accepted_proposal_keys(include_terminal=True)
    covered = proposals._dismissed_message_coverage()
    try:
        remote = fetch_remote_proposals()
    except Exception:
        remote = []
    for row in remote:
        source = row.get("source")
        thread = (row.get("source_conversation") or {}).get("thread_id")
        local = links.get((source, thread))
        project = row.get("project") or {}
        if local and (str(row.get("stored_status") or row.get("status") or "") not in ACTIVE_STATUSES
                      or project.get("decisions_project_id") == local[0]):
            covered.setdefault(f"{source}:{thread}", set()).update(map(str, row.get("source_message_ids") or []))
    updated, failed = 0, False
    for (source, thread), (project_id, message_id) in links.items():
        project = projects.get(project_id)
        if not project:
            continue
        try:
            key = f"{source}:{thread}"
            seen = set(covered.get(key, ()))
            for row in saved:
                if row.get("dedupe_key") != key:
                    continue
                if proposals._proposal_handoff_key(row) not in accepted and row.get("project_id") != project_id:
                    proposals.reassign_proposal_project(row, project_id)
                    updated += 1
                seen.update(map(str, row.get("message_ids") or []))
            items, anchor = _mail_conversation(source, thread, [message_id])
            if not anchor:
                raise LookupError("The linked message is no longer available")
            fresh = [
                {**item, "project_id": project_id, "project_ids": [project_id], "project_links_authoritative": True}
                for item in items if item.get("from_me") or str(item.get("source_message_id")) not in seen
            ]
            incidents = [item for item in fresh if _django_failure(item)]
            classified = []
            for incident in incidents:
                conversation = proposals._group_conversations([incident])[0]
                classified.append(proposals._proposal_from_triage(conversation, project, {
                    "title": f"Investigate {conversation['label'].removeprefix('[Django]').strip()}",
                    "description": "The linked Django alert reports a failed operation. Use the original alert and traceback to investigate.",
                    "tasks": ["Confirm the failed operation and its impact.", "Trace the exception and identify the likely cause.", "Verify the appropriate fix or recovery on the affected workflow."],
                    "priority": "medium",
                }))
            other = [item for item in fresh if item not in incidents]
            if any(not item.get("from_me") for item in other):
                try:
                    classified += proposals._triage_conversations(other, settings, strict=True)
                except Exception:
                    failed = True
                    logger.warning("Could not classify linked %s mail", source, exc_info=True)
            scoped = proposals._scoped_proposals(classified)
            proposals._record_local_proposals(scoped)
            updated += len(scoped)
        except Exception:
            failed = True
            logger.warning("Could not check a linked %s conversation", source, exc_info=True)
    return updated, failed
