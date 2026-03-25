"""
Google Workspace Tool for LangChain

This tool provides comprehensive access to Google Workspace services including:
- Gmail (check inbox, read, send, draft, reply, delete emails)
- Google Drive (list folders, read files, upload files, read PDFs)
- Google Calendar (create events, read events, check schedule)
- Google Docs (create from markdown)
"""

import logging
from typing import Optional, Dict, Any
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector

logger = logging.getLogger(__name__)


class GoogleWorkspaceInput(BaseModel):
    """Input schema for Google Workspace tool."""
    action: str = Field(description="The action to perform. Options: 'check_inbox', 'read_email', 'send_email', 'draft_email', 'list_drafts', 'get_draft', 'list_emails_by_type', 'reply_email', 'delete_email', 'list_drive_folders', 'list_drive_files', 'read_drive_file', 'upload_to_drive', 'read_pdf', 'create_calendar_event', 'get_calendar_events', 'get_schedule_tomorrow', 'get_schedule_this_week', 'create_doc_from_markdown'")
    params: Optional[Dict[str, Any]] = Field(default=None, description="Parameters for the action. For 'draft_email': {'to': 'email@example.com', 'subject': 'Subject text', 'body': 'Email body text'}. For 'send_email': same as draft_email plus optional 'cc' and 'bcc'. For 'check_inbox': optional 'max_results' (default 10) and 'query' (default 'in:inbox'). For other actions, see tool description.")


class GoogleWorkspaceTool(BaseTool):
    """Tool for interacting with Google Workspace services."""
    
    name: str = "google_workspace"
    description: str = (
        "EMAIL GMAIL INBOX - Use this tool for ALL email/Gmail operations when Google is connected.\n"
        "GOOGLE WORKSPACE TOOL - PRIMARY tool for ALL Google services when Google is connected.\n"
        "\n"
        "CRITICAL: If Google is connected, you MUST use this tool for ALL Google-related tasks.\n"
        "DO NOT USE RUBE FOR EMAIL/GMAIL WHEN GOOGLE IS CONNECTED - USE THIS TOOL INSTEAD.\n"
        "\n"
        "EMAIL = GMAIL: When user says 'email', they ALWAYS mean Gmail. Use this tool, NOT Rube.\n"
        "\n"
        "This tool takes ABSOLUTE PRIORITY over Rube for:\n"
        "- Gmail / Email operations (CRITICAL: 'email' = Gmail, ALWAYS use this tool for email when Google is connected, NEVER use Rube)\n"
        "  * Check inbox, read emails, send emails, draft emails, reply to emails, delete emails\n"
        "- Google Calendar (create events, read events, check schedule)\n"
        "- Google Drive (list folders, read files, upload files, read PDFs)\n"
        "- Google Docs (create from markdown)\n"
        "- ANY Google Workspace product or service\n"
        "\n"
        "CRITICAL RULE: When user says 'email', 'check email', 'send email', 'read email', 'inbox' - they mean Gmail.\n"
        "If Google is connected, ALWAYS use this tool for email/Gmail - NEVER use Rube.\n"
        "Rube is FORBIDDEN for email/Gmail when Google is connected.\n"
        "\n"
        "AVAILABLE ACTIONS:\n"
        "\n"
        "GMAIL:\n"
        "- 'check_inbox': Check Gmail inbox - shows all inbox emails by default (params: max_results, query). Default query is 'in:inbox' to show all emails. Use 'is:unread' to show only unread emails.\n"
        "- 'read_email': Read email by ID (params: message_id)\n"
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
        "- 'create_calendar_event': Create event (params: summary, start_time, end_time, description, location)\n"
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
        "REMEMBER: This tool has ABSOLUTE PRIORITY over Rube for ALL Google services (including email/Gmail) when Google is connected.\n"
        "Always check if Google is connected first, and if so, use this tool instead of Rube."
    )
    args_schema: type[BaseModel] = GoogleWorkspaceInput
    
    def __init__(self):
        super().__init__()
        # Use object.__setattr__ to bypass Pydantic validation for non-field attributes
        object.__setattr__(self, 'connector', GoogleWorkspaceConnector())
    
    def _run(self, action: str, params: Optional[Dict[str, Any]] = None, **kwargs) -> str:
        """Execute Google Workspace action"""
        # Check connection status first
        if not self.connector.is_connected():
            return "Error: Google is not connected. Please connect your Google account in Settings > Advanced. You can use Rube tool as fallback if needed."
        
        # Handle params - it might be a string (JSON) or dict
        # LangChain tools receive params from JSON, which might be passed incorrectly
        if params is None:
            params = {}
        elif isinstance(params, str):
            # Try to parse as JSON
            try:
                import json
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
                    result += f"ID: {msg.get('id', '')}\n\n"
                return result
            
            elif action == 'read_email':
                message_id = params.get('message_id')
                if not message_id:
                    return "Error: message_id is required"
                email = self.connector.get_email(message_id)
                if not email:
                    return "Error: Could not retrieve email"
                result = f"From: {email.get('from', 'Unknown')}\n"
                result += f"To: {email.get('to', 'Unknown')}\n"
                result += f"Subject: {email.get('subject', 'No Subject')}\n"
                result += f"Date: {email.get('date', '')}\n\n"
                result += f"Body:\n{email.get('body', '')}\n"
                return result
            
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
                    result += f"ID: {msg.get('id', '')}\n\n"
                return result
            
            elif action == 'reply_email':
                message_id = params.get('message_id')
                body = params.get('body', '')
                body_type = params.get('body_type', 'plain')
                
                if not message_id:
                    return "Error: message_id is required"
                
                success = self.connector.reply_to_email(message_id, body, body_type)
                return "Reply sent successfully" if success else "Error: Failed to send reply"
            
            elif action == 'delete_email':
                message_id = params.get('message_id')
                if not message_id:
                    return "Error: message_id is required"
                
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
                return f"Event created successfully (ID: {event_id})" if event_id else "Error: Failed to create event"
            
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
                return f"Error: Unknown action '{action}'. Available actions: check_inbox, read_email, send_email, draft_email, list_drafts, get_draft, list_emails_by_type, reply_email, delete_email, list_drive_folders, list_drive_files, read_drive_file, upload_to_drive, read_pdf, create_calendar_event, get_calendar_events, get_schedule_tomorrow, get_schedule_this_week, create_doc_from_markdown"
        
        except Exception as e:
            logger.error(f"Error executing Google Workspace action: {e}", exc_info=True)
            return f"Error: {str(e)}"
    
    async def _arun(self, action: str, params: Optional[Dict[str, Any]] = None, **kwargs) -> str:
        """Async run method"""
        return self._run(action, params, **kwargs)

