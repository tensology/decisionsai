"""
Metadata Store - SQLite database for tracking folders, files, chunks, and embeddings.

This module provides a SQLite-based metadata store for incremental sync,
file tracking, and citation support.
"""

import logging
import os
import sqlite3
import hashlib
import json
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class MetadataStore:
    """
    SQLite metadata store for folder ingestion tracking.
    
    Tracks:
    - Registered folders with stable IDs
    - Files with hashes and modification times
    - Chunks with file references and offsets
    - Embedding references
    - Git commit info (optional)
    """
    
    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the metadata store.
        
        Args:
            db_path: Path to SQLite database file. If None, uses default location.
        """
        if db_path is None:
            home_dir = os.path.expanduser("~")
            db_dir = os.path.join(home_dir, ".decisionsai", "rag_metadata")
            os.makedirs(db_dir, exist_ok=True)
            db_path = os.path.join(db_dir, "metadata.db")
        
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Folders table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS folders (
                folder_id TEXT PRIMARY KEY,
                folder_path TEXT NOT NULL UNIQUE,
                ignore_patterns TEXT,
                enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Files table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS files (
                file_id TEXT PRIMARY KEY,
                folder_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                modified_time REAL NOT NULL,
                file_size INTEGER NOT NULL,
                mime_type TEXT,
                language TEXT,
                git_commit TEXT,
                indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (folder_id) REFERENCES folders(folder_id) ON DELETE CASCADE
            )
        """)
        
        # Chunks table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                start_offset INTEGER NOT NULL,
                end_offset INTEGER NOT NULL,
                text TEXT NOT NULL,
                token_count INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (file_id) REFERENCES files(file_id) ON DELETE CASCADE
            )
        """)
        
        # Embeddings table (references to vector store)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                embedding_id TEXT PRIMARY KEY,
                chunk_id TEXT NOT NULL,
                vector_store_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE
            )
        """)
        
        # Indexes for performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_folder ON files(folder_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_path ON files(file_path)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_chunk ON embeddings(chunk_id)")
        
        conn.commit()
        conn.close()
        logger.info(f"Initialized metadata store at {self.db_path}")
    
    def register_folder(
        self,
        folder_path: str,
        ignore_patterns: Optional[List[str]] = None,
        enabled: bool = True
    ) -> str:
        """
        Register a folder with a stable ID.
        
        Args:
            folder_path: Absolute path to folder
            ignore_patterns: List of ignore patterns (e.g., ['*.pyc', '__pycache__'])
            enabled: Whether folder is enabled for indexing
            
        Returns:
            Stable folder_id
        """
        # Generate stable folder_id from path
        folder_id = hashlib.md5(folder_path.encode()).hexdigest()[:16]
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        ignore_json = json.dumps(ignore_patterns) if ignore_patterns else None
        
        cursor.execute("""
            INSERT OR REPLACE INTO folders 
            (folder_id, folder_path, ignore_patterns, enabled, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (folder_id, folder_path, ignore_json, 1 if enabled else 0))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Registered folder {folder_path} with ID {folder_id}")
        return folder_id
    
    def get_folder(self, folder_id: str) -> Optional[Dict[str, Any]]:
        """Get folder by ID."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM folders WHERE folder_id = ?", (folder_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            result = dict(row)
            if result.get('ignore_patterns'):
                result['ignore_patterns'] = json.loads(result['ignore_patterns'])
            return result
        return None
    
    def list_folders(self, enabled_only: bool = True) -> List[Dict[str, Any]]:
        """List all registered folders."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if enabled_only:
            cursor.execute("SELECT * FROM folders WHERE enabled = 1 ORDER BY created_at")
        else:
            cursor.execute("SELECT * FROM folders ORDER BY created_at")
        
        rows = cursor.fetchall()
        conn.close()
        
        result = []
        for row in rows:
            folder = dict(row)
            if folder.get('ignore_patterns'):
                folder['ignore_patterns'] = json.loads(folder['ignore_patterns'])
            result.append(folder)
        
        return result
    
    def update_folder(
        self,
        folder_id: str,
        ignore_patterns: Optional[List[str]] = None,
        enabled: Optional[bool] = None
    ) -> bool:
        """Update folder settings."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        updates = []
        params = []
        
        if ignore_patterns is not None:
            updates.append("ignore_patterns = ?")
            params.append(json.dumps(ignore_patterns))
        
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(1 if enabled else 0)
        
        if not updates:
            conn.close()
            return False
        
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(folder_id)
        
        cursor.execute(
            f"UPDATE folders SET {', '.join(updates)} WHERE folder_id = ?",
            params
        )
        
        conn.commit()
        conn.close()
        return cursor.rowcount > 0
    
    def remove_folder(self, folder_id: str) -> bool:
        """Remove folder and all associated data (cascade delete)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM folders WHERE folder_id = ?", (folder_id,))
        deleted = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        if deleted:
            logger.info(f"Removed folder {folder_id} and all associated data")
        
        return deleted
    
    def get_file_hash(self, file_path: str) -> Optional[str]:
        """Get file hash for change detection."""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception as e:
            logger.warning(f"Error computing hash for {file_path}: {e}")
            return None
    
    def get_file_metadata(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Get stored metadata for a file."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM files WHERE file_path = ?", (file_path,))
        row = cursor.fetchone()
        conn.close()
        
        return dict(row) if row else None
    
    def file_changed(self, file_path: str) -> bool:
        """
        Check if file has changed since last indexing.
        
        Returns:
            True if file is new or changed, False if unchanged
        """
        stored = self.get_file_metadata(file_path)
        
        if not stored:
            return True  # New file
        
        try:
            stat = os.stat(file_path)
            current_mtime = stat.st_mtime
            current_size = stat.st_size
            
            # Check if modified time or size changed
            if current_mtime != stored['modified_time'] or current_size != stored['file_size']:
                return True
            
            # Check hash if available
            current_hash = self.get_file_hash(file_path)
            if current_hash and current_hash != stored['file_hash']:
                return True
            
            return False
        except OSError:
            return True  # File doesn't exist or can't be read
    
    def record_file(
        self,
        folder_id: str,
        file_path: str,
        file_hash: str,
        mime_type: Optional[str] = None,
        language: Optional[str] = None,
        git_commit: Optional[str] = None
    ) -> str:
        """
        Record a file in the metadata store.
        
        Returns:
            file_id
        """
        file_id = hashlib.md5(file_path.encode()).hexdigest()[:16]
        
        try:
            stat = os.stat(file_path)
            modified_time = stat.st_mtime
            file_size = stat.st_size
        except OSError as e:
            logger.warning(f"Error getting file stats for {file_path}: {e}")
            modified_time = 0
            file_size = 0
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO files
            (file_id, folder_id, file_path, file_hash, modified_time, file_size, 
             mime_type, language, git_commit, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (file_id, folder_id, file_path, file_hash, modified_time, file_size,
              mime_type, language, git_commit))
        
        conn.commit()
        conn.close()
        
        return file_id
    
    def record_chunk(
        self,
        file_id: str,
        chunk_index: int,
        start_offset: int,
        end_offset: int,
        text: str,
        token_count: Optional[int] = None
    ) -> str:
        """
        Record a chunk in the metadata store.
        
        Returns:
            chunk_id
        """
        # Generate deterministic chunk_id
        chunk_id = hashlib.md5(
            f"{file_id}:{chunk_index}:{start_offset}:{end_offset}".encode()
        ).hexdigest()[:16]
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO chunks
            (chunk_id, file_id, chunk_index, start_offset, end_offset, text, token_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (chunk_id, file_id, chunk_index, start_offset, end_offset, text, token_count))
        
        conn.commit()
        conn.close()
        
        return chunk_id
    
    def record_embedding(
        self,
        chunk_id: str,
        vector_store_id: Optional[str] = None
    ) -> str:
        """Record embedding reference."""
        embedding_id = hashlib.md5(f"{chunk_id}:{vector_store_id}".encode()).hexdigest()[:16]
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO embeddings
            (embedding_id, chunk_id, vector_store_id)
            VALUES (?, ?, ?)
        """, (embedding_id, chunk_id, vector_store_id))
        
        conn.commit()
        conn.close()
        
        return embedding_id
    
    def get_chunk_citation(self, chunk_id: str) -> Optional[Dict[str, Any]]:
        """
        Get citation info for a chunk (file path and line ranges).
        
        Returns:
            Dict with file_path, start_line, end_line, or None if not found
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT f.file_path, c.start_offset, c.end_offset, c.text
            FROM chunks c
            JOIN files f ON c.file_id = f.file_id
            WHERE c.chunk_id = ?
        """, (chunk_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            # Convert byte offsets to line numbers (approximate)
            file_path = row['file_path']
            start_offset = row['start_offset']
            end_offset = row['end_offset']
            text = row['text']
            
            # Count newlines before start to estimate line number
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    start_line = content[:start_offset].count('\n') + 1
                    end_line = content[:end_offset].count('\n') + 1
            except Exception:
                # Fallback: estimate from chunk text
                start_line = text[:start_offset].count('\n') + 1 if start_offset < len(text) else 1
                end_line = text[:end_offset].count('\n') + 1 if end_offset < len(text) else start_line
            
            return {
                'file_path': file_path,
                'start_line': start_line,
                'end_line': end_line,
                'start_offset': start_offset,
                'end_offset': end_offset
            }
        
        return None
    
    def delete_file(self, file_path: str) -> bool:
        """Delete file and all associated chunks/embeddings (cascade)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM files WHERE file_path = ?", (file_path,))
        deleted = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return deleted
    
    def get_folder_stats(self, folder_id: str) -> Dict[str, Any]:
        """Get statistics for a folder."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Count files
        cursor.execute("SELECT COUNT(*) FROM files WHERE folder_id = ?", (folder_id,))
        file_count = cursor.fetchone()[0]
        
        # Count chunks
        cursor.execute("""
            SELECT COUNT(*) FROM chunks c
            JOIN files f ON c.file_id = f.file_id
            WHERE f.folder_id = ?
        """, (folder_id,))
        chunk_count = cursor.fetchone()[0]
        
        # Last sync time
        cursor.execute("""
            SELECT MAX(indexed_at) FROM files WHERE folder_id = ?
        """, (folder_id,))
        last_sync = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'folder_id': folder_id,
            'file_count': file_count,
            'chunk_count': chunk_count,
            'last_sync': last_sync
        }








