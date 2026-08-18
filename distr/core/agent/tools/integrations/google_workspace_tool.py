"""
Google Workspace Tool for LangChain

This tool provides comprehensive access to Google Workspace services including:
- Gmail (check inbox, read, send, draft, reply, delete emails)
- Google Drive (list folders, read files, upload files, read PDFs)
- Google Calendar (create events, read events, check schedule)
- Google Docs (create from markdown)
"""

import json
import logging
import os
import re
from typing import Optional, Dict, Any

from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector
from distr.core.agent.tools.base import LazyToolMixin

logger = logging.getLogger(__name__)


def _normalize_calendar_events_raw(params: Dict[str, Any]) -> Any:
    """Resolve batch events from params, alternate keys, JSON string, or nested params.params."""
    raw = params.get("events")
    if raw in (None, ""):
        raw = params.get("calendar_events") or params.get("calendarEvents")
    inner = params.get("params")
    if raw in (None, "") and isinstance(inner, dict):
        raw = inner.get("events") or inner.get("calendar_events")
    if isinstance(raw, str):
        t = raw.strip()
        if not t:
            return None
        try:
            raw = json.loads(t)
        except (json.JSONDecodeError, ValueError):
            return None
    return raw


def _resolve_google_workspace_action(
    action: Optional[str],
    params: Dict[str, Any],
    kwargs: Dict[str, Any],
    events: Optional[Any] = None,
) -> Optional[str]:
    """Infer action when models omit it or flatten params at the top level."""
    for candidate in (
        action,
        kwargs.get("action"),
        kwargs.get("operation"),
        params.get("action"),
        params.get("operation"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    merged = dict(params or {})
    for key, value in (kwargs or {}).items():
        if key not in ("action", "params", "last_user_message", "is_telegram_request", "events"):
            merged.setdefault(key, value)

    batch_events = _normalize_calendar_events_raw({**merged, "events": events})
    if batch_events:
        return "create_calendar_events_batch"

    if merged.get("summary") and merged.get("start_time") and merged.get("end_time"):
        return "create_calendar_event"

    if merged.get("message_id"):
        if merged.get("attachment_id") or merged.get("filename"):
            return "download_email_attachment"
        if merged.get("destination_dir") and not merged.get("attachment_id") and not merged.get("filename"):
            return "download_email_attachments"
        return "read_email"

    if merged.get("to") and merged.get("body"):
        return "send_email"

    if merged.get("draft_id"):
        return "get_draft"

    if merged.get("email_type"):
        return "list_emails_by_type"

    if merged.get("file_path"):
        return "upload_to_drive"

    if merged.get("file_id"):
        if (merged.get("mime_type") or "").lower() == "application/pdf":
            return "read_pdf"
        return "read_drive_file"

    if merged.get("title") and merged.get("markdown_content"):
        return "create_doc_from_markdown"

    if merged.get("time_min") or merged.get("time_max"):
        return "get_calendar_events"

    if merged.get("query") is not None or merged.get("max_results") is not None:
        return "check_inbox"

    return None


class GoogleWorkspaceInput(BaseModel):
    """Input schema for Google Workspace tool."""

    action: str = Field(
        description="The action to perform. Options: 'check_inbox', 'read_email', 'get_email', 'send_email', 'draft_email', 'list_drafts', 'get_draft', 'list_emails_by_type', 'reply_email', 'delete_email', 'download_email_attachment', 'download_email_attachments', 'list_drive_folders', 'list_drive_files', 'read_drive_file', 'upload_to_drive', 'read_pdf', 'create_calendar_event', 'delete_calendar_event', 'create_calendar_events_batch', 'get_calendar_events', 'get_schedule_tomorrow', 'get_schedule_this_week', 'create_doc_from_markdown'"
    )
    params: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Parameters for the action. For 'draft_email': {'to': 'email@example.com', 'subject': 'Subject text', 'body': 'Email body text'}. For 'send_email': same as draft_email plus optional 'cc' and 'bcc'. For 'check_inbox': optional 'max_results' (default 10) and 'query' (default 'in:inbox'). For 'create_calendar_events_batch', you may set params.events OR use the top-level 'events' field (recommended for large lists).",
    )
    # Declared so OpenAI-style tool JSON can pass events at top level; unknown extra keys are dropped by Pydantic.
    events: Optional[Any] = Field(
        default=None,
        description=(
            "Only for action='create_calendar_events_batch': non-empty list of objects, each with "
            "summary, start_time, end_time (ISO 8601 strings), optional description and location. "
            "Prefer passing here instead of params.events so the full list is not stripped as an 'unknown' field."
        ),
    )


class GoogleWorkspaceTool(LazyToolMixin, BaseTool):
    """Tool for interacting with Google Workspace services."""
    
    name: str = "google_workspace"
    description: str = (
        "EMAIL GMAIL INBOX - Use this tool for ALL email/Gmail operations when Google is connected.\n"
        "GOOGLE WORKSPACE TOOL - PRIMARY tool for ALL Google services when Google is connected.\n"
        "\n"
        "CRITICAL: If Google is connected, you MUST use this tool for ALL Google-related tasks.\n"
        "\n"
        "EMAIL = GMAIL: When user says 'email', they ALWAYS mean Gmail. Use this tool.\n"
        "\n"
        "This tool handles:\n"
        "- Gmail / Email operations (CRITICAL: 'email' = Gmail, ALWAYS use this tool for email when Google is connected)\n"
        "  * Check inbox, read emails, send emails, draft emails, reply to emails, delete emails\n"
        "- Google Calendar (create events, read events, check schedule)\n"
        "- Google Drive (list folders, read files, upload files, read PDFs)\n"
        "- Google Docs (create from markdown)\n"
        "- ANY Google Workspace product or service\n"
        "\n"
        "Use this tool when the user explicitly says Gmail or Google Workspace. For email without a named provider, prefer a project-linked source; configured Tensology Mail is handled by TensologyWorkspaceTool.\n"
        "If Google is connected, ALWAYS use this tool for email/Gmail.\n"
        "\n"
        "AVAILABLE ACTIONS:\n"
        "\n"
        "GMAIL:\n"
                "- 'check_inbox': Check Gmail inbox - shows all inbox emails by default (params: max_results, query). Default query is 'in:inbox' to show all emails. Use 'is:unread' to show only unread emails. Each email includes a Message ID — use this ID with read_email.\n"
        "- 'read_email' or 'get_email': Read full email by Message ID from check_inbox. Includes attachment list with attachment_id values. (params: message_id)\n"
        "- 'download_email_attachment': Download one Gmail attachment to disk (default ~/Downloads). (params: message_id, attachment_id, filename; or message_id + filename to match). Optional destination_dir.\n"
        "- 'download_email_attachments': Download every attachment on an email to disk (default ~/Downloads). (params: message_id, optional destination_dir)\n"
        "- 'send_email': Send email (params: to, subject, body, body_type, cc, bcc)\n"
        "- 'draft_email': Create draft email in Gmail (params: to (required), subject (optional), body (required), body_type (optional, default='plain')). Example: action='draft_email', params={'to': 'bob@bob.com', 'subject': 'Thank you', 'body': 'Thank you for the pineapples.'}\n"
        "- 'list_drafts': List all draft emails (params: max_results, default=10). Returns list of drafts with details including draft_id.\n"
        "- 'get_draft': Get a specific draft by draft_id (params: draft_id). Returns full draft details including body.\n"
        "- 'list_emails_by_type': List emails by type (params: email_type, max_results). Types: 'sent', 'drafts', 'starred', 'important', 'unread', 'read', 'trash', 'spam'.\n"
        "- 'reply_email': Reply to email (params: message_id, body, body_type)\n"
        "- 'delete_email': Delete email (params: message_id)\n"
        "\n"
        "GOOGLE DRIVE:\n"
        "- 'list_drive_folders': List folders (params: folder_id, default='root')\n"
        "- 'list_drive_files': List files (params: folder_id, mime_type)\n"
        "- 'read_drive_file': Read file content (params: file_id)\n"
        "- 'upload_to_drive': Upload file (params: file_path, folder_id, name)\n"
        "- 'read_pdf': Read PDF from Drive (params: file_id)\n"
        "\n"
        "GOOGLE CALENDAR:\n"
        "- 'create_calendar_event': Create ONE event (params: summary, start_time, end_time, description, location)\n"
        "- 'delete_calendar_event': Delete ONE event using the event_id returned by create_calendar_event (params: event_id)\n"
        "- 'create_calendar_events_batch': Create MANY events in ONE tool call. "
        "You MUST include a non-empty JSON array named events (top-level next to action is best). "
        "Shape: {\"action\":\"create_calendar_events_batch\",\"events\":[{\"summary\":\"...\",\"start_time\":\"2026-05-05T08:00:00\",\"end_time\":\"2026-05-05T08:45:00\",\"description\":\"optional\"}, ...]}. "
        "Alternatively params.events with the same array. Max 500 per call; split by week if larger. "
        "REQUIRED for multi-day protocols — do not call batch without the events array, and do not stop after one single event.\n"
        "- 'get_calendar_events': Get events (params: time_min, time_max, max_results)\n"
        "- 'get_schedule_tomorrow': Get tomorrow's schedule (no params)\n"
        "- 'get_schedule_this_week': Get this week's schedule (no params)\n"
        "\n"
        "GOOGLE DOCS:\n"
        "- 'create_doc_from_markdown': Create Doc from markdown (params: title, markdown_content, folder_id)\n"
        "\n"
        "EXAMPLES (email = Gmail):\n"
        "- 'check my email' or 'check email' -> action='check_inbox' (shows all inbox emails)\n"
        "- 'check my inbox' or 'check inbox' -> action='check_inbox' (shows all inbox emails)\n"
        "- 'check my unread inbox' or 'check unread email' or 'check new email' -> action='check_inbox', params={'query': 'is:unread'} (shows only unread emails)\n"
        "- 'read my emails' or 'read emails' -> action='check_inbox' (shows all inbox emails)\n"
        "- 'read the latest email from Alice' -> action='check_inbox', params={'query': 'in:inbox from:\"Alice\"', 'max_results': 1}, then read_email with the returned Message ID\n"
        "- 'download the attachment from that email' -> action='read_email' with message_id to list attachments, then action='download_email_attachment' or action='download_email_attachments'\n"
        "- 'send email' or 'send an email' -> action='send_email' (email = Gmail)\n"
        "- 'send email to john@example.com' -> action='send_email', params={'to': 'john@example.com', 'subject': '...', 'body': '...'}\n"
        "- 'create a draft' or 'draft an email' or 'create draft email' -> action='draft_email', params={'to': '...', 'subject': '...', 'body': '...'}\n"
        "- 'create a draft to bob@bob.com about pineapples' -> action='draft_email', params={'to': 'bob@bob.com', 'subject': 'Thank you', 'body': 'Thank you for the pineapples.'}\n"
        "- 'list my drafts' or 'show my drafts' or 'get my drafts' -> action='list_drafts'\n"
        "- 'get the last draft' or 'open the last draft' -> action='list_drafts', params={'max_results': 1}, then use 'get_draft' with the draft_id\n"
        "- 'show my sent emails' -> action='list_emails_by_type', params={'email_type': 'sent'}\n"
        "- 'show my starred emails' -> action='list_emails_by_type', params={'email_type': 'starred'}\n"
        "- 'what's on my schedule tomorrow' -> action='get_schedule_tomorrow'\n"
        "- 'what's on my schedule this week' -> action='get_schedule_this_week'\n"
        "- 'create a google doc from this markdown' -> action='create_doc_from_markdown', params={'title': '...', 'markdown_content': '...'}\n"
        "- 'list my drive folders' -> action='list_drive_folders'\n"
        "- 'upload file to drive' -> action='upload_to_drive', params={'file_path': '/path/to/file'}\n"
        "\n"
        "CRITICAL: When user says 'email', they mean Gmail. Always use this tool for email when Google is connected.\n"
        "REMEMBER: This tool has ABSOLUTE PRIORITY for ALL Google services (including email/Gmail) when Google is connected.\n"
        "Always check if Google is connected first, and if so, use this tool."
    )
    args_schema: type[BaseModel] = GoogleWorkspaceInput
    
    def __init__(self):
        super().__init__()

    def _lazy_init(self):
        object.__setattr__(self, 'connector', GoogleWorkspaceConnector())

    def normalize_tool_args(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Canonicalize model arguments before Pydantic validates the tool call."""
        normalized = dict(arguments or {})
        raw_params = normalized.get("params")
        if isinstance(raw_params, str):
            try:
                parsed = json.loads(raw_params)
            except (json.JSONDecodeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                normalized["params"] = parsed

        params = normalized.get("params")
        if not isinstance(params, dict):
            params = {}
            normalized["params"] = params

        action = _resolve_google_workspace_action(
            normalized.get("action"),
            params,
            normalized,
            normalized.get("events"),
        )
        if action:
            normalized["action"] = action

        from distr.core.human_engagement import remote_user_reply_text

        user_text = remote_user_reply_text(normalized.get("last_user_message", ""))
        sender_match = re.search(
            r"(?i)\b(?:latest|last|newest)?\s*(?:email|message|one)?\s*from\s+"
            r"([^\n,.!?]+)",
            user_text,
        )
        if action == "check_inbox" and sender_match:
            sender_hint = re.sub(r"\s+", " ", sender_match.group(1)).strip(" '\"")
            if sender_hint:
                query = str(params.get("query") or "in:inbox").strip()
                if "from:" not in query.lower():
                    params["query"] = f'{query} from:"{sender_hint}"'.strip()
                if re.search(r"(?i)\b(latest|last|newest)\b", user_text):
                    params["max_results"] = 1
        return normalized

    @staticmethod
    def _default_downloads_dir() -> str:
        return os.path.expanduser("~/Downloads")

    @staticmethod
    def _format_attachments_section(attachments: list) -> str:
        if not attachments:
            return "Attachments: none\n"
        lines = ["Attachments:"]
        for item in attachments:
            lines.append(
                f"  - {item.get('filename', 'attachment')} "
                f"(attachment_id={item.get('attachment_id', '')}, "
                f"mime_type={item.get('mime_type', '')}, size={item.get('size', 0)})"
            )
        lines.append(
            "Use download_email_attachment or download_email_attachments with the Message ID and attachment_id."
        )
        return "\n".join(lines) + "\n"

    def _resolve_email_attachment(
        self,
        message_id: str,
        attachment_id: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> tuple[Optional[dict], Optional[str]]:
        """Resolve a single attachment record from message_id + id or filename."""
        if attachment_id and filename:
            return (
                {
                    "message_id": message_id,
                    "attachment_id": attachment_id,
                    "filename": filename,
                },
                None,
            )

        email = self.connector.get_email(message_id)
        if not email:
            return None, "Error: Could not retrieve email"

        attachments = email.get("attachments") or []
        if not attachments:
            return None, "No attachments found on this email."

        if attachment_id:
            for item in attachments:
                if item.get("attachment_id") == attachment_id:
                    return item, None
            return None, f"Error: No attachment with attachment_id={attachment_id!r} on this email."

        if filename:
            needle = filename.strip().lower()
            matches = [
                item for item in attachments
                if (item.get("filename") or "").strip().lower() == needle
            ]
            if len(matches) == 1:
                return matches[0], None
            if len(matches) > 1:
                return None, "Error: Multiple attachments match that filename. Use attachment_id."
            return None, f"Error: No attachment named {filename!r} on this email."

        if len(attachments) == 1:
            return attachments[0], None

        names = ", ".join(item.get("filename", "attachment") for item in attachments)
        return None, (
            "Error: This email has multiple attachments. "
            f"Specify attachment_id or filename. Available: {names}"
        )
    
    def _run(
        self,
        action: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        events: Optional[Any] = None,
        **kwargs,
    ) -> str:
        """Execute Google Workspace action"""
        self._ensure_initialized()
        # Check connection status first
        if not self.connector.is_connected():
            return "Error: Google is not connected. Please connect your Google account in Settings > Advanced."
        
        # Handle params - it might be a string (JSON) or dict
        # LangChain tools receive params from JSON, which might be passed incorrectly
        if params is None:
            params = {}
        elif isinstance(params, str):
            # Try to parse as JSON
            try:
                params = json.loads(params)
                # Ensure it's still a dict after parsing
                if not isinstance(params, dict):
                    params = {}
            except (json.JSONDecodeError, ValueError):
                # If it's not valid JSON, treat it as a single value
                params = {'query': params} if action == 'check_inbox' else {}
        elif not isinstance(params, dict):
            # Convert other types to dict if possible
            # Log warning for debugging
            logger.warning(f"GoogleWorkspaceTool: params is not a dict, got {type(params)}: {params}")
            params = {}
        
        # Ensure params is always a dict before using .get()
        if not isinstance(params, dict):
            params = {}

        # LLMs often flatten nested params — merge any top-level kwargs into params
        # e.g. LLM passes {action: "read_email", message_id: "abc"} instead of
        #      {action: "read_email", params: {message_id: "abc"}}
        KNOWN_PARAM_KEYS = {
            'message_id', 'to', 'subject', 'body', 'body_type', 'cc', 'bcc',
            'draft_id', 'email_type', 'max_results', 'query', 'file_path',
            'folder_id', 'file_id', 'name', 'mime_type', 'summary',
            'event_id',
            'start_time', 'end_time', 'description', 'location', 'time_min',
            'time_max', 'title', 'markdown_content', 'convert_to_google_doc',
            'events', 'calendar_events', 'calendarEvents',
            'attachment_id', 'filename', 'destination_dir',
        }
        for key in KNOWN_PARAM_KEYS:
            if key in kwargs and key not in params:
                params[key] = kwargs[key]
        # Also merge any unknown kwargs that aren't 'action' or 'params'
        for key, val in kwargs.items():
            if key not in ('action', 'params', 'last_user_message') and key not in params:
                params[key] = val

        # Batch calendar: models often pass `events` at top level (must be on args_schema or Pydantic drops it).
        if events is None and kwargs.get("events") is not None:
            events = kwargs.get("events")
        if events is not None:
            pe = params.get("events")
            if pe in (None, [], ""):
                params["events"] = events

        resolved_action = _resolve_google_workspace_action(action, params, kwargs, events)
        if not resolved_action:
            logger.warning(
                "GoogleWorkspaceTool: missing action (params_keys=%s kwargs_keys=%s)",
                list(params.keys()),
                list(kwargs.keys()),
            )
            return (
                "Error: action is required. For a single calendar entry use "
                "action='create_calendar_event' with summary, start_time, and end_time "
                "(ISO 8601). Example: "
                '{"action":"create_calendar_event","params":{"summary":"Visit Louis",'
                '"start_time":"2026-06-20T13:00:00","end_time":"2026-06-20T14:00:00"}}'
            )
        action = resolved_action

        try:
            # Gmail actions
            if action == 'check_inbox':
                max_results = params.get('max_results', 10)
                query = params.get('query')
                
                # If query is provided as a string (from LLM), check for unread/new keywords
                if query is None:
                    # Default to showing all inbox emails (read and unread)
                    query = 'in:inbox'
                elif isinstance(query, str):
                    query_lower = query.lower()
                    # If user asks for unread/new emails, use is:unread filter
                    if any(keyword in query_lower for keyword in ['unread', 'new', 'unread inbox', 'new email', 'new emails']):
                        query = 'is:unread'
                    # If query doesn't look like a Gmail search query, treat it as a search term
                    elif not query.startswith('in:') and not query.startswith('is:'):
                        # Treat as a search query (search in subject/body)
                        query = f'in:inbox {query}'
                
                messages = self.connector.check_inbox(max_results=max_results, query=query)
                if messages is None:
                    return "Error: Gmail API is not enabled. Please enable Gmail API in Google Cloud Console at https://console.cloud.google.com/apis/library/gmail.googleapis.com"
                if not messages:
                    return "No emails found matching the query."
                result = f"Found {len(messages)} email(s):\n\n"
                for msg in messages:
                    result += f"From: {msg.get('from', 'Unknown')}\n"
                    result += f"Subject: {msg.get('subject', 'No Subject')}\n"
                    result += f"Snippet: {msg.get('snippet', '')}\n"
                    result += f"Date: {msg.get('date', '')}\n"
                    result += f"Message ID: {msg.get('id', '')}\n\n"
                return result
            
            elif action in ('read_email', 'get_email'):
                message_id = params.get('message_id') or params.get('id') or params.get('email_id')
                if not message_id:
                    logger.warning(f"read_email called without message_id. action={action}, params={params}, kwargs_keys={list(kwargs.keys())}")
                    return "Error: message_id is required. Please provide a message_id from the inbox listing."
                email = self.connector.get_email(message_id)
                if not email:
                    return "Error: Could not retrieve email"
                result = f"Message ID: {email.get('id', message_id)}\n"
                result += f"From: {email.get('from', 'Unknown')}\n"
                result += f"To: {email.get('to', 'Unknown')}\n"
                result += f"Subject: {email.get('subject', 'No Subject')}\n"
                result += f"Date: {email.get('date', '')}\n\n"
                result += self._format_attachments_section(email.get('attachments') or [])
                result += f"\nBody:\n{email.get('body', '')}\n"
                return result

            elif action == 'download_email_attachment':
                message_id = params.get('message_id') or params.get('id') or params.get('email_id')
                if not message_id:
                    return "Error: message_id is required."
                attachment_id = params.get('attachment_id')
                filename = params.get('filename')
                destination_dir = params.get('destination_dir') or self._default_downloads_dir()

                attachment, error = self._resolve_email_attachment(
                    message_id,
                    attachment_id=attachment_id,
                    filename=filename,
                )
                if error:
                    return error
                if not attachment:
                    return "Error: Could not resolve attachment."

                saved_path = self.connector.download_email_attachment(
                    message_id=message_id,
                    attachment_id=attachment.get('attachment_id', ''),
                    filename=attachment.get('filename', 'attachment'),
                    destination_dir=destination_dir,
                )
                if not saved_path:
                    return "Error: Failed to download attachment."
                return f"Saved attachment to {saved_path}"

            elif action == 'download_email_attachments':
                message_id = params.get('message_id') or params.get('id') or params.get('email_id')
                if not message_id:
                    return "Error: message_id is required."
                destination_dir = params.get('destination_dir') or self._default_downloads_dir()
                email = self.connector.get_email(message_id)
                if not email:
                    return "Error: Could not retrieve email"
                attachments = email.get('attachments') or []
                if not attachments:
                    return "No attachments found on this email."

                saved_paths = []
                for item in attachments:
                    saved_path = self.connector.download_email_attachment(
                        message_id=message_id,
                        attachment_id=item.get('attachment_id', ''),
                        filename=item.get('filename', 'attachment'),
                        destination_dir=destination_dir,
                    )
                    if saved_path:
                        saved_paths.append(saved_path)

                if not saved_paths:
                    return "Error: Failed to download attachments."
                if len(saved_paths) == 1:
                    return f"Saved attachment to {saved_paths[0]}"
                return "Saved attachments:\n" + "\n".join(f"- {path}" for path in saved_paths)
            
            elif action == 'send_email':
                to = params.get('to')
                subject = params.get('subject', '')
                body = params.get('body', '')
                body_type = params.get('body_type', 'plain')
                cc = params.get('cc')
                bcc = params.get('bcc')
                
                if not to:
                    return "Error: 'to' email address is required"
                
                success = self.connector.send_email(to, subject, body, body_type, cc, bcc)
                return "Email sent successfully" if success else "Error: Failed to send email"
            
            elif action == 'draft_email':
                to = params.get('to')
                subject = params.get('subject', '')
                body = params.get('body', '')
                body_type = params.get('body_type', 'plain')
                
                logger.info(f"Draft email requested: to={to}, subject={subject}, body_length={len(body) if body else 0}")
                
                if not to:
                    logger.warning("Draft email failed: 'to' email address is required")
                    return "Error: 'to' email address is required"
                
                if not body:
                    logger.warning("Draft email: body is empty, but proceeding anyway")
                
                draft_id = self.connector.draft_email(to, subject, body, body_type)
                if draft_id:
                    logger.info(f"Draft email created successfully: ID={draft_id}")
                    return f"Draft created successfully (ID: {draft_id})"
                else:
                    logger.error(f"Draft email failed: connector returned None")
                    return "Error: Failed to create draft. Check logs for details."
            
            elif action == 'list_drafts':
                max_results = params.get('max_results', 10)
                drafts = self.connector.list_drafts(max_results=max_results)
                if drafts is None:
                    return "Error: Gmail API is not enabled. Please enable Gmail API in Google Cloud Console at https://console.cloud.google.com/apis/library/gmail.googleapis.com"
                if not drafts:
                    return "No drafts found."
                result = f"Found {len(drafts)} draft(s):\n\n"
                for i, draft in enumerate(drafts, 1):
                    result += f"Draft {i}:\n"
                    result += f"  Draft ID: {draft.get('draft_id', 'Unknown')}\n"
                    result += f"  To: {draft.get('to', 'Unknown')}\n"
                    result += f"  Subject: {draft.get('subject', 'No Subject')}\n"
                    result += f"  Snippet: {draft.get('snippet', '')}\n"
                    result += f"  Date: {draft.get('date', '')}\n\n"
                return result
            
            elif action == 'get_draft':
                draft_id = params.get('draft_id')
                if not draft_id:
                    return "Error: 'draft_id' is required"
                draft = self.connector.get_draft(draft_id)
                if not draft:
                    return "Error: Could not retrieve draft or draft not found"
                result = f"Draft ID: {draft.get('draft_id', 'Unknown')}\n"
                result += f"From: {draft.get('from', 'Unknown')}\n"
                result += f"To: {draft.get('to', 'Unknown')}\n"
                result += f"Subject: {draft.get('subject', 'No Subject')}\n"
                result += f"Date: {draft.get('date', '')}\n\n"
                result += f"Body:\n{draft.get('body', '')}\n"
                return result
            
            elif action == 'list_emails_by_type':
                email_type = params.get('email_type', 'sent')
                max_results = params.get('max_results', 10)
                messages = self.connector.list_emails_by_type(email_type, max_results)
                if messages is None:
                    return "Error: Gmail API is not enabled. Please enable Gmail API in Google Cloud Console at https://console.cloud.google.com/apis/library/gmail.googleapis.com"
                if not messages:
                    return f"No {email_type} emails found."
                result = f"Found {len(messages)} {email_type} email(s):\n\n"
                for msg in messages:
                    result += f"From: {msg.get('from', 'Unknown')}\n"
                    result += f"Subject: {msg.get('subject', 'No Subject')}\n"
                    result += f"Snippet: {msg.get('snippet', '')}\n"
                    result += f"Date: {msg.get('date', '')}\n"
                    result += f"Message ID: {msg.get('id', '')}\n\n"
                return result
            
            elif action == 'reply_email':
                message_id = params.get('message_id') or params.get('id')
                body = params.get('body', '')
                body_type = params.get('body_type', 'plain')
                
                if not message_id:
                    return "Error: message_id is required. Please provide a message_id from the inbox listing."
                
                success = self.connector.reply_to_email(message_id, body, body_type)
                return "Reply sent successfully" if success else "Error: Failed to send reply"
            
            elif action == 'delete_email':
                message_id = params.get('message_id') or params.get('id')
                if not message_id:
                    return "Error: message_id is required. Please provide a message_id from the inbox listing."
                
                success = self.connector.delete_email(message_id)
                return "Email deleted successfully" if success else "Error: Failed to delete email"
            
            # Google Drive actions
            elif action == 'list_drive_folders':
                folder_id = params.get('folder_id', 'root')
                folders = self.connector.list_drive_folders(folder_id)
                if not folders:
                    return "No folders found."
                result = f"Found {len(folders)} folder(s):\n\n"
                for folder in folders:
                    result += f"Name: {folder.get('name', 'Unknown')}\n"
                    result += f"ID: {folder.get('id', '')}\n"
                    result += f"Modified: {folder.get('modifiedTime', '')}\n\n"
                return result
            
            elif action == 'list_drive_files':
                folder_id = params.get('folder_id', 'root')
                mime_type = params.get('mime_type')
                files = self.connector.list_drive_files(folder_id, mime_type)
                if not files:
                    return "No files found."
                result = f"Found {len(files)} file(s):\n\n"
                for file in files:
                    result += f"Name: {file.get('name', 'Unknown')}\n"
                    result += f"ID: {file.get('id', '')}\n"
                    result += f"Type: {file.get('mimeType', '')}\n"
                    result += f"Modified: {file.get('modifiedTime', '')}\n\n"
                return result
            
            elif action == 'read_drive_file':
                file_id = params.get('file_id')
                if not file_id:
                    return "Error: file_id is required"
                
                content = self.connector.read_drive_file(file_id)
                if content is None:
                    return "Error: Could not read file. Please ensure Google Drive API is enabled in Google Cloud Console at https://console.cloud.google.com/apis/library/drive.googleapis.com"
                return content if content else "Error: Could not read file"
            
            elif action == 'upload_to_drive':
                file_path = params.get('file_path')
                folder_id = params.get('folder_id', 'root')
                name = params.get('name')
                
                if not file_path:
                    return "Error: file_path is required"
                
                file_id = self.connector.upload_to_drive(file_path, folder_id, name)
                return f"File uploaded successfully (ID: {file_id})" if file_id else "Error: Failed to upload file"
            
            elif action == 'read_pdf':
                file_id = params.get('file_id')
                if not file_id:
                    return "Error: file_id is required"
                
                content = self.connector.read_pdf_from_drive(file_id)
                return content if content else "Error: Could not read PDF"
            
            # Google Calendar actions
            elif action == 'create_calendar_event':
                from datetime import datetime
                summary = params.get('summary')
                start_time_str = params.get('start_time')
                end_time_str = params.get('end_time')
                description = params.get('description')
                location = params.get('location')
                
                if not summary or not start_time_str or not end_time_str:
                    return "Error: summary, start_time, and end_time are required"
                
                try:
                    start_time = datetime.fromisoformat(start_time_str)
                    end_time = datetime.fromisoformat(end_time_str)
                except (ValueError, TypeError):
                    return "Error: Invalid date format. Use ISO format (YYYY-MM-DDTHH:MM:SS)"
                
                event_id = self.connector.create_calendar_event(summary, start_time, end_time, description, location)
                if event_id:
                    return f"Event created successfully (ID: {event_id})"
                connector_error = str(getattr(self.connector, "last_error", "") or "").strip()
                if connector_error:
                    return f"Error: {connector_error}"
                return "Error: Calendar API failed to create the event. Verify the event fields and connection."

            elif action == 'delete_calendar_event':
                event_id = str(params.get('event_id') or '').strip()
                if not event_id:
                    return "Error: event_id is required to delete a calendar event"
                deleted = self.connector.delete_calendar_event(event_id)
                if deleted:
                    return f"Calendar event deleted successfully (ID: {event_id})"
                connector_error = str(getattr(self.connector, "last_error", "") or "").strip()
                return f"Error: {connector_error or 'Failed to delete calendar event'}"

            elif action == 'create_calendar_events_batch':
                from datetime import datetime

                raw_events = _normalize_calendar_events_raw(params)
                if not raw_events or not isinstance(raw_events, list):
                    return (
                        "Error: No event list received. Pass a non-empty JSON array as top-level \"events\" "
                        "(sibling of \"action\", preferred for large protocols) or as params.events. "
                        "Each object needs summary, start_time, end_time (ISO strings); optional description, location. "
                        "More than 500 slots: split into multiple create_calendar_events_batch calls (e.g. per week)."
                    )
                max_batch = 500
                if len(raw_events) > max_batch:
                    return f"Error: at most {max_batch} events per batch; got {len(raw_events)}. Split into multiple calls."

                parsed: list[dict] = []
                parse_errors: list[str] = []

                def _parse_iso(s: Any) -> datetime | None:
                    if s is None:
                        return None
                    if isinstance(s, datetime):
                        return s
                    if not isinstance(s, str):
                        return None
                    t = s.strip()
                    if t.endswith("Z"):
                        t = t[:-1] + "+00:00"
                    try:
                        return datetime.fromisoformat(t)
                    except (ValueError, TypeError):
                        return None

                for idx, ev in enumerate(raw_events):
                    if not isinstance(ev, dict):
                        parse_errors.append(f"[{idx}] not an object")
                        continue
                    summary = ev.get("summary")
                    st = _parse_iso(ev.get("start_time"))
                    et = _parse_iso(ev.get("end_time"))
                    if not summary or st is None or et is None:
                        parse_errors.append(
                            f"[{idx}] need summary and valid ISO start_time/end_time; got summary={summary!r}"
                        )
                        continue
                    parsed.append(
                        {
                            "_batch_index": idx,
                            "summary": summary,
                            "start_time": st,
                            "end_time": et,
                            "description": ev.get("description"),
                            "location": ev.get("location"),
                        }
                    )

                if parse_errors and not parsed:
                    return "Error: no valid events to create.\n" + "\n".join(parse_errors)

                rows = self.connector.create_calendar_events_batch(parsed)
                connector_error = str(getattr(self.connector, "last_error", "") or "").strip()
                if rows and not any(r.get("event_id") for r in rows) and connector_error:
                    return f"Error: {connector_error}"
                ok = sum(1 for r in rows if r.get("event_id"))
                fail = len(rows) - ok
                lines = [
                    f"Batch calendar: created {ok} event(s), failed {fail}, input rows {len(raw_events)}.",
                ]
                if parse_errors:
                    lines.append("Skipped invalid input rows:")
                    lines.extend(parse_errors)
                for r in rows:
                    sid = r.get("summary") or ""
                    if r.get("event_id"):
                        lines.append(f"  [{r['index']}] OK id={r['event_id']} — {sid[:80]}")
                    else:
                        lines.append(
                            f"  [{r['index']}] FAILED — {sid[:80]} — {r.get('error', 'unknown')}"
                        )
                return "\n".join(lines)

            elif action == 'get_calendar_events':
                from datetime import datetime
                time_min_str = params.get('time_min')
                time_max_str = params.get('time_max')
                max_results = params.get('max_results', 10)
                
                time_min = None
                time_max = None
                
                if time_min_str:
                    try:
                        time_min = datetime.fromisoformat(time_min_str)
                    except (ValueError, TypeError):
                        return "Error: Invalid time_min format"
                
                if time_max_str:
                    try:
                        time_max = datetime.fromisoformat(time_max_str)
                    except (ValueError, TypeError):
                        return "Error: Invalid time_max format"
                
                events = self.connector.get_calendar_events(time_min, time_max, max_results)
                if events is None:
                    return "Error: Could not retrieve calendar events. Please ensure Google Calendar API is enabled in Google Cloud Console at https://console.cloud.google.com/apis/library/calendar-json.googleapis.com"
                if not events:
                    return "No events found."
                
                result = f"Found {len(events)} event(s):\n\n"
                for event in events:
                    result += f"Summary: {event.get('summary', 'No Title')}\n"
                    start = event.get('start', {}).get('dateTime', event.get('start', {}).get('date', ''))
                    end = event.get('end', {}).get('dateTime', event.get('end', {}).get('date', ''))
                    result += f"Start: {start}\n"
                    result += f"End: {end}\n"
                    if event.get('description'):
                        result += f"Description: {event.get('description')}\n"
                    if event.get('location'):
                        result += f"Location: {event.get('location')}\n"
                    result += "\n"
                return result
            
            elif action == 'get_schedule_tomorrow':
                events = self.connector.get_schedule_tomorrow()
                if not events:
                    return "No events scheduled for tomorrow."
                
                result = f"Tomorrow's schedule ({len(events)} event(s)):\n\n"
                for event in events:
                    result += f"Summary: {event.get('summary', 'No Title')}\n"
                    start = event.get('start', {}).get('dateTime', event.get('start', {}).get('date', ''))
                    end = event.get('end', {}).get('dateTime', event.get('end', {}).get('date', ''))
                    result += f"Time: {start} - {end}\n"
                    if event.get('description'):
                        result += f"Description: {event.get('description')}\n"
                    result += "\n"
                return result
            
            elif action == 'get_schedule_this_week':
                events = self.connector.get_schedule_this_week()
                if events is None:
                    return "Error: Could not retrieve schedule. Please ensure Google Calendar API is enabled in Google Cloud Console at https://console.cloud.google.com/apis/library/calendar-json.googleapis.com"
                if not events:
                    return "No events scheduled for this week."
                
                result = f"This week's schedule ({len(events)} event(s)):\n\n"
                for event in events:
                    result += f"Summary: {event.get('summary', 'No Title')}\n"
                    start = event.get('start', {}).get('dateTime', event.get('start', {}).get('date', ''))
                    end = event.get('end', {}).get('dateTime', event.get('end', {}).get('date', ''))
                    result += f"Time: {start} - {end}\n"
                    if event.get('description'):
                        result += f"Description: {event.get('description')}\n"
                    result += "\n"
                return result
            
            # Google Docs actions
            elif action == 'create_doc_from_markdown':
                title = params.get('title')
                markdown_content = params.get('markdown_content')
                folder_id = params.get('folder_id', 'root')
                
                if not title or not markdown_content:
                    return "Error: title and markdown_content are required"
                
                doc_id = self.connector.create_doc_from_markdown(title, markdown_content, folder_id)
                return f"Document created successfully (ID: {doc_id})" if doc_id else "Error: Failed to create document"
            
            else:
                return f"Error: Unknown action '{action}'. Available actions: check_inbox, read_email, get_email, send_email, draft_email, list_drafts, get_draft, list_emails_by_type, reply_email, delete_email, download_email_attachment, download_email_attachments, list_drive_folders, list_drive_files, read_drive_file, upload_to_drive, read_pdf, create_calendar_event, delete_calendar_event, create_calendar_events_batch, get_calendar_events, get_schedule_tomorrow, get_schedule_this_week, create_doc_from_markdown"
        
        except Exception as e:
            logger.error(f"Error executing Google Workspace action: {e}", exc_info=True)
            return f"Error: {str(e)}"
    
    async def _arun(
        self,
        action: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        events: Optional[Any] = None,
        **kwargs,
    ) -> str:
        """Async run method"""
        self._ensure_initialized()
        return self._run(action, params, events, **kwargs)
