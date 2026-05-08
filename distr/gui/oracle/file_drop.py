"""File-drop handling mixin for OracleWindow.

Handles drag-and-drop of files/folders onto the oracle sphere,
including RAG indexing, per-chat file association, and agent notification.
"""

import logging
import os

logger = logging.getLogger(__name__)


class FileDropMixin:
    """Handles file/folder drag-and-drop onto the Oracle window."""

    def dragEnterEvent(self, event):
        """Handle drag enter event - accept file drops."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            logger.debug(f"[DROP] dragEnterEvent: Accepted drag with {len(event.mimeData().urls())} URL(s)")

    def dragMoveEvent(self, event):
        """Handle drag move event - keep accepting drop as mouse moves over widget."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def _get_file_metadata(self, file_path):
        """Get comprehensive metadata for a file (wrapper around utility function)."""
        from distr.core.files.metadata import get_file_metadata
        return get_file_metadata(file_path)

    def dropEvent(self, event):
        """Handle file/directory drop events and make them available to file operations and execute_code tools."""
        import time
        import threading
        from PyQt6.QtCore import QThread
        
        # Accept the drop action
        event.acceptProposedAction()
        
        drop_start_time = time.time()
        current_thread = threading.current_thread().name
        logger.info(f"[DROP] dropEvent started on thread: {current_thread} (UI thread: {QThread.currentThread() == self.thread()})")
        
        dropped_paths = []
        dropped_folders = []  # Track folders separately for display
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if os.path.isfile(file_path):
                logger.info(f"[DROP] File: {os.path.basename(file_path)}")
                dropped_paths.append(file_path)
            elif os.path.isdir(file_path):
                logger.info(f"[DROP] Folder: {os.path.basename(file_path)}")
                # Store the folder path for display (not individual files)
                dropped_folders.append(file_path)
                # Recursively find all files in the directory for RAG indexing
                folder_files = self._get_all_files_in_directory(file_path)
                dropped_paths.extend(folder_files)
                logger.info(f"[DROP] Indexing {len(folder_files)} files from folder")
        
        # Store dropped files for file operations and execute_code tools
        if dropped_paths or dropped_folders:
            # Trigger light purple glow for 2 seconds to indicate successful drop
            self._trigger_drop_success_glow()
            
            # CRITICAL: Move file I/O to background thread to avoid blocking UI
            # This prevents the spinner/timer from freezing during file operations
            def store_files_async():
                thread_name = threading.current_thread().name
                logger.info(f"[DROP] _store_dropped_files started on thread: {thread_name}")
                store_start = time.time()
                try:
                    self._store_dropped_files(dropped_paths, dropped_folders)
                    store_duration = time.time() - store_start
                    logger.info(f"[DROP] _store_dropped_files completed in {store_duration:.3f}s on thread: {thread_name}")
                except Exception as e:
                    logger.error(f"[DROP] Error in _store_dropped_files: {e}", exc_info=True)
            
            # Run in background thread to avoid blocking UI
            # This ensures dropEvent returns immediately and UI remains responsive
            threading.Thread(target=store_files_async, daemon=True).start()
            drop_duration = time.time() - drop_start_time
            logger.info(f"[DROP] dropEvent completed (async) in {drop_duration:.3f}s, file storage running in background")

    def _get_all_files_in_directory(self, directory_path):
        """Recursively get all files in a directory."""
        all_files = []
        try:
            for root, dirs, files in os.walk(directory_path):
                # Skip hidden directories (like .git, .DS_Store, etc.)
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for file in files:
                    # Skip hidden files
                    if not file.startswith('.'):
                        file_path = os.path.join(root, file)
                        all_files.append(file_path)
        except Exception as e:
            logger.warning(f"Error reading directory {directory_path}: {e}")
        return all_files

    def _store_dropped_files(self, file_paths, dropped_folders=None):
        """Store dropped file paths for file operations and execute_code tools to access and index them in RAG.
        
        For display purposes: Only folders are shown (not individual files from folders).
        For RAG indexing: Folders can be indexed on-demand using IndexFolderTool when needed.
        
        NOTE: This method may run on a background thread to avoid blocking the UI.
        File I/O operations are performed here, and RAG indexing is started in a separate thread.
        
        Args:
            file_paths: List of individual file paths (for RAG indexing)
            dropped_folders: List of folder paths that were dropped (for display in context)
        """
        if dropped_folders is None:
            dropped_folders = []
        import json
        import time
        import threading
        
        store_start_time = time.time()
        current_thread = threading.current_thread().name
        logger.info(f"[DROP] _store_dropped_files started on thread: {current_thread}")
        
        storage_dir = os.path.join(os.path.expanduser("~"), ".decisions", "dropped_files")
        os.makedirs(storage_dir, exist_ok=True)
        
        # Separate audio files from other files
        audio_extensions = {'.mp3', '.m4a', '.wav', '.flac', '.ogg', '.opus', '.aac', '.m4b', '.wma'}
        audio_files = []
        other_files = []
        
        for path in file_paths:
            if os.path.isfile(path):
                file_ext = os.path.splitext(path)[1].lower()
                if file_ext in audio_extensions:
                    audio_files.append(path)
                else:
                    other_files.append(path)
            else:
                other_files.append(path)
        
        storage_file = os.path.join(storage_dir, "current_files.json")
        indexed_files = []
        indexed_directories = []
        
        try:
            # Read existing files to append new ones (don't replace)
            existing_files = []
            existing_audio_files = []
            existing_other_files = []
            existing_folders = []  # Track dropped folders separately for display
            file_timestamps = {}  # Map file path -> timestamp when dropped
            folder_timestamps = {}  # Map folder path -> timestamp when dropped
            chat_files_index = {}  # Per-chat dropped files/folders buckets
            existing_data = {}  # Initialize to avoid NameError
            
            if os.path.exists(storage_file):
                try:
                    with open(storage_file, 'r') as f:
                        existing_data = json.load(f)
                        existing_files = existing_data.get("files", [])
                        existing_audio_files = existing_data.get("audio_files", [])
                        existing_other_files = existing_data.get("other_files", [])
                        existing_folders = existing_data.get("dropped_folders", [])  # Load existing folders
                        file_timestamps = existing_data.get("file_timestamps", {})  # Load existing timestamps
                        folder_timestamps = existing_data.get("folder_timestamps", {})  # Load existing folder timestamps
                        chat_files_index = existing_data.get("chat_files_index", {})
                except Exception as e:
                    logger.warning(f"Error reading existing dropped files: {e}")
                    existing_data = {}  # Reset on error
            
            # Append new files to existing ones (avoid duplicates)
            all_files = list(existing_files)
            all_audio_files = list(existing_audio_files)
            all_other_files = list(existing_other_files)
            all_folders = list(existing_folders)  # Track folders separately
            current_time = time.time()
            
            # Add dropped folders (for display in context)
            for folder_path in dropped_folders:
                if folder_path not in all_folders:
                    all_folders.append(folder_path)
                    folder_timestamps[folder_path] = current_time
                else:
                    # Folder already exists - update timestamp to reflect most recent drop
                    folder_timestamps[folder_path] = current_time
                    logger.debug(f"[DROP] Updated timestamp for existing folder: {os.path.basename(folder_path)}")
            
            # Add individual files (for RAG indexing - files from folders are included here)
            for path in file_paths:
                if path not in all_files:
                    all_files.append(path)  # Append new files to the end (most recent last)
                    file_timestamps[path] = current_time  # Store timestamp for this file
                else:
                    # File already exists - update timestamp to reflect most recent drop
                    file_timestamps[path] = current_time
                    logger.debug(f"[DROP] Updated timestamp for existing file: {os.path.basename(path)}")
            
            for audio_path in audio_files:
                if audio_path not in all_audio_files:
                    all_audio_files.append(audio_path)
                    if audio_path not in file_timestamps:
                        file_timestamps[audio_path] = current_time
            
            for other_path in other_files:
                if other_path not in all_other_files:
                    all_other_files.append(other_path)
                    if other_path not in file_timestamps:
                        file_timestamps[other_path] = current_time
            
            # Get current chat ID to associate files with this chat
            # CRITICAL: We MUST have a valid chat_id to associate files, otherwise they won't appear in any chat's context
            current_chat_id = None
            if hasattr(self, 'chat_manager') and self.chat_manager:
                try:
                    current_chat_id = self.chat_manager.get_current_chat()
                    if current_chat_id is None:
                        # If no current chat, try to get the last chat from settings
                        logger.warning("[DROP] No current chat_id - attempting to get last chat from settings")
                        try:
                            from distr.core.db import get_session, Settings
                            session = get_session()
                            try:
                                settings = session.query(Settings).first()
                                if settings and hasattr(settings, 'last_chat_id') and settings.last_chat_id:
                                    current_chat_id = settings.last_chat_id
                                    logger.info(f"[DROP] Using last_chat_id from settings: {current_chat_id}")
                                else:
                                    # Last resort: create a new chat
                                    logger.warning("[DROP] No last_chat_id in settings - creating new chat for dropped files")
                                    current_chat_id = self.chat_manager.create_chat("New Conversation", is_new=True)
                                    logger.info(f"[DROP] Created new chat {current_chat_id} for dropped files")
                                    
                                    # FIX: Save this as the last active chat so subsequent voice commands use it
                                    try:
                                        # Re-query settings to ensure we have a fresh object attached to session
                                        settings = session.query(Settings).first()
                                        if settings:
                                            settings.last_chat_id = current_chat_id
                                            session.commit()
                                            logger.info(f"[DROP] Updated settings.last_chat_id to {current_chat_id}")
                                    except Exception as e:
                                        logger.error(f"[DROP] Failed to update settings.last_chat_id: {e}")
                            finally:
                                session.close()
                        except Exception as e:
                            logger.error(f"[DROP] Error getting/creating chat_id: {e}", exc_info=True)
                    else:
                        logger.info(f"[DROP] Using current chat_id: {current_chat_id}")
                except Exception as e:
                    logger.error(f"[DROP] Error getting current_chat_id: {e}", exc_info=True)
            
            # Store file-to-chat mapping (which chats each file was dropped in)
            # CRITICAL: Files can be associated with MULTIPLE chats - each chat_id is stored in a list
            file_chat_mapping = existing_data.get("file_chat_mapping", {}) if os.path.exists(storage_file) else {}
            
            # Associate new files with current chat
            logger.info(f"[DROP] Associating {len(file_paths)} file(s) with chat_id={current_chat_id}")
            if not current_chat_id:
                logger.error(f"[DROP] CRITICAL: No current_chat_id available - files will NOT be associated with any chat!")
            for path in file_paths:
                file_name = os.path.basename(path)
                if not current_chat_id:
                    logger.error(f"[DROP] ❌ Cannot associate file '{file_name}' - no current_chat_id available - file will NOT appear in any chat's context!")
                    continue
                
                # Get existing chat_ids for this file (can be a list or a single chat_id for backwards compatibility)
                existing_chat_ids = file_chat_mapping.get(path, [])
                if not isinstance(existing_chat_ids, list):
                    # Backwards compatibility: convert single chat_id to list
                    existing_chat_ids = [existing_chat_ids] if existing_chat_ids else []
                
                # Add current chat_id if not already present
                if current_chat_id not in existing_chat_ids:
                    existing_chat_ids.append(current_chat_id)
                    file_chat_mapping[path] = existing_chat_ids
                    logger.info(f"[DROP] ✅ Associated file '{file_name}' with chat_id={current_chat_id} (now visible in {len(existing_chat_ids)} chat(s): {existing_chat_ids})")
                else:
                    logger.debug(f"[DROP] File '{file_name}' already associated with chat_id={current_chat_id} (visible in {len(existing_chat_ids)} chat(s): {existing_chat_ids})")
            
            # Store file-to-chat mapping for folders too
            # CRITICAL: Folders can be associated with MULTIPLE chats - each chat_id is stored in a list
            # CRITICAL: When a folder is dropped, ALL subfolders inside it must ALSO be associated with the current chat
            folder_chat_mapping = existing_data.get("folder_chat_mapping", {}) if os.path.exists(storage_file) else {}
            
            def associate_folder_with_chat(folder_path, chat_id, mapping):
                """Associate a folder and ALL its subfolders with the given chat_id."""
                folders_updated = []
                
                # Normalize path - remove trailing slashes for consistent storage
                normalized_folder = folder_path.rstrip('/').rstrip('\\')
                
                # Associate the folder itself
                folder_name = os.path.basename(normalized_folder) if os.path.basename(normalized_folder) else normalized_folder
                existing_chat_ids = mapping.get(normalized_folder, [])
                if not isinstance(existing_chat_ids, list):
                    existing_chat_ids = [existing_chat_ids] if existing_chat_ids else []
                
                if chat_id not in existing_chat_ids:
                    existing_chat_ids.append(chat_id)
                    mapping[normalized_folder] = existing_chat_ids
                    folders_updated.append(normalized_folder)
                
                # Recursively associate all subfolders
                try:
                    for root, dirs, _ in os.walk(folder_path):
                        for dir_name in dirs:
                            subfolder_path = os.path.join(root, dir_name)
                            # Normalize subfolder path too
                            normalized_subfolder = subfolder_path.rstrip('/').rstrip('\\')
                            existing_ids = mapping.get(normalized_subfolder, [])
                            if not isinstance(existing_ids, list):
                                existing_ids = [existing_ids] if existing_ids else []
                            
                            if chat_id not in existing_ids:
                                existing_ids.append(chat_id)
                                mapping[normalized_subfolder] = existing_ids
                                folders_updated.append(normalized_subfolder)
                except Exception as e:
                    logger.warning(f"[DROP] Error walking folder {folder_path}: {e}")
                
                return folders_updated
            
            for folder_path in dropped_folders:
                folder_name = os.path.basename(folder_path) if os.path.basename(folder_path) else folder_path
                if not current_chat_id:
                    logger.error(f"[DROP] ❌ Cannot associate folder '{folder_name}' - no current_chat_id available - folder will NOT appear in any chat's context!")
                    continue
                
                # Associate this folder AND all subfolders with the current chat
                updated_folders = associate_folder_with_chat(folder_path, current_chat_id, folder_chat_mapping)
                if updated_folders:
                    logger.info(f"[DROP] ✅ Associated folder '{folder_name}' + {len(updated_folders)-1} subfolder(s) with chat_id={current_chat_id}")
                else:
                    logger.debug(f"[DROP] Folder '{folder_name}' and subfolders already associated with chat_id={current_chat_id}")

            # Build/refresh per-chat dropped-files bucket so each chat has its own list.
            chat_key = str(current_chat_id) if current_chat_id else None
            if chat_key:
                chat_bucket = chat_files_index.get(chat_key, {})
                chat_bucket_files = list(chat_bucket.get("files", []))
                chat_bucket_audio_files = list(chat_bucket.get("audio_files", []))
                chat_bucket_other_files = list(chat_bucket.get("other_files", []))
                chat_bucket_folders = list(chat_bucket.get("dropped_folders", []))

                for path in file_paths:
                    if path not in chat_bucket_files:
                        chat_bucket_files.append(path)
                for path in audio_files:
                    if path not in chat_bucket_audio_files:
                        chat_bucket_audio_files.append(path)
                for path in other_files:
                    if path not in chat_bucket_other_files:
                        chat_bucket_other_files.append(path)
                for path in dropped_folders:
                    if path not in chat_bucket_folders:
                        chat_bucket_folders.append(path)

                # Keep each chat bucket bounded (most recent items at end).
                MAX_CHAT_FILES = 2000
                MAX_CHAT_AUDIO = 400
                MAX_CHAT_OTHER = 2000
                MAX_CHAT_FOLDERS = 200
                chat_bucket_files = chat_bucket_files[-MAX_CHAT_FILES:]
                chat_bucket_audio_files = chat_bucket_audio_files[-MAX_CHAT_AUDIO:]
                chat_bucket_other_files = chat_bucket_other_files[-MAX_CHAT_OTHER:]
                chat_bucket_folders = chat_bucket_folders[-MAX_CHAT_FOLDERS:]

                chat_files_index[chat_key] = {
                    "files": chat_bucket_files,
                    "audio_files": chat_bucket_audio_files,
                    "other_files": chat_bucket_other_files,
                    "dropped_folders": chat_bucket_folders,
                    "updated_at": current_time,
                }

            # Keep global lists bounded to avoid unbounded growth.
            MAX_GLOBAL_FILES = 5000
            MAX_GLOBAL_AUDIO = 1000
            MAX_GLOBAL_OTHER = 5000
            MAX_GLOBAL_FOLDERS = 500
            all_files = sorted(set(all_files), key=lambda p: file_timestamps.get(p, 0))[-MAX_GLOBAL_FILES:]
            all_audio_files = sorted(set(all_audio_files), key=lambda p: file_timestamps.get(p, 0))[-MAX_GLOBAL_AUDIO:]
            all_other_files = sorted(set(all_other_files), key=lambda p: file_timestamps.get(p, 0))[-MAX_GLOBAL_OTHER:]
            all_folders = sorted(set(all_folders), key=lambda p: folder_timestamps.get(p, 0))[-MAX_GLOBAL_FOLDERS:]
            keep_files_set = set(all_files)
            keep_folders_set = set(all_folders)
            file_timestamps = {k: v for k, v in file_timestamps.items() if k in keep_files_set}
            folder_timestamps = {k: v for k, v in folder_timestamps.items() if k in keep_folders_set}
            file_chat_mapping = {k: v for k, v in file_chat_mapping.items() if k in keep_files_set}
            folder_chat_mapping = {k: v for k, v in folder_chat_mapping.items() if k in keep_folders_set}
            
            # Store ALL files (existing + new) in the storage file with timestamps and chat mapping
            # CRITICAL: Ensure file is written and flushed to disk BEFORE sending notification
            with open(storage_file, 'w') as f:
                json.dump({
                    "files": all_files,  # ALL files (existing + new, most recent last) - for RAG indexing
                    "dropped_folders": all_folders,  # Folders that were dropped (for display in context)
                    "audio_files": all_audio_files,  # Track audio files separately
                    "other_files": all_other_files,
                    "file_timestamps": file_timestamps,  # Map of file path -> timestamp
                    "folder_timestamps": folder_timestamps,  # Map of folder path -> timestamp
                    "file_chat_mapping": file_chat_mapping,  # Map of file path -> chat_id
                    "folder_chat_mapping": folder_chat_mapping,  # Map of folder path -> chat_id
                    "chat_files_index": chat_files_index,  # Per-chat buckets for dropped file context
                    "timestamp": current_time  # Last update timestamp
                }, f, indent=2)
                f.flush()  # CRITICAL: Flush to disk immediately
                os.fsync(f.fileno())  # CRITICAL: Force OS to write to disk (Unix/Mac)
            logger.info(f"Stored {len(file_paths)} new dropped file(s) ({len(audio_files)} audio, {len(other_files)} other). Total files now: {len(all_files)}")
            if dropped_folders:
                logger.info(f"Stored {len(dropped_folders)} dropped folder(s): {dropped_folders}")
            
            # CRITICAL: Verify file was written before sending notification
            # This prevents race condition where notification is sent before file is on disk
            import time
            verify_start = time.time()
            max_verify_time = 2.0  # Wait up to 2 seconds
            file_verified = False
            while time.time() - verify_start < max_verify_time:
                if os.path.exists(storage_file):
                    try:
                        # Try to read it back to verify it's valid JSON
                        with open(storage_file, 'r') as verify_f:
                            verify_data = json.load(verify_f)
                            # Check that our folders are actually in the file
                            stored_folders = verify_data.get("dropped_folders", [])
                            if dropped_folders:
                                # Verify at least one of our dropped folders is in the stored data
                                if any(folder in stored_folders for folder in dropped_folders):
                                    file_verified = True
                                    logger.info(f"✅ Verified dropped folders written to disk: {dropped_folders}")
                                    break
                            else:
                                # No folders dropped, just verify file exists and is valid
                                file_verified = True
                                break
                    except (json.JSONDecodeError, IOError) as e:
                        logger.warning(f"File verification failed (will retry): {e}")
                time.sleep(0.1)  # Wait 100ms before retry
            
            if not file_verified:
                logger.error(f"⚠️ WARNING: Could not verify dropped folders were written to disk after {max_verify_time}s. Notification may be sent with stale data.")
            else:
                verify_duration = time.time() - verify_start
                logger.info(f"File verified in {verify_duration:.3f}s")
            
            # NOTE: Indexing is now on-demand via IndexFolderTool instead of automatic
            # This prevents excessive processing when folders are dropped and gives the LLM
            # better context about folder structure before indexing is requested.
            # The LLM can call IndexFolderTool when the user wants to search or query folder contents.
            
            # Dropped files are stored in current_files.json and added to chat history via notification
            # Global RAG index (from Advanced Settings) is separate and persistent across all chats
            
            # Notify agent about the JUST-DROPPED items only (not the entire chat history).
            # Include a brief summary of previously dropped items so the agent can still reference them.
            previous_folders = []
            previous_files = []
            if current_chat_id is not None:
                chat_bucket = chat_files_index.get(str(current_chat_id), {})
                previous_folders = [f for f in chat_bucket.get("dropped_folders", []) if f not in dropped_folders]
                previous_files = [f for f in chat_bucket.get("files", []) if f not in file_paths]
            self._notify_agent_files_dropped(dropped_folders, file_paths, file_timestamps, folder_timestamps,
                                             previous_folders, previous_files)
            
        except Exception as e:
            logger.error(f"Error storing dropped files: {e}")
            # Even on error, try to notify about what we have
            try:
                # Fallback: use empty dicts if timestamps weren't created
                fallback_file_ts = {}
                fallback_folder_ts = {}
                self._notify_agent_files_dropped(dropped_folders or [], file_paths or [], fallback_file_ts, fallback_folder_ts)
            except Exception:
                pass

    def _notify_agent_files_dropped(self, dropped_folders, all_files, file_timestamps, folder_timestamps,
                                     previous_folders=None, previous_files=None):
        """Notify the agent that files/folders have been dropped and are available.
        
        This method adds dropped files/folders to the chat history/context.
        Focus is on the just-dropped items, with a brief summary of previously dropped items.
        
        Args:
            dropped_folders: List of folder paths that were just dropped
            all_files: All individual files just dropped
            file_timestamps: Map of file path -> timestamp
            folder_timestamps: Map of folder path -> timestamp
            previous_folders: Previously dropped folders in this chat (for context)
            previous_files: Previously dropped files in this chat (for context)
        """
        if previous_folders is None:
            previous_folders = []
        if previous_files is None:
            previous_files = []
        # Combine folders and individual files (files not inside folders) for display
        # Filter out files that are inside dropped folders
        individual_files = []
        for f in all_files:
            # Check if this file is inside any dropped folder
            is_inside_folder = False
            for folder in dropped_folders:
                try:
                    normalized_file = os.path.normpath(os.path.abspath(f))
                    normalized_folder = os.path.normpath(os.path.abspath(folder))
                    if normalized_file.startswith(normalized_folder + os.sep) or normalized_file == normalized_folder:
                        is_inside_folder = True
                        break
                except (ValueError, OSError):
                    pass
            # Only include files that are NOT inside a dropped folder
            if not is_inside_folder and os.path.exists(f):
                individual_files.append(f)
        
        # Combine folders and individual files for display
        items_to_show = dropped_folders + individual_files
        if not items_to_show:
            return  # Nothing to notify about
        
        try:
            # Format timestamp helper
            def format_timestamp(ts):
                if not ts:
                    return "unknown time"
                from datetime import datetime
                dt = datetime.fromtimestamp(ts)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            
            # Build notification message — only the JUST-DROPPED items, not historical drops
            notification_parts = []
            folder_count = len(dropped_folders)
            file_count = len(individual_files)
            if folder_count > 0 and file_count > 0:
                notification_parts.append(f"📎 {folder_count} folder(s) and {file_count} file(s) just dropped on Oracle:")
            elif folder_count > 0:
                notification_parts.append(f"📎 {folder_count} folder(s) just dropped on Oracle:")
            else:
                notification_parts.append(f"📎 {file_count} file(s) just dropped on Oracle:")
            
            for i, item_path in enumerate(items_to_show, 1):
                item_ts = file_timestamps.get(item_path) or folder_timestamps.get(item_path)
                timestamp_str = format_timestamp(item_ts) if item_ts else "just now"
                
                if os.path.isdir(item_path):
                    folder_name = os.path.basename(item_path)
                    notification_parts.append(f"  [{i}] 📁 {folder_name} ({item_path})")
                elif os.path.isfile(item_path):
                    metadata = self._get_file_metadata(item_path)
                    info_parts = []
                    info_parts.append(f"Size: {metadata['size_human']}")
                    if metadata['type_description'] != 'unknown':
                        info_parts.append(f"Type: {metadata['type_description']}")
                    file_info = " | ".join(info_parts)
                    notification_parts.append(f"  [{i}] 📄 {os.path.basename(item_path)} ({item_path}) - {file_info}")
            
            notification_parts.append("")
            notification_parts.append("⚡ Focus on these just-dropped items. Ask the user what they'd like to do with them.")
            notification_parts.append("  - Access via execute_code or file_operations tools")
            notification_parts.append("  - Index folders on-demand using IndexFolderTool for semantic search")

            # Summarize previously dropped items so the agent can still reference them
            prev_folder_names = [os.path.basename(f) or f for f in previous_folders if os.path.exists(f)]
            prev_file_names = [os.path.basename(f) for f in previous_files if os.path.isfile(f)]
            # Filter out files that live inside previous folders (avoid noise)
            prev_file_names_filtered = []
            for f in previous_files:
                if not os.path.isfile(f):
                    continue
                inside = False
                for pf in previous_folders:
                    try:
                        if os.path.normpath(os.path.abspath(f)).startswith(os.path.normpath(os.path.abspath(pf)) + os.sep):
                            inside = True
                            break
                    except (ValueError, OSError):
                        pass
                if not inside:
                    prev_file_names_filtered.append(os.path.basename(f))
            if prev_folder_names or prev_file_names_filtered:
                notification_parts.append("")
                notification_parts.append("📂 Previously dropped in this chat (still accessible):")
                for name in prev_folder_names[:20]:
                    notification_parts.append(f"  📁 {name}")
                for name in prev_file_names_filtered[:20]:
                    notification_parts.append(f"  📄 {name}")
                remaining = max(0, len(prev_folder_names) - 20) + max(0, len(prev_file_names_filtered) - 20)
                if remaining > 0:
                    notification_parts.append(f"  ... and {remaining} more")

            # Check if there's an active project and inform the agent
            try:
                from distr.core.agent.services.rag.project import get_active_project
                active_project = get_active_project()
                if active_project:
                    notification_parts.append(f"\n🎯 **ACTIVE PROJECT DETECTED:** {active_project['name']}")
                    notification_parts.append(f"  - If the user says these files/folders are 'for the project', use AddFilesToProjectTool")
                    notification_parts.append(f"  - If the user says a folder is a project, use CreateProjectFromFolderTool")
                    notification_parts.append(f"  - Project folder: {active_project.get('folder_location', 'Not set')}")
            except Exception as e:
                logger.debug(f"Could not check for active project: {e}")
            
            notification_message = "\n".join(notification_parts)
            
            from distr.core.signals import signal_manager
            from PyQt6 import QtWidgets

            # Add to chat if chat_manager is available (save to database but hide from UI)
            if hasattr(self, 'chat_manager') and self.chat_manager:
                try:
                    current_chat = self.chat_manager.get_current_chat()
                    if current_chat:
                        # Add as a hidden assistant message to the chat
                        # is_hidden=True means it's saved to database and in memory for LLM, but not shown in UI
                        self.chat_manager.add_assistant_message(current_chat, notification_message, is_hidden=True)
                        # Don't emit chat_message_added signal for hidden messages (UI shouldn't show them)
                        signal_manager.chat_updated.emit(current_chat)
                        logger.info(f"✅ Saved file drop notification to chat {current_chat} (hidden from UI, visible to LLM)")
                    else:
                        logger.warning("No current chat available for file drop notification")
                except Exception as e:
                    logger.warning(f"Could not add notification to chat: {e}")
            else:
                logger.warning("chat_manager not available for file drop notification")
            
            # Notify LLM service via command queue (cross-process communication)
            # The agent process will handle this and call _on_files_indexed on the LLM service
            try:
                # Get the application instance to access command queue
                app = QtWidgets.QApplication.instance()
                logger.debug(f"Application instance: {app}, has _send_command_to_agent: {hasattr(app, '_send_command_to_agent') if app else False}")
                
                if app and hasattr(app, '_send_command_to_agent'):
                    try:
                        app._send_command_to_agent('files_dropped', {'notification_message': notification_message})
                        logger.info(f"✅ Sent files_dropped command to agent process for {len(items_to_show)} item(s) ({len(dropped_folders)} folder(s), {len(individual_files)} file(s))")
                    except Exception as cmd_error:
                        logger.error(f"Error sending files_dropped command: {cmd_error}", exc_info=True)
                        # Fallback to signal
                        signal_manager.files_indexed.emit(notification_message)
                        logger.info(f"✅ Fallback: Emitted files_indexed signal (same process) for {len(items_to_show)} item(s)")
                else:
                    # Fallback: try signal (only works if in same process)
                    signal_manager.files_indexed.emit(notification_message)
                    logger.info(f"✅ Emitted files_indexed signal to LLM service for {len(items_to_show)} item(s) (same process fallback - no command queue)")
            except Exception as e:
                logger.error(f"Could not notify LLM service: {e}", exc_info=True)
                # Last resort: try signal
                try:
                    signal_manager.files_indexed.emit(notification_message)
                    logger.info(f"✅ Last resort: Emitted files_indexed signal for {len(items_to_show)} item(s)")
                except Exception as signal_error:
                    logger.error(f"Failed to emit signal as last resort: {signal_error}", exc_info=True)
                
        except Exception as e:
            logger.error(f"Error notifying agent about dropped files: {e}", exc_info=True)

