"""
Ingestion Worker - Async worker for folder scanning, extraction, chunking, and embedding.

This module provides the IngestionWorker class that handles:
- Recursive folder scanning with ignore patterns
- Content extraction from various file types
- Code-aware chunking
- Incremental sync (only process changed files)
- Async processing
"""

import logging
import os
import re
import mimetypes
import threading
from typing import Optional, List, Dict, Any, Callable
from pathlib import Path
from fnmatch import fnmatch

from .metadata_store import MetadataStore
from .folder_registry import FolderRegistry

logger = logging.getLogger(__name__)


class IngestionWorker:
    """
    Async worker for ingesting folders into the RAG system.
    
    Handles:
    - File discovery with ignore patterns
    - Content extraction
    - Code-aware chunking
    - Incremental sync
    - Progress reporting
    """
    
    # Supported file extensions
    TEXT_EXTENSIONS = {'.py', '.md', '.txt', '.json', '.yaml', '.yml', '.toml', '.sql', '.js', '.ts', '.jsx', '.tsx', '.html', '.css', '.sh', '.bat', '.ps1'}
    CODE_EXTENSIONS = {'.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.cpp', '.c', '.h', '.hpp', '.cs', '.go', '.rs', '.rb', '.php', '.swift', '.kt', '.scala'}
    DOCUMENT_EXTENSIONS = {'.pdf', '.doc', '.docx'}
    EXCLUDED_EXTENSIONS = {'.wav', '.mp3', '.mp4', '.mov', '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.svg'}
    
    def __init__(
        self,
        metadata_store: Optional[MetadataStore] = None,
        folder_registry: Optional[FolderRegistry] = None,
        rag_service: Optional[Any] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ):
        """
        Initialize ingestion worker.
        
        Args:
            metadata_store: MetadataStore instance
            folder_registry: FolderRegistry instance
            rag_service: LlamaIndexRAGService instance for embedding
            progress_callback: Optional callback for progress updates
        """
        self.metadata_store = metadata_store or MetadataStore()
        self.folder_registry = folder_registry or FolderRegistry(self.metadata_store)
        self.rag_service = rag_service
        self.progress_callback = progress_callback
        self._cancelled = False
        self._error_count = 0
        self._max_errors = 5  # Stop after 5 consecutive errors
        self._last_error = None
        
        # Default ignore patterns
        self.default_ignore_patterns = [
            'node_modules', 'dist', 'build', '.git', '.venv', '__pycache__',
            '.DS_Store', '*.pyc', '*.pyo', '*.pyd', '.pytest_cache',
            '.mypy_cache', '.coverage', 'htmlcov', '.tox', 'venv', 'env', '.env'
        ]
    
    def cancel(self):
        """Cancel the current ingestion operation"""
        self._cancelled = True
    
    def should_ignore(self, file_path: str, ignore_patterns: List[str]) -> bool:
        """
        Check if file should be ignored based on patterns.
        
        Args:
            file_path: File path to check
            ignore_patterns: List of ignore patterns
            
        Returns:
            True if file should be ignored
        """
        file_name = os.path.basename(file_path)
        rel_path = file_path
        
        for pattern in ignore_patterns:
            # Check filename
            if fnmatch(file_name, pattern):
                return True
            # Check if pattern matches any part of path
            if pattern in rel_path:
                return True
            # Check directory names
            for part in Path(rel_path).parts:
                if fnmatch(part, pattern):
                    return True
        
        return False
    
    def discover_files(
        self,
        folder_path: str,
        ignore_patterns: Optional[List[str]] = None
    ) -> List[str]:
        """
        Recursively discover files in folder.
        
        Args:
            folder_path: Root folder path
            ignore_patterns: List of ignore patterns
            
        Returns:
            List of file paths to process
        """
        if ignore_patterns is None:
            ignore_patterns = self.default_ignore_patterns
        
        files = []
        folder_path = os.path.abspath(folder_path)
        
        if not os.path.exists(folder_path):
            logger.warning(f"Folder does not exist: {folder_path}")
            return files
        
        for root, dirs, filenames in os.walk(folder_path):
            # Filter out ignored directories
            dirs[:] = [d for d in dirs if not self.should_ignore(os.path.join(root, d), ignore_patterns)]
            
            for filename in filenames:
                file_path = os.path.join(root, filename)
                
                # Check if should ignore
                if self.should_ignore(file_path, ignore_patterns):
                    continue
                
                # Check file extension
                ext = os.path.splitext(filename)[1].lower()
                if ext in self.EXCLUDED_EXTENSIONS:
                    continue
                
                files.append(file_path)
        
        return files
    
    def extract_text(self, file_path: str) -> Optional[str]:
        """
        Extract text content from file.
        
        Supports:
        - Text files: read as UTF-8
        - Code files: keep as plain text
        - PDF/DOCX: extract text when possible
        - Binary: skip
        
        Returns:
            Extracted text or None if cannot extract
        """
        ext = os.path.splitext(file_path)[1].lower()
        
        # Text and code files
        if ext in self.TEXT_EXTENSIONS or ext in self.CODE_EXTENSIONS:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
            except Exception as e:
                logger.warning(f"Error reading text file {file_path}: {e}")
                return None
        
        # PDF and DOCX
        if ext in self.DOCUMENT_EXTENSIONS:
            try:
                from distr.core.agent.tools.files.document_extractor import DocumentExtractorTool
                extractor = DocumentExtractorTool()
                text = extractor._run(file_path=file_path, extract_archives=False)
                if text and not text.startswith("Error"):
                    return text
            except Exception as e:
                logger.warning(f"Error extracting from {file_path}: {e}")
        
        # Binary files - skip
        logger.debug(f"Skipping binary file: {file_path}")
        return None
    
    def chunk_code(self, text: str, file_path: str) -> List[Dict[str, Any]]:
        """
        Chunk code by function or class when feasible.
        
        Args:
            text: Code text
            file_path: File path for context
            
        Returns:
            List of chunks with start/end offsets
        """
        ext = os.path.splitext(file_path)[1].lower()
        
        # Python-specific chunking
        if ext == '.py':
            return self._chunk_python(text)
        
        # JavaScript/TypeScript chunking
        if ext in {'.js', '.ts', '.jsx', '.tsx'}:
            return self._chunk_javascript(text)
        
        # Fallback to generic chunking
        return self._chunk_generic(text)
    
    def _chunk_python(self, text: str) -> List[Dict[str, Any]]:
        """Chunk Python code by function/class."""
        chunks = []
        lines = text.split('\n')
        current_chunk = []
        start_line = 0
        in_function = False
        in_class = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # Detect class or function definitions
            if stripped.startswith('class ') or stripped.startswith('def '):
                # Save previous chunk
                if current_chunk:
                    chunk_text = '\n'.join(current_chunk)
                    chunks.append({
                        'text': chunk_text,
                        'start_offset': sum(len(l) + 1 for l in lines[:start_line]),
                        'end_offset': sum(len(l) + 1 for l in lines[:i]),
                        'start_line': start_line + 1,
                        'end_line': i + 1
                    })
                
                # Start new chunk
                current_chunk = [line]
                start_line = i
                in_function = stripped.startswith('def ')
                in_class = stripped.startswith('class ')
            else:
                current_chunk.append(line)
        
        # Add final chunk
        if current_chunk:
            chunk_text = '\n'.join(current_chunk)
            chunks.append({
                'text': chunk_text,
                'start_offset': sum(len(l) + 1 for l in lines[:start_line]),
                'end_offset': len(text),
                'start_line': start_line + 1,
                'end_line': len(lines)
            })
        
        return chunks if chunks else self._chunk_generic(text)
    
    def _chunk_javascript(self, text: str) -> List[Dict[str, Any]]:
        """Chunk JavaScript/TypeScript code by function/class."""
        # Similar to Python but with JS patterns
        # For now, use generic chunking
        return self._chunk_generic(text)
    
    def _chunk_generic(self, text: str, chunk_size: int = 1000, overlap: int = 200) -> List[Dict[str, Any]]:
        """
        Generic chunking with overlap.
        
        Args:
            text: Text to chunk
            chunk_size: Target chunk size in characters
            overlap: Overlap between chunks in characters
            
        Returns:
            List of chunks with offsets
        """
        chunks = []
        start = 0
        text_len = len(text)
        
        while start < text_len:
            end = min(start + chunk_size, text_len)
            
            # Try to break at sentence or line boundary
            if end < text_len:
                # Look for newline within last 100 chars
                newline_pos = text.rfind('\n', max(start, end - 100), end)
                if newline_pos > start:
                    end = newline_pos + 1
                # Or look for sentence boundary
                elif end < text_len - 1:
                    sentence_end = max(
                        text.rfind('. ', max(start, end - 100), end),
                        text.rfind('! ', max(start, end - 100), end),
                        text.rfind('? ', max(start, end - 100), end)
                    )
                    if sentence_end > start:
                        end = sentence_end + 2
            
            chunk_text = text[start:end]
            start_line = text[:start].count('\n') + 1
            end_line = text[:end].count('\n') + 1
            
            chunks.append({
                'text': chunk_text,
                'start_offset': start,
                'end_offset': end,
                'start_line': start_line,
                'end_line': end_line
            })
            
            # Move start with overlap
            start = max(start + 1, end - overlap)
        
        return chunks
    
    def ingest_folder(
        self,
        folder_id: str,
        force_full_sync: bool = False,
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Ingest a folder (sync files).
        
        Args:
            folder_id: Folder ID to ingest
            force_full_sync: If True, reprocess all files even if unchanged
            timeout: Optional timeout in seconds (None = no timeout)
            
        Returns:
            Dict with ingestion results
        """
        self._cancelled = False  # Reset cancellation flag
        self._error_count = 0  # Reset error count
        self._last_error = None
        
        import time
        start_time = time.time()
        
        folder = self.folder_registry.get_folder(folder_id)
        if not folder:
            return {"success": False, "error": f"Folder {folder_id} not found"}
        
        if not folder.get('enabled'):
            return {"success": False, "error": f"Folder {folder_id} is disabled"}
        
        folder_path = folder['folder_path']
        ignore_patterns = folder.get('ignore_patterns', [])
        
        # Discover files
        files = self.discover_files(folder_path, ignore_patterns)
        
        if not files:
            return {
                "success": True,
                "files_processed": 0,
                "files_skipped": 0,
                "chunks_created": 0,
                "message": "No files found to process"
            }
        
        # Report progress
        if self.progress_callback:
            self.progress_callback({
                'stage': 'discovery',
                'files_found': len(files),
                'folder_id': folder_id
            })
        
        # Process files
        files_processed = 0
        files_skipped = 0
        files_changed = 0
        chunks_created = 0
        errors = []
        
        for file_path in files:
            # Check for cancellation
            if self._cancelled:
                return {
                    "success": False,
                    "cancelled": True,
                    "message": "Indexing cancelled by user",
                    "files_processed": files_processed,
                    "chunks_created": chunks_created
                }
            
            # Check for timeout
            if timeout and (time.time() - start_time) > timeout:
                return {
                    "success": False,
                    "error": f"Indexing timed out after {timeout} seconds",
                    "error_type": "timeout",
                    "files_processed": files_processed,
                    "chunks_created": chunks_created,
                    "message": f"Indexing exceeded timeout of {timeout} seconds. Please try with fewer files or increase timeout."
                }
            try:
                # Check if file changed (incremental sync)
                if not force_full_sync and not self.metadata_store.file_changed(file_path):
                    files_skipped += 1
                    continue
                
                files_changed += 1
                
                # Extract text
                text = self.extract_text(file_path)
                if not text:
                    files_skipped += 1
                    continue
                
                # Get file hash
                file_hash = self.metadata_store.get_file_hash(file_path)
                if not file_hash:
                    files_skipped += 1
                    continue
                
                # Get MIME type
                mime_type, _ = mimetypes.guess_type(file_path)
                
                # Record file
                file_id = self.metadata_store.record_file(
                    folder_id=folder_id,
                    file_path=file_path,
                    file_hash=file_hash,
                    mime_type=mime_type
                )
                
                # Chunk text
                ext = os.path.splitext(file_path)[1].lower()
                if ext in self.CODE_EXTENSIONS:
                    chunks = self.chunk_code(text, file_path)
                else:
                    chunks = self._chunk_generic(text)
                
                # Record chunks and create embeddings
                for i, chunk_data in enumerate(chunks):
                    chunk_id = self.metadata_store.record_chunk(
                        file_id=file_id,
                        chunk_index=i,
                        start_offset=chunk_data['start_offset'],
                        end_offset=chunk_data['end_offset'],
                        text=chunk_data['text'],
                        token_count=len(chunk_data['text'].split())  # Approximate
                    )
                    
                    # Add to RAG service if available
                    if self.rag_service:
                        try:
                            from llama_index.core import Document
                            from llama_index.core.node_parser import SimpleNodeParser
                            
                            doc = Document(
                                text=chunk_data['text'],
                                metadata={
                                    'file_path': file_path,
                                    'chunk_id': chunk_id,
                                    'file_id': file_id,
                                    'start_line': chunk_data.get('start_line', 0),
                                    'end_line': chunk_data.get('end_line', 0)
                                }
                            )
                            
                            # Parse document into nodes
                            node_parser = SimpleNodeParser.from_defaults(
                                chunk_size=len(chunk_data['text']),
                                chunk_overlap=0
                            )
                            nodes = node_parser.get_nodes_from_documents([doc])
                            
                            # Insert nodes into index
                            if nodes:
                                self.rag_service.index.insert_nodes(nodes)
                                chunks_created += 1
                                # Reset error count on success
                                self._error_count = 0
                        except Exception as e:
                            error_str = str(e)
                            self._error_count += 1
                            self._last_error = error_str
                            
                            # Check if it's an API key error (401) or other critical error
                            is_api_error = '401' in error_str or 'invalid_api_key' in error_str.lower() or 'api key' in error_str.lower()
                            
                            if is_api_error:
                                logger.error(f"API authentication error adding chunk to RAG service: {error_str}")
                                # Stop immediately on API errors
                                return {
                                    "success": False,
                                    "error": f"API authentication failed: {error_str}",
                                    "error_type": "api_auth_error",
                                    "files_processed": files_processed,
                                    "chunks_created": chunks_created,
                                    "message": "Indexing stopped due to API authentication error. Please check your API key in settings."
                                }
                            elif self._error_count >= self._max_errors:
                                logger.error(f"Too many errors ({self._error_count}) adding chunks to RAG service. Last error: {error_str}")
                                return {
                                    "success": False,
                                    "error": f"Too many errors during indexing. Last error: {error_str}",
                                    "error_type": "too_many_errors",
                                    "files_processed": files_processed,
                                    "chunks_created": chunks_created,
                                    "error_count": self._error_count,
                                    "message": f"Indexing stopped after {self._error_count} consecutive errors."
                                }
                            else:
                                logger.warning(f"Error adding chunk to RAG service ({self._error_count}/{self._max_errors}): {e}")
                    else:
                        chunks_created += 1
                
                files_processed += 1
                
                # Report progress
                if self.progress_callback:
                    self.progress_callback({
                        'stage': 'processing',
                        'file_path': file_path,
                        'files_processed': files_processed,
                        'total_files': len(files)
                    })
                
            except Exception as e:
                logger.error(f"Error processing file {file_path}: {e}")
                errors.append(f"{file_path}: {str(e)}")
                files_skipped += 1
        
        # Persist RAG index if service available
        if self.rag_service:
            try:
                self.rag_service.index.storage_context.persist(persist_dir=self.rag_service.persist_dir)
            except Exception as e:
                logger.warning(f"Error persisting RAG index: {e}")
        
        result = {
            "success": True,
            "files_processed": files_processed,
            "files_skipped": files_skipped,
            "files_changed": files_changed,
            "chunks_created": chunks_created,
            "total_files": len(files)
        }
        
        if errors:
            result["errors"] = errors[:10]  # Limit error list
        
        return result
    
    def ingest_folder_async(
        self,
        folder_id: str,
        force_full_sync: bool = False,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> threading.Thread:
        """
        Ingest folder in background thread.
        
        Args:
            folder_id: Folder ID to ingest
            force_full_sync: If True, reprocess all files
            callback: Optional callback for completion
            
        Returns:
            Thread object
        """
        def worker():
            try:
                result = self.ingest_folder(folder_id, force_full_sync)
                if callback:
                    callback(result)
            except Exception as e:
                logger.error(f"Error in async ingestion: {e}")
                if callback:
                    callback({"success": False, "error": str(e)})
        
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        return thread

