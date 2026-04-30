"""
Index Folder Tool - Index dropped folders into RAG for semantic search.

This tool allows the LLM to index folders on-demand when the user wants to
search or query folder contents.
"""

import logging
import os
import json
import re
from typing import Optional, Any
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class IndexFolderInput(BaseModel):
    """Input schema for index_folder tool."""
    folder_path: str = Field(description="Path to folder to index. Can be a full path or 'the folder I dropped' to reference the most recently dropped folder.")
    chat_id: Optional[int] = Field(default=None, description="Optional chat ID for per-chat indexing. If not provided, uses current chat.")


class IndexFolderTool(BaseTool):
    """Index a dropped folder into RAG for semantic search."""
    
    name: str = "index_folder"
    description: str = (
        "Index a dropped folder into RAG for semantic search. "
        "ALWAYS call this tool automatically when the user asks questions about folder contents, such as: "
        "'what's in this folder', 'tell me about the code in this folder', 'what does this folder contain', "
        "'search this folder', 'query this folder', or any questions about files/code within a dropped folder. "
        "The folder must have been dropped on the oracle ball first. "
        "You can reference it as 'the folder I dropped' or provide the full path. "
        "This tool indexes the folder contents so they can be queried semantically via RAG."
    )
    args_schema: type[BaseModel] = IndexFolderInput
    
    # Pydantic fields for inter-process communication (excluded from schema)
    event_queue: Optional[Any] = Field(default=None, exclude=True)
    command_queue: Optional[Any] = Field(default=None, exclude=True)
    confirmation_results_dict: Optional[Any] = Field(default=None, exclude=True)
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, event_queue=None, command_queue=None, confirmation_results_dict=None, chat_manager=None, **kwargs):
        """Initialize index folder tool."""
        super().__init__(event_queue=event_queue, command_queue=command_queue, confirmation_results_dict=confirmation_results_dict, chat_manager=chat_manager, **kwargs)
    
    def _get_last_dropped_folder(self) -> Optional[str]:
        """Get the most recently dropped folder."""
        try:
            storage_dir = os.path.join(os.path.expanduser("~"), ".decisionsai", "dropped_files")
            storage_file = os.path.join(storage_dir, "current_files.json")
            
            if not os.path.exists(storage_file):
                return None
            
            with open(storage_file, 'r') as f:
                data = json.load(f)
                dropped_folders = data.get("dropped_folders", [])
                
                if not dropped_folders:
                    return None
                
                # Return the last folder (most recently dropped)
                # Filter to only existing folders
                existing_folders = [f for f in dropped_folders if os.path.exists(f) and os.path.isdir(f)]
                if existing_folders:
                    return existing_folders[-1]
                
                return None
        except Exception as e:
            logger.error(f"Error getting last dropped folder: {e}")
            return None
    
    def _resolve_folder_path(self, folder_path: str, chat_id: Optional[int] = None) -> str:
        """Resolve folder path, handling 'the folder I dropped' references."""
        folder_path = folder_path.strip().strip('"').strip("'")
        
        # Check for "the folder I dropped" or similar references
        dropped_folder_patterns = [
            r'the\s+folder\s+i\s+(?:just\s+)?(?:dropped|gave\s+you|gave)',
            r'the\s+(?:last|most\s+recent)\s+(?:folder|dropped\s+folder)',
            r'that\s+folder\s+i\s+dropped',
            r'my\s+(?:last|recent)\s+folder'
        ]
        
        for pattern in dropped_folder_patterns:
            if re.search(pattern, folder_path.lower()):
                last_folder = self._get_last_dropped_folder()
                if last_folder:
                    logger.info(f"Resolved '{folder_path}' to last dropped folder: {last_folder}")
                    return last_folder
                else:
                    raise ValueError("No dropped folders found. Please drop a folder on the oracle ball first.")
        
        # If it's already a full path and exists, return it
        if os.path.exists(folder_path) and os.path.isdir(folder_path):
            return os.path.abspath(folder_path)
        
        # Try to resolve as relative path
        if not os.path.isabs(folder_path):
            # Try relative to home directory
            home = os.path.expanduser("~")
            resolved = os.path.join(home, folder_path)
            if os.path.exists(resolved) and os.path.isdir(resolved):
                return os.path.abspath(resolved)
        
        # If we get here, the path doesn't exist
        raise ValueError(f"Folder not found: {folder_path}. Please provide a valid folder path or drop a folder on the oracle ball first.")
    
    def _run(self, folder_path: str, chat_id: Optional[int] = None, **kwargs) -> str:
        """Index the specified folder into RAG."""
        try:
            # Resolve folder path (handles "the folder I dropped" references)
            resolved_path = self._resolve_folder_path(folder_path, chat_id)
            
            logger.info(f"Indexing folder: {resolved_path}")
            
            # Get chat_id if not provided
            if chat_id is None and self.chat_manager:
                try:
                    chat_id = self.chat_manager.get_current_chat()
                    if chat_id is None:
                        return "Error: No active chat session. Please start a conversation first."
                except Exception as e:
                    logger.warning(f"Could not get current chat_id: {e}")
                    return "Error: Could not determine current chat session."
            
            if chat_id is None:
                return "Error: Chat ID is required. Please provide a chat_id or ensure there's an active chat session."
            
            # Get model and excluded file types from settings
            try:
                from distr.core.settings import load_settings_from_db
                settings = load_settings_from_db()
                model_name = settings.get('agent_model', 'deepseek-v4-pro:cloud') or 'deepseek-v4-pro:cloud'
                exclude_text = settings.get('excluded_files', '')
                exclude_extensions = None
                if exclude_text:
                    exclude_extensions = [
                        ext.strip() if ext.strip().startswith('.') else f".{ext.strip()}"
                        for ext in exclude_text.split(',')
                        if ext.strip()
                    ]
            except Exception as e:
                logger.warning(f"Could not load settings, using defaults: {e}")
                model_name = 'deepseek-v4-pro:cloud'
                exclude_extensions = None
            
            # Index the folder
            from distr.core.agent.services.rag.integration import index_chat_directory
            
            result = index_chat_directory(
                chat_id=chat_id,
                directory_path=resolved_path,
                model_name=model_name,
                exclude_extensions=exclude_extensions
            )
            
            if result.get('success'):
                documents_indexed = result.get('documents_indexed', 0)
                files_processed = result.get('files_processed', 0)
                logger.info(f"Successfully indexed folder {resolved_path}: {documents_indexed} documents from {files_processed} files")
                return f"Successfully indexed folder '{os.path.basename(resolved_path)}' ({resolved_path}). Indexed {documents_indexed} document(s) from {files_processed} file(s). The folder contents can now be queried semantically."
            else:
                error_msg = result.get('error', 'Unknown error')
                logger.error(f"Failed to index folder {resolved_path}: {error_msg}")
                return f"Failed to index folder '{os.path.basename(resolved_path)}': {error_msg}"
                
        except ValueError as e:
            # User-friendly error messages
            return str(e)
        except Exception as e:
            logger.error(f"Error indexing folder: {e}", exc_info=True)
            return f"Error indexing folder: {str(e)}"

