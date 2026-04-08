"""
Upload DOC/DOCX to Google Doc Tool

This tool uploads DOC or DOCX files from the user's computer to Google Drive,
converts them to Google Docs format, and opens them in the browser.
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector
from distr.core.agent.tools.base import LazyToolMixin

logger = logging.getLogger(__name__)


def resolve_folder_path(folder_name: str) -> str:
    """Resolve folder references like 'my desktop' to actual paths."""
    home = os.path.expanduser("~")
    folder_lower = folder_name.lower().strip()
    
    # Handle "my X" pattern
    if folder_lower.startswith('my '):
        folder_lower = folder_lower[3:]
    
    folder_map = {
        'desktop': os.path.join(home, 'Desktop'),
        'documents': os.path.join(home, 'Documents'),
        'downloads': os.path.join(home, 'Downloads'),
        'pictures': os.path.join(home, 'Pictures'),
        'music': os.path.join(home, 'Music'),
        'videos': os.path.join(home, 'Movies'),
        'movies': os.path.join(home, 'Movies'),
    }
    
    return folder_map.get(folder_lower, folder_name)


def find_file(file_name: str, search_folders: Optional[str] = None) -> Optional[str]:
    """Find a file by name, supporting partial names and folder references."""
    from difflib import SequenceMatcher
    
    # Normalize file name
    file_name_clean = file_name.strip().strip('"').strip("'")
    
    # Parse search folders
    if search_folders:
        folders = [resolve_folder_path(f.strip()) for f in search_folders.split(',')]
    else:
        # Default search folders
        home = os.path.expanduser("~")
        folders = [
            os.path.join(home, "Downloads"),
            os.path.join(home, "Documents"),
            os.path.join(home, "Desktop"),
        ]
    
    # Extract base name and check for extension
    base_name = os.path.basename(file_name_clean)
    has_extension = '.' in base_name
    
    # If no extension, try DOC/DOCX extensions
    extensions_to_try = []
    if has_extension:
        extensions_to_try = [base_name]
    else:
        extensions_to_try = [
            base_name + '.docx',
            base_name + '.doc',
        ]
    
    best_match = None
    best_score = 0.0
    
    logger.info(f"Searching for '{file_name_clean}' in {len(folders)} folder(s)")
    
    for folder in folders:
        if not os.path.exists(folder):
            continue
        
        for root, dirs, files in os.walk(folder):
            for file in files:
                # Check exact matches first
                for ext_name in extensions_to_try:
                    if file == ext_name or file.lower() == ext_name.lower():
                        full_path = os.path.join(root, file)
                        if os.path.isfile(full_path):
                            logger.info(f"Found exact match: {full_path}")
                            return full_path
                
                # Fuzzy match
                score = SequenceMatcher(None, file.lower(), base_name.lower()).ratio()
                if score > best_score and score > 0.6:
                    # Check if it's a DOC/DOCX file
                    if file.lower().endswith(('.doc', '.docx')):
                        best_match = os.path.join(root, file)
                        best_score = score
    
    if best_match and os.path.isfile(best_match):
        logger.info(f"Found fuzzy match: {best_match} (score: {best_score:.2f})")
        return best_match
    
    return None


class UploadDocToGoogleInput(BaseModel):
    """Input schema for upload DOC to Google Doc tool."""
    file_path: str = Field(description="File path or name. Can be full path, file name, or reference like 'my documents/report.docx'. Supports 'the file I dropped' for dropped files.")
    open_in_brave: bool = Field(default=True, description="Whether to open the document in Brave browser after upload")
    search_folders: Optional[str] = Field(default=None, description="Optional comma-separated list of folders to search (e.g., 'Downloads,Documents,Desktop'). Defaults to common folders if not specified.")


class UploadDocToGoogleTool(LazyToolMixin, BaseTool):
    """Tool for uploading DOC/DOCX files to Google Drive, converting to Google Docs, and opening in browser."""
    
    name: str = "upload_doc_to_google"
    description: str = (
        "Upload a DOC or DOCX file from your computer to Google Drive, convert it to Google Docs format, and open it in Brave browser.\n"
        "\n"
        "This tool will:\n"
        "- Find the file by name or path (supports fuzzy matching)\n"
        "- Upload it to Google Drive\n"
        "- Convert it to native Google Doc format\n"
        "- Open it in Brave browser\n"
        "\n"
        "Use this when user says:\n"
        "- 'Upload my document.docx to Google Docs and open it'\n"
        "- 'Take the file I dropped and convert it to a Google Doc'\n"
        "- 'Upload report.doc from my Documents folder to Google Drive'\n"
        "- 'Convert my Word document to Google Docs and open it'\n"
        "\n"
        "Supports:\n"
        "- Full file paths\n"
        "- File names (searches Downloads, Documents, Desktop)\n"
        "- Folder references ('my documents/report.docx')\n"
        "- Dropped files ('the file I dropped')\n"
        "- Fuzzy matching for partial file names\n"
    )
    args_schema: type[BaseModel] = UploadDocToGoogleInput
    
    def __init__(self):
        super().__init__()

    def _lazy_init(self):
        object.__setattr__(self, 'connector', GoogleWorkspaceConnector())
    
    def _get_last_dropped_file(self) -> Optional[str]:
        """Get the last file that was dropped on the oracle ball."""
        try:
            from distr.core.agent.tools.integrations.rube_tool import get_dropped_files
            dropped_files = get_dropped_files()
            if dropped_files and len(dropped_files) > 0:
                return dropped_files[-1]
        except Exception:
            pass
        return None
    
    def _resolve_file_path(self, file_path: str, search_folders: Optional[str] = None) -> Optional[str]:
        """Resolve file path, handling various input formats."""
        file_path = file_path.strip().strip('"').strip("'")
        
        # Check for "the file I dropped" or similar references
        dropped_file_patterns = [
            r'the\s+file\s+i\s+(?:just\s+)?(?:dropped|gave\s+you|gave)',
            r'the\s+(?:last|most\s+recent)\s+(?:file|dropped\s+file)',
            r'that\s+file\s+i\s+dropped',
            r'my\s+(?:last|recent)\s+file'
        ]
        
        for pattern in dropped_file_patterns:
            if re.search(pattern, file_path.lower()):
                last_file = self._get_last_dropped_file()
                if last_file:
                    logger.info(f"Resolved '{file_path}' to last dropped file: {last_file}")
                    return last_file
                else:
                    raise ValueError("No dropped files found. Please drop a file on the oracle ball first.")
        
        # If it's already a full path and exists, return it
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return file_path
        
        # If it contains a slash, try to resolve as path
        if '/' in file_path or '\\' in file_path:
            # Try to resolve folder references in the path
            parts = file_path.replace('\\', '/').split('/')
            resolved_parts = []
            for part in parts:
                if part in ['my', 'the']:
                    continue
                resolved = resolve_folder_path(part)
                if resolved != part:
                    resolved_parts.append(resolved)
                else:
                    resolved_parts.append(part)
            
            resolved_path = '/'.join(resolved_parts)
            if os.path.exists(resolved_path) and os.path.isfile(resolved_path):
                return resolved_path
        
        # Otherwise, search for the file by name
        return find_file(file_path, search_folders)
    
    def _run(self, file_path: str, open_in_brave: bool = True, search_folders: Optional[str] = None, **kwargs) -> str:
        """Execute upload DOC to Google Doc workflow"""
        self._ensure_initialized()
        try:
            # Step 1: Resolve file path
            logger.info(f"Resolving file path: {file_path}")
            resolved_path = self._resolve_file_path(file_path, search_folders)
            
            if not resolved_path:
                return f"Error: Could not find file '{file_path}'. Please provide a full path or ensure the file exists in Downloads, Documents, or Desktop."
            
            if not os.path.exists(resolved_path):
                return f"Error: File not found: {resolved_path}"
            
            if not os.path.isfile(resolved_path):
                return f"Error: Path is not a file: {resolved_path}"
            
            # Check if it's a DOC/DOCX file
            file_ext = Path(resolved_path).suffix.lower()
            if file_ext not in ['.doc', '.docx']:
                return f"Error: File must be a DOC or DOCX file. Found: {file_ext}"
            
            logger.info(f"Found file: {resolved_path}")
            
            # Step 2: Check if Google is connected
            if not self.connector.is_connected():
                return "Error: Google is not connected. Please connect your Google account in Settings > Advanced."
            
            # Step 3: Get file name without extension for the Google Doc
            file_name = Path(resolved_path).stem
            # Sanitize filename
            safe_name = re.sub(r'[^\w\s-]', '', file_name)[:50]
            
            # Step 4: Upload to Drive with conversion
            logger.info(f"Uploading {resolved_path} to Google Drive with conversion...")
            file_id = self.connector.upload_to_drive(
                file_path=resolved_path,
                folder_id="root",
                name=safe_name,
                convert_to_google_doc=True
            )
            
            if not file_id:
                # Try uploading without conversion, then convert separately
                logger.info("Upload with conversion failed, trying separate upload and convert...")
                file_id = self.connector.upload_to_drive(
                    file_path=resolved_path,
                    folder_id="root",
                    name=safe_name,
                    convert_to_google_doc=False
                )
                
                if file_id:
                    # Check if it's already a Google Doc (might have been auto-converted)
                    file_info = self.connector._make_request(
                        'GET',
                        f"https://www.googleapis.com/drive/v3/files/{file_id}",
                        params={'fields': 'mimeType'}
                    )
                    
                    if file_info and file_info.get('mimeType') != 'application/vnd.google-apps.document':
                        # Convert to Google Doc
                        google_doc_id = self.connector.convert_docx_to_google_doc(file_id)
                        if google_doc_id:
                            file_id = google_doc_id
                        else:
                            return f"Error: Uploaded file (ID: {file_id}) but failed to convert to Google Doc. You can access it at: https://drive.google.com/file/d/{file_id}/view"
            
            if not file_id:
                return "Error: Failed to upload file to Google Drive."
            
            # Step 5: Get document URL
            doc_url = self.connector.get_document_url(file_id)
            logger.info(f"Successfully uploaded and converted: {file_id}, URL: {doc_url}")
            
            # Step 6: Open in Brave if requested
            if open_in_brave:
                self.connector.open_url_in_brave(doc_url)
            
            return f"Successfully uploaded '{safe_name}' to Google Drive and converted to Google Doc (ID: {file_id}). Document URL: {doc_url}"
            
        except ValueError as e:
            return str(e)
        except Exception as e:
            logger.error(f"Error in upload DOC to Google Doc workflow: {e}", exc_info=True)
            return f"Error: {str(e)}"
    
    async def _arun(self, file_path: str, open_in_brave: bool = True, search_folders: Optional[str] = None, **kwargs) -> str:
        """Async run method"""
        self._ensure_initialized()
        return self._run(file_path=file_path, open_in_brave=open_in_brave, search_folders=search_folders, **kwargs)

