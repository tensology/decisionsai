"""
Fast File Operations Tool - Direct execution without LLM for common file operations.

This tool handles simple file operations directly using Python/os commands
for fast execution.
"""

import logging
import os
import subprocess
from typing import Optional, List, Any
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

from distr.core.files.user_library_guard import (
    REFUSAL_TOOL_DIRECTORY_DELETE,
    is_protected_library_root,
    refusal_delete_library_root,
    refusal_protected_library_root,
)

logger = logging.getLogger(__name__)

_FILE_SAFETY_UNAVAILABLE = (
    "File safety checks are unavailable — this operation was blocked for safety."
)


class FileOperationsInput(BaseModel):
    """Input schema for file_operations tool."""
    operation: str = Field(
        description=(
            "Operation type: 'list', 'create', 'read', 'delete' (single files only — "
            "directories forbidden), 'copy', 'move'"
        )
    )
    path: str = Field(description="File or directory path (supports 'my desktop', 'my documents', etc.)")
    content: Optional[str] = Field(default=None, description="Content for create/write operations")
    destination: Optional[str] = Field(default=None, description="Destination path for copy/move operations")


class FileOperationsTool(BaseTool):
    """Fast file operations tool for direct execution without LLM."""
    
    name: str = "file_operations"
    description: str = (
        "Fast file operations tool for simple file/directory operations. "
        "Use this for: listing files, creating files, reading files, deleting single files only "
        "(never whole folders), copying files, moving files. "
        "This tool executes directly without LLM, making it much faster for simple operations. "
        "For complex operations requiring code generation, use execute_code instead."
    )
    args_schema: type[BaseModel] = FileOperationsInput
    
    # Pydantic fields for inter-process communication (excluded from schema)
    event_queue: Optional[Any] = Field(default=None, exclude=True)
    command_queue: Optional[Any] = Field(default=None, exclude=True)
    confirmation_results_dict: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, event_queue=None, command_queue=None, confirmation_results_dict=None, **kwargs):
        """Initialize file operations tool with event/command queues for inter-process communication."""
        super().__init__(event_queue=event_queue, command_queue=command_queue, confirmation_results_dict=confirmation_results_dict, **kwargs)
    
    def _resolve_folder_path(self, folder_name: str) -> str:
        """Resolve folder references like 'my desktop' to actual paths."""
        try:
            from distr.core.settings import resolve_folder_path
            return resolve_folder_path(folder_name)
        except Exception:
            # Fallback: use standard macOS paths
            home = os.path.expanduser("~")
            folder_lower = folder_name.lower()
            if 'desktop' in folder_lower:
                return os.path.join(home, 'Desktop')
            elif 'documents' in folder_lower:
                return os.path.join(home, 'Documents')
            elif 'downloads' in folder_lower:
                return os.path.join(home, 'Downloads')
            elif 'pictures' in folder_lower:
                return os.path.join(home, 'Pictures')
            elif 'music' in folder_lower:
                return os.path.join(home, 'Music')
            elif 'videos' in folder_lower:
                return os.path.join(home, 'Movies')
            return folder_name
    
    def _resolve_path(self, path: str) -> str:
        """Resolve path with folder references and dropped file references."""
        import re
        home = os.path.expanduser("~")
        
        path_lower = path.lower().strip()
        
        # Check for "the file I dropped" or similar references
        dropped_file_patterns = [
            r'the\s+file\s+i\s+(?:just\s+)?(?:dropped|gave\s+you|gave)',
            r'the\s+(?:last|most\s+recent)\s+(?:file|dropped\s+file)',
            r'that\s+file\s+i\s+dropped',
            r'my\s+(?:last|recent)\s+file'
        ]
        
        for pattern in dropped_file_patterns:
            if re.search(pattern, path_lower):
                last_file = self._get_last_dropped_file()
                if last_file:
                    logger.info(f"Resolved '{path}' to last dropped file: {last_file}")
                    return last_file
                else:
                    raise ValueError("No dropped files found. Please drop a file on the oracle ball first.")
        
        # Direct folder name mappings
        folder_map = {
            'desktop': os.path.join(home, 'Desktop'),
            'documents': os.path.join(home, 'Documents'),
            'downloads': os.path.join(home, 'Downloads'),
            'pictures': os.path.join(home, 'Pictures'),
            'music': os.path.join(home, 'Music'),
            'videos': os.path.join(home, 'Movies'),
        }
        
        # Check for "my X" pattern (only if path doesn't look like a full file path)
        # Don't match if path contains slashes (likely a full path) or file extensions
        is_likely_full_path = '/' in path or '\\' in path or '.' in os.path.basename(path) if '.' in path else False
        
        if not is_likely_full_path:
            for folder_name, folder_path in folder_map.items():
                if f'my {folder_name}' in path_lower:
                    return folder_path
                elif folder_name in path_lower and 'my' not in path_lower:
                    return folder_path
        
        # Try to resolve using settings utility
        try:
            resolved = self._resolve_folder_path(path)
            if resolved != path:  # If it was resolved
                return resolved
        except Exception:
            pass
        
        # Expand ~ if present
        if path.startswith('~'):
            return os.path.expanduser(path)
        
        # If no match, return as-is (might be a full path)
        return os.path.normpath(path)
    
    def _list_files(self, path: str) -> str:
        """List files in a directory. Returns conversational format for TTS."""
        try:
            resolved_path = self._resolve_path(path)
            if not os.path.exists(resolved_path):
                return f"The path {resolved_path} does not exist."
            
            if not os.path.isdir(resolved_path):
                return f"{resolved_path} is not a directory."
            
            files = []
            dirs = []
            try:
                for item in os.listdir(resolved_path):
                    item_path = os.path.join(resolved_path, item)
                    if os.path.isdir(item_path):
                        dirs.append(item)
                    else:
                        files.append(item)
            except PermissionError:
                return f"Permission denied accessing {resolved_path}."
            
            # Sort and format for conversational TTS
            dirs.sort()
            files.sort()
            
            # Create conversational summary for TTS
            total_items = len(dirs) + len(files)
            if total_items == 0:
                return f"Your {os.path.basename(resolved_path)} folder is empty."
            
            result_parts = []
            
            # List directories conversationally
            if dirs:
                if len(dirs) == 1:
                    result_parts.append(f"1 folder: {dirs[0]}")
                elif len(dirs) <= 3:
                    result_parts.append(f"{len(dirs)} folders: {', '.join(dirs)}")
                else:
                    result_parts.append(f"{len(dirs)} folders including {', '.join(dirs[:2])} and {len(dirs) - 2} more")
            
            # List files conversationally
            if files:
                if len(files) == 1:
                    result_parts.append(f"1 file: {files[0]}")
                elif len(files) <= 5:
                    result_parts.append(f"{len(files)} files: {', '.join(files)}")
                else:
                    # Show first 3-4 files, then summarize
                    file_list = ', '.join(files[:3])
                    result_parts.append(f"{len(files)} files including {file_list} and {len(files) - 3} more")
            
            return ". ".join(result_parts) + "."
        except Exception as e:
            logger.error(f"Error listing files: {e}", exc_info=True)
            return f"Error listing files: {str(e)}"
    
    def _create_file(self, path: str, content: Optional[str] = None) -> str:
        """Create a file with optional content."""
        try:
            resolved_path = self._resolve_path(path)
            
            # GUARDRAIL: Check and confirm if file creation would overwrite existing file
            import os
            if os.path.exists(resolved_path):
                logger.info(f"[FILE_OPERATIONS] Create operation would overwrite existing file: {resolved_path}")
                try:
                    from distr.core.agent.services.safety.interceptor import check_and_confirm_direct_file_operation
                    
                    allowed, plan = check_and_confirm_direct_file_operation(
                        operation_type='WRITE',
                        source_path=resolved_path,
                        task=f"Create/overwrite file {resolved_path}",
                        originating_pathway="file_operations_tool",
                        event_queue=getattr(self, 'event_queue', None),
                        command_queue=getattr(self, 'command_queue', None),
                        confirmation_results_dict=getattr(self, 'confirmation_results_dict', None)
                    )
                    
                    if not allowed:
                        logger.warning(f"[FILE_OPERATIONS] Create operation blocked by guardrail: {resolved_path}")
                        return "File creation was cancelled by user (would overwrite existing file)."
                    
                    logger.info(f"[FILE_OPERATIONS] Create operation confirmed - proceeding: {resolved_path}")
                except ImportError:
                    logger.warning(
                        "[FILE_OPERATIONS] File safety interceptor not available — blocking overwrite"
                    )
                    return _FILE_SAFETY_UNAVAILABLE
                except Exception as e:
                    logger.error(f"[FILE_OPERATIONS] Error in guardrail check: {e}", exc_info=True)
                    # On error, default to deny for safety
                    return f"File safety check failed - creation blocked for safety. Error: {str(e)}"
            
            # Create directory if needed
            os.makedirs(os.path.dirname(resolved_path), exist_ok=True)
            
            # Perform the file creation
            with open(resolved_path, 'w', encoding='utf-8') as f:
                if content:
                    f.write(content)
            
            logger.info(f"[FILE_OPERATIONS] File created successfully: {resolved_path}")
            return f"File created: {resolved_path}"
        except Exception as e:
            logger.error(f"Error creating file: {e}", exc_info=True)
            return f"Error creating file: {str(e)}"
    
    def _get_dropped_files(self) -> Optional[List[str]]:
        """Get files that were dropped on the oracle ball."""
        import json
        storage_dir = os.path.join(os.path.expanduser("~"), ".decisionsai", "dropped_files")
        storage_file = os.path.join(storage_dir, "current_files.json")
        
        if not os.path.exists(storage_file):
            return None
        
        try:
            with open(storage_file, 'r') as f:
                data = json.load(f)
                files = data.get("files", [])
                # Only return files that still exist
                existing_files = [f for f in files if os.path.exists(f)]
                return existing_files if existing_files else None
        except Exception as e:
            logger.error(f"Error reading dropped files: {e}")
            return None
    
    def _get_last_dropped_file(self) -> Optional[str]:
        """Get the most recently dropped file (last in the list)."""
        dropped_files = self._get_dropped_files()
        if not dropped_files:
            return None
        # Return the last file (most recently dropped)
        # Filter to only files (not directories)
        files_only = [f for f in dropped_files if os.path.isfile(f)]
        return files_only[-1] if files_only else None
    
    def _read_file(self, path: str) -> str:
        """Read file contents."""
        try:
            resolved_path = self._resolve_path(path)
            
            # Verify it exists
            if not os.path.exists(resolved_path):
                return f"Error: File does not exist: {resolved_path}"
            
            # Check if it's a directory (must check BEFORE trying to open as file)
            if os.path.isdir(resolved_path):
                return f"Error: {resolved_path} is a directory, not a file. Use 'list' operation to see files in a directory."
            
            # Verify it's actually a file (double-check)
            if not os.path.isfile(resolved_path):
                return f"Error: {resolved_path} exists but is not a file. It may be a directory or special file."
            
            # Try to read the file
            try:
                with open(resolved_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except UnicodeDecodeError:
                # Try with different encoding or binary mode
                return f"Error: Cannot read {resolved_path} as text file. It may be a binary file or use a different encoding."
            
            # Limit content length for TTS
            if len(content) > 5000:
                return f"File content (first 5000 chars):\n{content[:5000]}\n\n... (truncated, file is {len(content)} characters)"
            
            return f"File content:\n{content}"
        except Exception as e:
            logger.error(f"Error reading file: {e}", exc_info=True)
            return f"Error reading file: {str(e)}"
    
    def _delete_file(self, path: str) -> str:
        """Delete a single file (directories are refused — bulk-delete policy)."""
        try:
            resolved_path = self._resolve_path(path)
            if not os.path.exists(resolved_path):
                return f"Error: Path does not exist: {resolved_path}"

            if is_protected_library_root(resolved_path):
                logger.warning(
                    "[FILE_OPERATIONS] Delete blocked — protected library/home root: %s",
                    resolved_path,
                )
                return refusal_delete_library_root(resolved_path)

            if os.path.isdir(resolved_path):
                logger.warning(
                    "[FILE_OPERATIONS] Delete blocked — directory delete disabled (bulk policy): %s",
                    resolved_path,
                )
                return REFUSAL_TOOL_DIRECTORY_DELETE
            
            # GUARDRAIL: Check and confirm before deletion
            logger.info(f"[FILE_OPERATIONS] Delete operation requested: {resolved_path}")
            try:
                from distr.core.agent.services.safety.interceptor import check_and_confirm_direct_file_operation
                
                allowed, plan = check_and_confirm_direct_file_operation(
                    operation_type='DELETE',
                    source_path=resolved_path,
                    task=f"Delete {resolved_path}",
                    originating_pathway="file_operations_tool",
                    event_queue=getattr(self, 'event_queue', None),
                    command_queue=getattr(self, 'command_queue', None),
                    confirmation_results_dict=getattr(self, 'confirmation_results_dict', None)
                )
                
                if not allowed:
                    logger.warning(f"[FILE_OPERATIONS] Delete operation blocked by guardrail: {resolved_path}")
                    # Log operation cancellation
                    try:
                        from distr.core.files.safety import get_file_safety
                        file_safety = get_file_safety()
                        file_safety.log_operation('operation_cancelled', {
                            'operation_type': 'DELETE',
                            'target_path': resolved_path,
                            'originating_pathway': 'file_operations_tool',
                            'result': 'cancelled_by_user',
                            'reason': 'User cancelled in confirmation dialog'
                        })
                    except Exception:
                        pass
                    return "File deletion was cancelled by user."
                
                logger.info(f"[FILE_OPERATIONS] Delete operation confirmed - proceeding: {resolved_path}")
            except ImportError:
                logger.warning(
                    "[FILE_OPERATIONS] File safety interceptor not available — blocking delete"
                )
                return _FILE_SAFETY_UNAVAILABLE
            except Exception as e:
                logger.error(f"[FILE_OPERATIONS] Error in guardrail check: {e}", exc_info=True)
                # On error, default to deny for safety
                return f"File safety check failed - deletion blocked for safety. Error: {str(e)}"
            
            # Perform the deletion (files only — directories rejected earlier)
            os.remove(resolved_path)
            logger.info(f"[FILE_OPERATIONS] File deleted successfully: {resolved_path}")
            try:
                from distr.core.files.safety import get_file_safety
                file_safety = get_file_safety()
                file_safety.log_operation('operation_executed', {
                    'operation_type': 'DELETE',
                    'target_path': resolved_path,
                    'originating_pathway': 'file_operations_tool',
                    'result': 'success',
                    'was_directory': False
                })
            except Exception:
                pass
            return f"File deleted: {resolved_path}"
        except Exception as e:
            logger.error(f"Error deleting: {e}", exc_info=True)
            return f"Error deleting: {str(e)}"
    
    def _copy_file(self, source: str, destination: str) -> str:
        """Copy a file or directory."""
        try:
            source_path = self._resolve_path(source)
            dest_path = self._resolve_path(destination)
            
            if not os.path.exists(source_path):
                return f"Error: Source does not exist: {source_path}"
            
            # GUARDRAIL: Check and confirm if copy would overwrite
            import os
            if os.path.exists(dest_path):
                logger.info(f"[FILE_OPERATIONS] Copy operation would overwrite: {source_path} -> {dest_path}")
                try:
                    from distr.core.agent.services.safety.interceptor import check_and_confirm_direct_file_operation
                    
                    allowed, plan = check_and_confirm_direct_file_operation(
                        operation_type='COPY',
                        source_path=source_path,
                        destination_path=dest_path,
                        task=f"Copy {source_path} to {dest_path} (will overwrite)",
                        originating_pathway="file_operations_tool",
                        event_queue=getattr(self, 'event_queue', None),
                        command_queue=getattr(self, 'command_queue', None),
                        confirmation_results_dict=getattr(self, 'confirmation_results_dict', None)
                    )
                    
                    if not allowed:
                        logger.warning(f"[FILE_OPERATIONS] Copy operation blocked by guardrail: {source_path} -> {dest_path}")
                        return "File copy was cancelled by user (would overwrite existing file)."
                    
                    logger.info(f"[FILE_OPERATIONS] Copy operation confirmed - proceeding: {source_path} -> {dest_path}")
                except ImportError:
                    logger.warning(
                        "[FILE_OPERATIONS] File safety interceptor not available — blocking copy overwrite"
                    )
                    return _FILE_SAFETY_UNAVAILABLE
                except Exception as e:
                    logger.error(f"[FILE_OPERATIONS] Error in guardrail check: {e}", exc_info=True)
                    # On error, default to deny for safety
                    return f"File safety check failed - copy blocked for safety. Error: {str(e)}"
            
            # Perform the copy
            if os.path.isdir(source_path):
                import shutil
                shutil.copytree(source_path, dest_path, dirs_exist_ok=True)
                logger.info(f"[FILE_OPERATIONS] Directory copied successfully: {source_path} -> {dest_path}")
                return f"Directory copied: {source_path} -> {dest_path}"
            else:
                import shutil
                shutil.copy2(source_path, dest_path)
                logger.info(f"[FILE_OPERATIONS] File copied successfully: {source_path} -> {dest_path}")
                return f"File copied: {source_path} -> {dest_path}"
        except Exception as e:
            logger.error(f"Error copying: {e}", exc_info=True)
            return f"Error copying: {str(e)}"
    
    def _move_file(self, source: str, destination: str) -> str:
        """Move a file or directory."""
        try:
            source_path = self._resolve_path(source)
            dest_path = self._resolve_path(destination)
            
            if not os.path.exists(source_path):
                return f"Error: Source does not exist: {source_path}"

            if is_protected_library_root(source_path):
                logger.warning(
                    "[FILE_OPERATIONS] Move blocked — source is protected library/home root: %s",
                    source_path,
                )
                return refusal_protected_library_root(source_path, "move this folder")
            
            # GUARDRAIL: Check and confirm before move (destructive operation)
            logger.info(f"[FILE_OPERATIONS] Move operation requested: {source_path} -> {dest_path}")
            try:
                from distr.core.agent.services.safety.interceptor import check_and_confirm_direct_file_operation
                
                allowed, plan = check_and_confirm_direct_file_operation(
                    operation_type='MOVE',
                    source_path=source_path,
                    destination_path=dest_path,
                    task=f"Move {source_path} to {dest_path}",
                    originating_pathway="file_operations_tool",
                    event_queue=getattr(self, 'event_queue', None),
                    command_queue=getattr(self, 'command_queue', None),
                    confirmation_results_dict=getattr(self, 'confirmation_results_dict', None)
                )
                
                if not allowed:
                    logger.warning(f"[FILE_OPERATIONS] Move operation blocked by guardrail: {source_path} -> {dest_path}")
                    # Log operation cancellation
                    try:
                        from distr.core.files.safety import get_file_safety
                        file_safety = get_file_safety()
                        file_safety.log_operation('operation_cancelled', {
                            'operation_type': 'MOVE',
                            'target_path': source_path,
                            'destination_path': dest_path,
                            'originating_pathway': 'file_operations_tool',
                            'result': 'cancelled_by_user',
                            'reason': 'User cancelled in confirmation dialog'
                        })
                    except Exception:
                        pass
                    return "File move was cancelled by user."
                
                logger.info(f"[FILE_OPERATIONS] Move operation confirmed - proceeding: {source_path} -> {dest_path}")
            except ImportError:
                logger.warning(
                    "[FILE_OPERATIONS] File safety interceptor not available — blocking move"
                )
                return _FILE_SAFETY_UNAVAILABLE
            except Exception as e:
                logger.error(f"[FILE_OPERATIONS] Error in guardrail check: {e}", exc_info=True)
                # On error, default to deny for safety
                return f"File safety check failed - move blocked for safety. Error: {str(e)}"
            
            # Perform the move
            import shutil
            shutil.move(source_path, dest_path)
            logger.info(f"[FILE_OPERATIONS] File moved successfully: {source_path} -> {dest_path}")
            # Log operation completion
            try:
                from distr.core.files.safety import get_file_safety
                file_safety = get_file_safety()
                file_safety.log_operation('operation_executed', {
                    'operation_type': 'MOVE',
                    'target_path': source_path,
                    'destination_path': dest_path,
                    'originating_pathway': 'file_operations_tool',
                    'result': 'success'
                })
            except Exception:
                pass
            return f"Moved: {source_path} -> {dest_path}"
        except Exception as e:
            logger.error(f"Error moving: {e}", exc_info=True)
            return f"Error moving: {str(e)}"
    
    def _run(self, operation: str, path: str, content: Optional[str] = None, destination: Optional[str] = None, **kwargs) -> str:
        """Execute file operation."""
        operation = operation.lower()
        
        if operation == 'list':
            return self._list_files(path)
        elif operation == 'create':
            return self._create_file(path, content)
        elif operation == 'read':
            return self._read_file(path)
        elif operation == 'delete':
            return self._delete_file(path)
        elif operation == 'copy':
            if not destination:
                return "Error: destination required for copy operation"
            return self._copy_file(path, destination)
        elif operation == 'move':
            if not destination:
                return "Error: destination required for move operation"
            return self._move_file(path, destination)
        else:
            return f"Error: Unknown operation '{operation}'. Supported: list, create, read, delete, copy, move"
    
    async def _arun(self, operation: str, path: str, content: Optional[str] = None, destination: Optional[str] = None, **kwargs) -> str:
        """Async version of _run."""
        return self._run(operation=operation, path=path, content=content, destination=destination, **kwargs)
    
    def get_triggers(self) -> List[str]:
        """Get trigger phrases that indicate this tool should be used."""
        return [
            "list files",
            "list the files",
            "show files",
            "what files",
            "files in",
            "files on"
        ]

