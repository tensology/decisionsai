"""
Google Workspace Connector Service

This service provides comprehensive access to Google Workspace APIs including:
- Gmail (read, send, draft, delete emails)
- Google Drive (list folders, read files, upload files, read PDFs)
- Google Calendar (create events, read events, check schedule)
- Google Docs (create from markdown, convert markdown)
- Google Sheets and Slides
"""

import logging
import json
import os
import io
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from pathlib import Path
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import base64
import re

logger = logging.getLogger(__name__)


def _walk_gmail_parts(part: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten Gmail MIME parts, including nested multipart children."""
    parts: List[Dict[str, Any]] = []
    stack = list(part.get('parts') or [])
    while stack:
        current = stack.pop(0)
        parts.append(current)
        children = current.get('parts') or []
        if children:
            stack[0:0] = children
    return parts


def _safe_attachment_filename(filename: str) -> str:
    """Return a filesystem-safe attachment filename while preserving extension."""
    name = os.path.basename(filename or "").strip() or "attachment"
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    name = name.strip(" .")
    return name or "attachment"


class GoogleWorkspaceConnector:
    """Comprehensive Google Workspace API connector"""
    
    def __init__(self):
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_expires_at: Optional[datetime] = None
        self.client_id: Optional[str] = None
        self.client_secret: Optional[str] = None
        self._load_credentials()
    
    def _load_credentials(self) -> bool:
        """Load OAuth credentials and tokens from database"""
        try:
            from distr.core.settings import load_settings_from_db
            from distr.gui.web.oauth import load_google_oauth_config
            
            # Load OAuth config
            oauth_config = load_google_oauth_config()
            if not oauth_config:
                logger.debug("Google OAuth config not found")
                return False
            
            web_config = oauth_config.get('web', {})
            self.client_id = web_config.get('client_id')
            self.client_secret = web_config.get('client_secret')
            
            if not self.client_id or not self.client_secret:
                logger.warning("Google OAuth client credentials not found")
                return False
            
            # Load tokens from database
            settings = load_settings_from_db()
            connected_accounts = []
            
            if settings.get('connected_accounts'):
                try:
                    accounts_data = settings.get('connected_accounts', '[]')
                    if isinstance(accounts_data, str):
                        parsed = json.loads(accounts_data)
                    else:
                        parsed = accounts_data
                    
                    if isinstance(parsed, list):
                        connected_accounts = parsed
                    elif isinstance(parsed, dict):
                        connected_accounts = [parsed]
                except Exception as e:
                    logger.warning(f"Failed to parse connected_accounts: {e}")
                    return False
            
            # Find Google account
            google_account = None
            for account in connected_accounts:
                if isinstance(account, dict) and account.get('provider') == 'google':
                    google_account = account
                    break
            
            if not google_account or not google_account.get('access_token'):
                logger.warning("Google account not connected or no access token")
                return False
            
            self.access_token = google_account.get('access_token')
            self.refresh_token = google_account.get('refresh_token')
            
            # Check token expiration
            expires_in = google_account.get('expires_in', 3600)
            connected_at = google_account.get('connected_at')
            if connected_at:
                try:
                    connected_time = datetime.fromisoformat(connected_at.replace('Z', '+00:00'))
                    self.token_expires_at = connected_time + timedelta(seconds=expires_in)
                except (ValueError, TypeError):
                    self.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            else:
                self.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            
            logger.info("Google Workspace credentials loaded successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load Google credentials: {e}", exc_info=True)
            return False
    
    def is_connected(self) -> bool:
        """Check if Google is connected and tokens are available"""
        if not self.access_token:
            return self._load_credentials()
        return True
    
    def _ensure_valid_token(self) -> bool:
        """Ensure access token is valid, refresh if needed"""
        if not self.is_connected():
            return False
        
        # Check if token needs refresh (refresh 5 minutes before expiration)
        if self.token_expires_at and datetime.utcnow() >= (self.token_expires_at - timedelta(minutes=5)):
            return self._refresh_access_token()
        
        return True
    
    def _refresh_access_token(self) -> bool:
        """Refresh the access token using refresh token"""
        if not self.refresh_token:
            logger.error("No refresh token available")
            return False
        
        try:
            token_uri = "https://oauth2.googleapis.com/token"
            token_data = {
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'refresh_token': self.refresh_token,
                'grant_type': 'refresh_token'
            }
            
            response = requests.post(token_uri, data=token_data)
            response.raise_for_status()
            tokens = response.json()
            
            self.access_token = tokens.get('access_token')
            expires_in = tokens.get('expires_in', 3600)
            self.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            
            # Update tokens in database
            self._save_tokens_to_db()
            
            logger.info("Access token refreshed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to refresh access token: {e}", exc_info=True)
            return False
    
    def _save_tokens_to_db(self):
        """Save updated tokens to database"""
        try:
            from distr.core.db import get_session, Settings
            
            with get_session() as session:
                settings = session.query(Settings).first()
                if not settings:
                    return
                
                connected_accounts = []
                if settings.connected_accounts:
                    try:
                        if isinstance(settings.connected_accounts, str):
                            parsed = json.loads(settings.connected_accounts)
                        else:
                            parsed = settings.connected_accounts
                        
                        if isinstance(parsed, list):
                            connected_accounts = parsed
                        elif isinstance(parsed, dict):
                            connected_accounts = [parsed]
                    except (json.JSONDecodeError, ValueError, TypeError):
                        connected_accounts = []
                
                # Update Google account
                google_account = None
                for account in connected_accounts:
                    if isinstance(account, dict) and account.get('provider') == 'google':
                        google_account = account
                        break
                
                if google_account:
                    google_account['access_token'] = self.access_token
                    if self.token_expires_at:
                        google_account['expires_in'] = int((self.token_expires_at - datetime.utcnow()).total_seconds())
                    settings.connected_accounts = json.dumps(connected_accounts)
                    session.commit()
                    
        except Exception as e:
            logger.error(f"Failed to save tokens to database: {e}", exc_info=True)
    
    def _make_request(self, method: str, url: str, **kwargs) -> Optional[Dict[str, Any]]:
        """Make authenticated API request"""
        if not self._ensure_valid_token():
            return None
        
        headers = kwargs.get('headers', {})
        headers['Authorization'] = f'Bearer {self.access_token}'
        kwargs['headers'] = headers
        
        try:
            response = requests.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else {}
        except requests.exceptions.HTTPError as e:
            error_msg = f"API request failed: {e}"
            activation_url = None
            service_name = "API"
            response_text = None
            
            if hasattr(e, 'response') and e.response:
                # Try to get response text, content, or status
                try:
                    response_text = e.response.text if hasattr(e.response, 'text') and e.response.text else None
                    if not response_text and hasattr(e.response, 'content'):
                        try:
                            response_text = e.response.content.decode('utf-8', errors='ignore')
                        except (UnicodeDecodeError, AttributeError):
                            response_text = str(e.response.content)
                    if not response_text:
                        response_text = f"Status: {e.response.status_code}, Headers: {dict(e.response.headers)}"
                except Exception as text_error:
                    response_text = f"Could not read response: {text_error}"
                
                # Log full response for debugging 400 and 500 errors
                if e.response.status_code in [400, 500]:
                    api_name = "Docs API" if "docs.googleapis.com" in url else "Gmail API" if "gmail.googleapis.com" in url else "API"
                    logger.error(f"{api_name} {e.response.status_code} error - Status: {e.response.status_code}")
                    logger.error(f"{api_name} {e.response.status_code} error - Full response: {response_text}")
                    logger.error(f"Request URL: {url}, Method: {method}")
                    if 'json' in kwargs:
                        request_data = kwargs.get('json')
                        # Log request data but mask sensitive info
                        if isinstance(request_data, dict) and 'message' in request_data:
                            safe_data = request_data.copy()
                            if 'raw' in safe_data.get('message', {}):
                                raw_msg = safe_data['message']['raw']
                                safe_data['message']['raw'] = f"{raw_msg[:50]}... (truncated, length: {len(raw_msg)})"
                            logger.error(f"Request data: {safe_data}")
                        else:
                            logger.error(f"Request data: {request_data}")
                
                if response_text:
                    try:
                        error_data = e.response.json()
                        if 'error' in error_data:
                            error_info = error_data['error']
                            
                            # Check for API not enabled errors
                            if (error_info.get('reason') == 'accessNotConfigured' or 
                                error_info.get('status') == 'PERMISSION_DENIED' or
                                'SERVICE_DISABLED' in str(error_info)):
                                
                                # Extract activation URL and service name
                                if 'details' in error_info:
                                    for detail in error_info['details']:
                                        if isinstance(detail, dict):
                                            if 'metadata' in detail and 'activationUrl' in detail['metadata']:
                                                activation_url = detail['metadata']['activationUrl']
                                            if 'metadata' in detail and 'serviceTitle' in detail['metadata']:
                                                service_name = detail['metadata']['serviceTitle']
                                
                                # Build user-friendly error message
                                if activation_url:
                                    error_msg = f"{service_name} is not enabled. Please enable it at: {activation_url}"
                                else:
                                    error_msg = f"{service_name} is not enabled. Please enable it in Google Cloud Console."
                                
                                logger.error(error_msg)
                                return None
                    except Exception as parse_error:
                        logger.debug(f"Could not parse error response: {parse_error}")
            
            logger.error(f"{error_msg}, Response: {response_text if response_text else 'N/A'}")
            return None
        except Exception as e:
            logger.error(f"API request error: {e}", exc_info=True)
            return None
    
    # ==================== Gmail Methods ====================
    
    def check_inbox(self, max_results: int = 10, query: str = "in:inbox") -> List[Dict[str, Any]]:
        """Check Gmail inbox - shows all emails in inbox by default (both read and unread)"""
        if not self._ensure_valid_token():
            return []
        
        url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults={max_results}&q={query}"
        result = self._make_request('GET', url)
        
        if not result:
            return []
        
        messages = []
        for msg_id in result.get('messages', []):
            msg_detail = self.get_email(msg_id['id'])
            if msg_detail:
                messages.append(msg_detail)
        
        return messages
    
    def get_email(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Get email details by ID"""
        if not message_id:
            logger.error("get_email called with empty message_id")
            return None
        if not self._ensure_valid_token():
            return None
        
        logger.info(f"get_email called with message_id={message_id!r}")
        url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}"
        result = self._make_request('GET', url, params={'format': 'full'})
        
        if not result:
            return None
        
        # Parse email data
        headers = {h['name']: h['value'] for h in result.get('payload', {}).get('headers', [])}
        body = ""
        attachments: List[Dict[str, Any]] = []
        
        payload = result.get('payload', {})
        if 'parts' in payload:
            for part in _walk_gmail_parts(payload):
                if part.get('mimeType') == 'text/plain':
                    data = part.get('body', {}).get('data', '')
                    if data:
                        body = base64.urlsafe_b64decode(data).decode('utf-8')
                elif part.get('mimeType') == 'text/html' and not body:
                    data = part.get('body', {}).get('data', '')
                    if data:
                        body = base64.urlsafe_b64decode(data).decode('utf-8')
                filename = part.get('filename') or ''
                attachment_id = part.get('body', {}).get('attachmentId')
                if filename and attachment_id:
                    attachments.append({
                        'message_id': message_id,
                        'attachment_id': attachment_id,
                        'filename': filename,
                        'mime_type': part.get('mimeType', ''),
                        'size': part.get('body', {}).get('size', 0),
                    })
        else:
            data = payload.get('body', {}).get('data', '')
            if data:
                body = base64.urlsafe_b64decode(data).decode('utf-8')
        
        return {
            'id': message_id,
            'threadId': result.get('threadId'),
            'subject': headers.get('Subject', ''),
            'from': headers.get('From', ''),
            'to': headers.get('To', ''),
            'date': headers.get('Date', ''),
            'snippet': result.get('snippet', ''),
            'body': body,
            'labels': result.get('labelIds', []),
            'attachments': attachments,
        }

    def download_email_attachment(
        self,
        message_id: str,
        attachment_id: str,
        filename: str,
        destination_dir: str,
    ) -> Optional[str]:
        """Download a Gmail attachment to ``destination_dir`` and return its path."""
        if not self._ensure_valid_token():
            return None
        if not message_id or not attachment_id or not filename:
            logger.error("download_email_attachment missing required arguments")
            return None

        safe_name = _safe_attachment_filename(filename)
        dest_dir = Path(destination_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}/attachments/{attachment_id}"
        result = self._make_request('GET', url)
        if not result or not result.get('data'):
            return None
        data = str(result.get('data') or '')
        padding = "=" * (-len(data) % 4)
        try:
            raw = base64.urlsafe_b64decode((data + padding).encode("ascii"))
        except Exception as exc:
            logger.error("Failed to decode Gmail attachment %s: %s", attachment_id, exc, exc_info=True)
            return None
        path = dest_dir / safe_name
        path.write_bytes(raw)
        return str(path)
    
    def send_email(self, to: str, subject: str, body: str, body_type: str = 'plain', cc: Optional[str] = None, bcc: Optional[str] = None) -> bool:
        """Send email via Gmail"""
        if not self._ensure_valid_token():
            return False
        
        try:
            message = MIMEText(body, body_type)
            message['to'] = to
            message['subject'] = subject
            if cc:
                message['cc'] = cc
            if bcc:
                message['bcc'] = bcc
            
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
            
            url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
            data = {'raw': raw_message}
            result = self._make_request('POST', url, json=data)
            
            return result is not None
            
        except Exception as e:
            logger.error(f"Failed to send email: {e}", exc_info=True)
            return False
    
    def draft_email(self, to: str, subject: str, body: str, body_type: str = 'plain') -> Optional[str]:
        """Create draft email"""
        if not self._ensure_valid_token():
            return None
        
        try:
            # Create message exactly like send_email for consistency
            message = MIMEText(body, body_type)
            message['to'] = to
            message['subject'] = subject
            # Gmail API will automatically set 'From' based on authenticated user
            
            # Encode message - use same method as send_email
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
            
            # Gmail API expects base64url encoding without padding for drafts
            # But we should keep it consistent with send_email which works
            # Actually, let's not strip padding - send_email doesn't strip it
            # raw_message = raw_message.rstrip('=')
            
            url = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
            data = {
                'message': {
                    'raw': raw_message
                }
            }
            
            logger.debug(f"Creating draft: to={to}, subject={subject}, body_length={len(body)}")
            logger.debug(f"Draft raw message length: {len(raw_message)}")
            
            # Make request with better error handling
            result = self._make_request('POST', url, json=data)
            
            if result and 'id' in result:
                draft_id = result.get('id')
                logger.info(f"Successfully created draft with ID: {draft_id}")
                return draft_id
            else:
                logger.error(f"Failed to create draft: No ID returned. Response: {result}")
                return None
            
        except Exception as e:
            logger.error(f"Failed to create draft: {e}", exc_info=True)
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None
    
    def list_drafts(self, max_results: int = 10) -> List[Dict[str, Any]]:
        """List all draft emails"""
        if not self._ensure_valid_token():
            return []
        
        url = f"https://gmail.googleapis.com/gmail/v1/users/me/drafts?maxResults={max_results}"
        result = self._make_request('GET', url)
        
        if not result:
            return []
        
        drafts = []
        for draft_item in result.get('drafts', []):
            draft_id = draft_item.get('id')
            message_id = draft_item.get('message', {}).get('id')
            
            if message_id:
                # Get the full message details
                email_detail = self.get_email(message_id)
                if email_detail:
                    email_detail['draft_id'] = draft_id
                    drafts.append(email_detail)
        
        return drafts
    
    def get_draft(self, draft_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific draft by ID"""
        if not self._ensure_valid_token():
            return None
        
        url = f"https://gmail.googleapis.com/gmail/v1/users/me/drafts/{draft_id}"
        result = self._make_request('GET', url)
        
        if not result:
            return None
        
        message_id = result.get('message', {}).get('id')
        if message_id:
            email_detail = self.get_email(message_id)
            if email_detail:
                email_detail['draft_id'] = draft_id
                return email_detail
        
        return None
    
    def list_emails_by_type(self, email_type: str = "sent", max_results: int = 10) -> List[Dict[str, Any]]:
        """List emails by type: 'sent', 'drafts', 'starred', 'important', etc."""
        if not self._ensure_valid_token():
            return []
        
        # Map email types to Gmail query
        query_map = {
            "sent": "in:sent",
            "drafts": "in:drafts",
            "starred": "is:starred",
            "important": "is:important",
            "unread": "is:unread",
            "read": "is:read",
            "trash": "in:trash",
            "spam": "in:spam"
        }
        
        query = query_map.get(email_type.lower(), f"in:{email_type}")
        url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults={max_results}&q={query}"
        result = self._make_request('GET', url)
        
        if not result:
            return []
        
        messages = []
        for msg_id in result.get('messages', []):
            msg_detail = self.get_email(msg_id['id'])
            if msg_detail:
                messages.append(msg_detail)
        
        return messages
    
    def reply_to_email(self, message_id: str, body: str, body_type: str = 'plain') -> bool:
        """Reply to an email"""
        if not self._ensure_valid_token():
            return False
        
        # Get original message
        original = self.get_email(message_id)
        if not original:
            return False
        
        # Create reply
        try:
            message = MIMEText(body, body_type)
            message['to'] = original['from']
            message['subject'] = f"Re: {original['subject']}"
            message['In-Reply-To'] = message_id
            message['References'] = message_id
            
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
            
            url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
            data = {'raw': raw_message, 'threadId': original.get('threadId')}
            result = self._make_request('POST', url, json=data)
            
            return result is not None
            
        except Exception as e:
            logger.error(f"Failed to reply to email: {e}", exc_info=True)
            return False
    
    def delete_email(self, message_id: str) -> bool:
        """Delete email"""
        if not self._ensure_valid_token():
            return False
        
        url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}"
        result = self._make_request('DELETE', url)
        
        return result is not None
    
    # ==================== Google Drive Methods ====================
    
    def list_drive_folders(self, folder_id: str = "root") -> List[Dict[str, Any]]:
        """List folders in Google Drive"""
        if not self._ensure_valid_token():
            return []
        
        url = "https://www.googleapis.com/drive/v3/files"
        params = {
            'q': f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            'fields': 'files(id, name, mimeType, modifiedTime)'
        }
        result = self._make_request('GET', url, params=params)
        
        return result.get('files', []) if result else []
    
    def list_drive_files(self, folder_id: str = "root", mime_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """List files in Google Drive"""
        if not self._ensure_valid_token():
            return []
        
        query = f"'{folder_id}' in parents and trashed=false"
        if mime_type:
            query += f" and mimeType='{mime_type}'"
        
        url = "https://www.googleapis.com/drive/v3/files"
        params = {
            'q': query,
            'fields': 'files(id, name, mimeType, modifiedTime, size)'
        }
        result = self._make_request('GET', url, params=params)
        
        return result.get('files', []) if result else []
    
    def read_drive_file(self, file_id: str) -> Optional[str]:
        """Read file content from Google Drive"""
        if not self._ensure_valid_token():
            return None
        
        # Get file metadata first
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
        file_info = self._make_request('GET', url, params={'fields': 'name, mimeType'})
        
        if not file_info:
            return None
        
        mime_type = file_info.get('mimeType', '')
        
        # For Google Docs, Sheets, Slides - export as text
        if mime_type == 'application/vnd.google-apps.document':
            export_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export?mimeType=text/plain"
        elif mime_type == 'application/vnd.google-apps.spreadsheet':
            export_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export?mimeType=text/csv"
        elif mime_type == 'application/vnd.google-apps.presentation':
            export_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export?mimeType=text/plain"
        else:
            # For regular files, download directly
            export_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
        
        try:
            headers = {'Authorization': f'Bearer {self.access_token}'}
            response = requests.get(export_url, headers=headers)
            response.raise_for_status()
            return response.text
        except Exception as e:
            logger.error(f"Failed to read file: {e}", exc_info=True)
            return None
    
    def upload_to_drive(self, file_path: str, folder_id: str = "root", name: Optional[str] = None, convert_to_google_doc: bool = False) -> Optional[str]:
        """Upload file to Google Drive
        
        Args:
            file_path: Path to file to upload
            folder_id: Google Drive folder ID (default: "root")
            name: Optional custom name for the file
            convert_to_google_doc: If True and file is DOCX, convert to Google Doc format
        """
        if not self._ensure_valid_token():
            return None
        
        path = Path(file_path)
        if not path.exists():
            logger.error(f"File not found: {file_path}")
            return None
        
        file_name = name or path.name
        
        try:
            # Determine MIME type
            mime_type_map = {
                '.pdf': 'application/pdf',
                '.txt': 'text/plain',
                '.md': 'text/markdown',
                '.html': 'text/html',
                '.json': 'application/json',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.png': 'image/png',
                '.gif': 'image/gif',
                '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                '.doc': 'application/msword'
            }
            mime_type = mime_type_map.get(path.suffix.lower(), 'application/octet-stream')
            
            # If converting DOC/DOCX to Google Doc, set target MIME type
            target_mime_type = None
            if convert_to_google_doc and path.suffix.lower() in ['.docx', '.doc']:
                target_mime_type = 'application/vnd.google-apps.document'
                # Remove extension from name if converting
                if file_name.endswith('.docx'):
                    file_name = file_name[:-5]
                elif file_name.endswith('.doc'):
                    file_name = file_name[:-4]
            
            # Get file metadata
            file_metadata = {
                'name': file_name,
                'parents': [folder_id]
            }
            
            if target_mime_type:
                file_metadata['mimeType'] = target_mime_type
            
            # Upload file with convert parameter if converting
            if target_mime_type:
                url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&convert=true"
            else:
                url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
            
            with open(file_path, 'rb') as f:
                files = {
                    'metadata': (None, json.dumps(file_metadata), 'application/json'),
                    'file': (file_name, f, mime_type)
                }
                
                headers = {'Authorization': f'Bearer {self.access_token}'}
                response = requests.post(url, headers=headers, files=files)
                response.raise_for_status()
                result = response.json()
                file_id = result.get('id')
                
                logger.info(f"File uploaded successfully: {file_id}, converted: {bool(target_mime_type)}")
                return file_id
                
        except Exception as e:
            logger.error(f"Failed to upload file: {e}", exc_info=True)
            return None
    
    def convert_docx_to_google_doc(self, docx_file_id: str) -> Optional[str]:
        """Convert an uploaded DOCX file to a native Google Doc
        
        Args:
            docx_file_id: The file ID of the uploaded DOCX file
            
        Returns:
            The file ID of the converted Google Doc, or None if conversion failed
        """
        if not self._ensure_valid_token():
            return None
        
        try:
            # Get the file name
            file_info = self._make_request('GET', f"https://www.googleapis.com/drive/v3/files/{docx_file_id}", params={'fields': 'name'})
            file_name = file_info.get('name', 'Document') if file_info else 'Document'
            
            # Remove .docx extension if present
            if file_name.endswith('.docx') or file_name.endswith('.doc'):
                file_name = file_name.rsplit('.', 1)[0]
            
            # Download the file content
            download_url = f"https://www.googleapis.com/drive/v3/files/{docx_file_id}?alt=media"
            headers = {'Authorization': f'Bearer {self.access_token}'}
            response = requests.get(download_url, headers=headers)
            response.raise_for_status()
            file_content = response.content
            
            # Re-upload with Google Docs MIME type to trigger conversion
            file_metadata = {
                'name': file_name,
                'mimeType': 'application/vnd.google-apps.document'
            }
            
            upload_url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&convert=true"
            files = {
                'metadata': (None, json.dumps(file_metadata), 'application/json'),
                'file': (file_name, io.BytesIO(file_content), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
            }
            
            upload_response = requests.post(upload_url, headers=headers, files=files)
            upload_response.raise_for_status()
            result = upload_response.json()
            
            if result and 'id' in result:
                google_doc_id = result.get('id')
                logger.info(f"Successfully converted DOCX {docx_file_id} to Google Doc {google_doc_id}")
                # Optionally delete the original DOCX
                try:
                    self._make_request('DELETE', f"https://www.googleapis.com/drive/v3/files/{docx_file_id}")
                except Exception:
                    pass  # Don't fail if deletion fails
                return google_doc_id
            else:
                logger.error(f"Failed to convert DOCX: No ID returned. Response: {result}")
                return None
                
        except Exception as e:
            logger.error(f"Failed to convert DOCX to Google Doc: {e}", exc_info=True)
            return None
    
    def get_document_url(self, doc_id: str) -> str:
        """Get the view/edit URL for a Google Doc"""
        return f"https://docs.google.com/document/d/{doc_id}/edit"
    
    def open_url_in_brave(self, url: str) -> bool:
        """Open URL in Brave browser (macOS)"""
        import subprocess
        import platform
        
        try:
            system = platform.system()
            if system == 'Darwin':  # macOS
                # Try to open in Brave specifically
                result = subprocess.run(
                    ['open', '-a', 'Brave Browser', url],
                    capture_output=True,
                    timeout=5
                )
                if result.returncode == 0:
                    logger.info(f"Opened URL in Brave: {url}")
                    return True
                else:
                    # Fallback to default browser
                    logger.warning(f"Failed to open in Brave, trying default browser")
                    subprocess.run(['open', url], timeout=5)
                    return True
            elif system == 'Windows':
                # Try Brave on Windows
                brave_paths = [
                    r'C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe',
                    r'C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe'
                ]
                for brave_path in brave_paths:
                    if Path(brave_path).exists():
                        subprocess.run([brave_path, url], timeout=5)
                        logger.info(f"Opened URL in Brave: {url}")
                        return True
                # Fallback
                subprocess.run(['start', url], shell=True, timeout=5)
                return True
            else:  # Linux
                subprocess.run(['brave-browser', url], timeout=5)
                return True
        except Exception as e:
            logger.error(f"Failed to open URL in Brave: {e}", exc_info=True)
            return False
    
    def read_pdf_from_drive(self, file_id: str) -> Optional[str]:
        """Read PDF file from Google Drive (exports as text)"""
        if not self._ensure_valid_token():
            return None
        
        # Export PDF as plain text
        export_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export?mimeType=text/plain"
        
        try:
            headers = {'Authorization': f'Bearer {self.access_token}'}
            response = requests.get(export_url, headers=headers)
            response.raise_for_status()
            return response.text
        except Exception as e:
            logger.error(f"Failed to read PDF: {e}", exc_info=True)
            return None
    
    # ==================== Google Calendar Methods ====================
    
    def create_calendar_event(self, summary: str, start_time: datetime, end_time: datetime, 
                             description: Optional[str] = None, location: Optional[str] = None) -> Optional[str]:
        """Create calendar event"""
        if not self._ensure_valid_token():
            return None
        
        event = {
            'summary': summary,
            'start': {
                'dateTime': start_time.isoformat(),
                'timeZone': 'UTC'
            },
            'end': {
                'dateTime': end_time.isoformat(),
                'timeZone': 'UTC'
            }
        }
        
        if description:
            event['description'] = description
        if location:
            event['location'] = location
        
        url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
        result = self._make_request('POST', url, json=event)
        
        return result.get('id') if result else None

    def create_calendar_events_batch(
        self,
        events: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Create multiple primary-calendar events in order (one API call each).

        Each dict must include: summary (str), start_time (datetime), end_time (datetime).
        Optional: description, location (str).

        Returns rows: index, summary, event_id (or None), error (or None).
        """
        results: List[Dict[str, Any]] = []
        for pos, ev in enumerate(events):
            logical_index = ev.get("_batch_index")
            if logical_index is None:
                logical_index = pos
            summary = ev.get("summary")
            st = ev.get("start_time")
            et = ev.get("end_time")
            if not summary or st is None or et is None:
                results.append(
                    {
                        "index": logical_index,
                        "summary": summary,
                        "event_id": None,
                        "error": "missing summary, start_time, or end_time",
                    }
                )
                continue
            if not isinstance(st, datetime) or not isinstance(et, datetime):
                results.append(
                    {
                        "index": logical_index,
                        "summary": summary,
                        "event_id": None,
                        "error": "start_time and end_time must be datetime instances",
                    }
                )
                continue
            eid = self.create_calendar_event(
                summary,
                st,
                et,
                ev.get("description"),
                ev.get("location"),
            )
            results.append(
                {
                    "index": logical_index,
                    "summary": summary,
                    "event_id": eid,
                    "error": None if eid else "calendar API returned no id",
                }
            )
        return results

    def get_calendar_events(self, time_min: Optional[datetime] = None, time_max: Optional[datetime] = None, 
                           max_results: int = 10) -> Optional[List[Dict[str, Any]]]:
        """Get calendar events"""
        if not self._ensure_valid_token():
            return None
        
        url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
        params = {
            'maxResults': max_results,
            'singleEvents': True,
            'orderBy': 'startTime'
        }
        
        if time_min:
            params['timeMin'] = time_min.isoformat() + 'Z'
        if time_max:
            params['timeMax'] = time_max.isoformat() + 'Z'
        
        result = self._make_request('GET', url, params=params)
        
        if result is None:
            return None  # API error (e.g., not enabled)
        
        return result.get('items', [])
    
    def get_schedule_tomorrow(self) -> Optional[List[Dict[str, Any]]]:
        """Get schedule for tomorrow"""
        tomorrow = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        time_min = tomorrow
        time_max = tomorrow + timedelta(days=1)
        return self.get_calendar_events(time_min=time_min, time_max=time_max)
    
    def get_schedule_this_week(self) -> Optional[List[Dict[str, Any]]]:
        """Get schedule for this week"""
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        # Get Monday of current week
        days_since_monday = today.weekday()
        monday = today - timedelta(days=days_since_monday)
        next_monday = monday + timedelta(days=7)
        
        return self.get_calendar_events(time_min=monday, time_max=next_monday)
    
    # ==================== Google Docs Methods ====================
    
    def create_doc_from_markdown(self, title: str, markdown_content: str, folder_id: str = "root", preserve_formatting: bool = True) -> Optional[str]:
        """Create Google Doc from markdown content with proper formatting
        
        Args:
            title: Document title
            markdown_content: Markdown content to convert
            folder_id: Google Drive folder ID (default: "root")
            preserve_formatting: If True, preserves headings, bold, lists, etc. If False, uses plain text.
        """
        if not self._ensure_valid_token():
            return None
        
        try:
            # First create empty document
            doc_metadata = {
                'name': title,
                'mimeType': 'application/vnd.google-apps.document',
                'parents': [folder_id]
            }
            
            url = "https://www.googleapis.com/drive/v3/files"
            result = self._make_request('POST', url, json=doc_metadata)
            
            if not result:
                return None
            
            doc_id = result.get('id')
            
            if not preserve_formatting:
                # Simple plain text conversion (original behavior)
                import re
                plain_text = markdown_content
                plain_text = re.sub(r'^#+\s+', '', plain_text, flags=re.MULTILINE)
                plain_text = re.sub(r'\*\*([^*]+)\*\*', r'\1', plain_text)
                plain_text = re.sub(r'\*([^*]+)\*', r'\1', plain_text)
                plain_text = re.sub(r'```[^`]+```', '', plain_text, flags=re.DOTALL)
                plain_text = re.sub(r'`([^`]+)`', r'\1', plain_text)
                plain_text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', plain_text)
                
                return self._insert_plain_text(doc_id, plain_text)
            else:
                # Use formatted conversion with proper styling
                return self._insert_formatted_markdown(doc_id, markdown_content)
            
        except Exception as e:
            logger.error(f"Failed to create doc from markdown: {e}", exc_info=True)
            return None
    
    def _insert_plain_text(self, doc_id: str, plain_text: str) -> Optional[str]:
        """Insert plain text into Google Doc"""
        try:
            docs_url = f"https://docs.googleapis.com/v1/documents/{doc_id}"
            doc_info = self._make_request('GET', docs_url)
            
            if not doc_info:
                return doc_id
            
            body_end_index = 1
            if 'body' in doc_info and 'content' in doc_info['body']:
                for element in doc_info['body']['content']:
                    if 'endIndex' in element:
                        body_end_index = max(body_end_index, element['endIndex'])
            
            batch_update_url = f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate"
            requests_list = []
            
            if body_end_index > 2:
                delete_end = max(2, body_end_index - 1)
                if delete_end > 1:
                    requests_list.append({
                        'deleteContentRange': {
                            'range': {
                                'startIndex': 1,
                                'endIndex': delete_end
                            }
                        }
                    })
            
            if plain_text and plain_text.strip():
                requests_list.append({
                    'insertText': {
                        'location': {'index': 1},
                        'text': plain_text
                    }
                })
            
            if requests_list:
                requests_data = {'requests': requests_list}
                update_result = self._make_request('POST', batch_update_url, json=requests_data)
            else:
                logger.warning(f"No content to insert into document {doc_id}")
                update_result = None
            
            if not update_result:
                logger.warning(f"Document {doc_id} created but content insertion failed. Document may be empty.")
            
            return doc_id
        except Exception as e:
            logger.error(f"Failed to insert plain text: {e}", exc_info=True)
            return doc_id
    
    def _insert_formatted_markdown(self, doc_id: str, markdown_content: str) -> Optional[str]:
        """Insert formatted markdown into Google Doc with proper styling"""
        import re
        
        try:
            docs_url = f"https://docs.googleapis.com/v1/documents/{doc_id}"
            doc_info = self._make_request('GET', docs_url)
            
            if not doc_info:
                return doc_id
            
            body_end_index = 1
            if 'body' in doc_info and 'content' in doc_info['body']:
                for element in doc_info['body']['content']:
                    if 'endIndex' in element:
                        body_end_index = max(body_end_index, element['endIndex'])
            
            batch_update_url = f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate"
            requests_list = []
            
            # Delete default content
            if body_end_index > 2:
                delete_end = max(2, body_end_index - 1)
                if delete_end > 1:
                    requests_list.append({
                        'deleteContentRange': {
                            'range': {
                                'startIndex': 1,
                                'endIndex': delete_end
                            }
                        }
                    })
            
            # Parse markdown and create formatted requests
            lines = markdown_content.split('\n')
            current_index = 1
            
            i = 0
            while i < len(lines):
                line = lines[i]
                stripped = line.strip()
                
                # Headings
                if stripped.startswith('#'):
                    level = len(stripped) - len(stripped.lstrip('#'))
                    text = stripped.lstrip('#').strip()
                    if text:
                        heading_size_map = {1: 20, 2: 18, 3: 16, 4: 14, 5: 12, 6: 11}
                        heading_size = heading_size_map.get(min(level, 6), 11)
                        
                        requests_list.append({
                            'insertText': {
                                'location': {'index': current_index},
                                'text': text + '\n'
                            }
                        })
                        # Apply heading style
                        end_index = current_index + len(text) + 1
                        requests_list.append({
                            'updateParagraphStyle': {
                                'range': {
                                    'startIndex': current_index,
                                    'endIndex': end_index
                                },
                                'paragraphStyle': {
                                    'namedStyleType': f'HEADING_{min(level, 6)}'
                                },
                                'fields': 'namedStyleType'
                            }
                        })
                        current_index = end_index
                
                # Horizontal rules
                elif stripped in ['---', '***', '___']:
                    requests_list.append({
                        'insertText': {
                            'location': {'index': current_index},
                            'text': '\n'
                        }
                    })
                    current_index += 1
                
                # Unordered lists
                elif stripped.startswith('- ') or stripped.startswith('* '):
                    text = stripped[2:].strip()
                    if text:
                        # Remove bold/italic markers for now (can enhance later)
                        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
                        text = re.sub(r'\*([^*]+)\*', r'\1', text)
                        
                        requests_list.append({
                            'insertText': {
                                'location': {'index': current_index},
                                'text': '• ' + text + '\n'
                            }
                        })
                        current_index += len('• ' + text + '\n')
                
                # Bold text (inline)
                elif '**' in stripped or '__' in stripped:
                    # Simple approach: insert text and apply bold formatting
                    text = stripped
                    # Replace markdown bold with plain text for now
                    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
                    text = re.sub(r'__([^_]+)__', r'\1', text)
                    
                    if text:
                        requests_list.append({
                            'insertText': {
                                'location': {'index': current_index},
                                'text': text + '\n'
                            }
                        })
                        current_index += len(text + '\n')
                
                # Regular paragraph
                elif stripped:
                    # Remove markdown formatting
                    text = stripped
                    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
                    text = re.sub(r'\*([^*]+)\*', r'\1', text)
                    text = re.sub(r'`([^`]+)`', r'\1', text)
                    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
                    
                    if text:
                        requests_list.append({
                            'insertText': {
                                'location': {'index': current_index},
                                'text': text + '\n'
                            }
                        })
                        current_index += len(text + '\n')
                
                # Empty line
                else:
                    requests_list.append({
                        'insertText': {
                            'location': {'index': current_index},
                            'text': '\n'
                        }
                    })
                    current_index += 1
                
                i += 1
            
            if requests_list:
                requests_data = {'requests': requests_list}
                update_result = self._make_request('POST', batch_update_url, json=requests_data)
            else:
                logger.warning(f"No content to insert into document {doc_id}")
                update_result = None
            
            if not update_result:
                logger.warning(f"Document {doc_id} created but content insertion failed. Document may be empty.")
            
            return doc_id
        except Exception as e:
            logger.error(f"Failed to insert formatted markdown: {e}", exc_info=True)
            return doc_id
    
    def _markdown_to_html(self, markdown: str) -> str:
        """Convert markdown to HTML for Google Docs"""
        import re
        
        html = markdown
        
        # Headers
        html = re.sub(r'^### (.*)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
        html = re.sub(r'^## (.*)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
        html = re.sub(r'^# (.*)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
        
        # Bold
        html = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', html)
        
        # Italic
        html = re.sub(r'\*(.*?)\*', r'<i>\1</i>', html)
        
        # Code blocks
        html = re.sub(r'```([^`]+)```', r'<pre>\1</pre>', html, flags=re.DOTALL)
        html = re.sub(r'`([^`]+)`', r'<code>\1</code>', html)
        
        # Links
        html = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<a href="\2">\1</a>', html)
        
        # Line breaks
        html = html.replace('\n\n', '</p><p>')
        html = '<p>' + html + '</p>'
        
        return html
