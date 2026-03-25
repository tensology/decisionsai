"""
Folder Ingestion API - High-level API for folder registration, sync, and query.

This module provides the main API for the folder ingestion system:
- register_folder: Register a folder for indexing
- sync_folder: Sync/ingest a folder
- query: Query indexed folders with citations
- get_status: Get folder statistics
"""

import logging
from typing import Optional, List, Dict, Any, Callable

from .metadata_store import MetadataStore
from .folder_registry import FolderRegistry
from .worker import IngestionWorker

logger = logging.getLogger(__name__)


class FolderIngestionAPI:
    """
    High-level API for folder ingestion system.
    
    Provides:
    - Folder registration with stable IDs
    - Async folder ingestion
    - Query with citations
    - Status and statistics
    """
    
    def __init__(
        self,
        rag_service: Optional[Any] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ):
        """
        Initialize folder ingestion API.
        
        Args:
            rag_service: LlamaIndexRAGService instance for embedding
            progress_callback: Optional callback for progress updates
        """
        self.metadata_store = MetadataStore()
        self.folder_registry = FolderRegistry(self.metadata_store)
        self.ingestion_worker = IngestionWorker(
            metadata_store=self.metadata_store,
            folder_registry=self.folder_registry,
            rag_service=rag_service,
            progress_callback=progress_callback
        )
        self.rag_service = rag_service
    
    def register_folder(
        self,
        folder_path: str,
        ignore_patterns: Optional[List[str]] = None,
        enabled: bool = True
    ) -> str:
        """
        Register a folder with a stable folder ID.
        
        Args:
            folder_path: Absolute path to folder
            ignore_patterns: List of ignore patterns (e.g., ['*.pyc', '__pycache__'])
            enabled: Whether folder is enabled for indexing
            
        Returns:
            Stable folder_id
        """
        return self.folder_registry.register_folder(
            folder_path=folder_path,
            ignore_patterns=ignore_patterns,
            enabled=enabled
        )
    
    def sync_folder(
        self,
        folder_id: str,
        force_full_sync: bool = False,
        async_mode: bool = True,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Sync/ingest a folder.
        
        Args:
            folder_id: Folder ID to sync
            force_full_sync: If True, reprocess all files even if unchanged
            async_mode: If True, run in background thread
            callback: Optional callback for completion (only used in async mode)
            timeout: Optional timeout in seconds (None = no timeout)
            
        Returns:
            Dict with sync results (or thread if async_mode=True)
        """
        if async_mode:
            thread = self.ingestion_worker.ingest_folder_async(
                folder_id=folder_id,
                force_full_sync=force_full_sync,
                callback=callback
            )
            return {
                "success": True,
                "async": True,
                "thread": thread,
                "message": "Ingestion started in background"
            }
        else:
            return self.ingestion_worker.ingest_folder(
                folder_id=folder_id,
                force_full_sync=force_full_sync,
                timeout=timeout
            )
    
    def query(
        self,
        folder_ids: Optional[List[str]] = None,
        question: str = "",
        top_k: int = 5
    ) -> Dict[str, Any]:
        """
        Query indexed folders with hybrid retrieval.
        
        Args:
            folder_ids: List of folder IDs to query (None = all enabled folders)
            question: Query question
            top_k: Number of results to return
            
        Returns:
            Dict with response and citations
        """
        if not self.rag_service:
            return {
                "success": False,
                "error": "RAG service not available"
            }
        
        # Query RAG service
        result = self.rag_service.query(question)
        
        if not result.get('success'):
            return result
        
        # Enhance with citations from metadata store
        source_nodes = result.get('source_nodes', [])
        enhanced_sources = []
        
        for node in source_nodes:
            metadata = node.get('metadata', {})
            chunk_id = metadata.get('chunk_id')
            
            citation = None
            if chunk_id:
                citation = self.metadata_store.get_chunk_citation(chunk_id)
            
            enhanced_source = {
                'text': node.get('text', ''),
                'score': node.get('score', 0.0),
                'metadata': metadata,
                'citation': citation
            }
            enhanced_sources.append(enhanced_source)
        
        return {
            "success": True,
            "response": result.get('response', ''),
            "sources": enhanced_sources,
            "top_k": top_k
        }
    
    def get_status(self, folder_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get status and statistics for folder(s).
        
        Args:
            folder_id: Specific folder ID, or None for all folders
            
        Returns:
            Dict with status information
        """
        if folder_id:
            folder = self.folder_registry.get_folder(folder_id)
            if not folder:
                return {"success": False, "error": f"Folder {folder_id} not found"}
            
            stats = self.folder_registry.get_folder_stats(folder_id)
            return {
                "success": True,
                "folder": folder,
                "stats": stats
            }
        else:
            # Get all folders
            folders = self.folder_registry.list_folders(enabled_only=False)
            all_stats = []
            
            for folder in folders:
                stats = self.folder_registry.get_folder_stats(folder['folder_id'])
                all_stats.append({
                    "folder": folder,
                    "stats": stats
                })
            
            return {
                "success": True,
                "folders": all_stats,
                "total_folders": len(folders)
            }
    
    def enable_folder(self, folder_id: str) -> bool:
        """Enable folder for indexing."""
        return self.folder_registry.enable_folder(folder_id)
    
    def disable_folder(self, folder_id: str) -> bool:
        """Disable folder (keeps data but stops indexing)."""
        return self.folder_registry.disable_folder(folder_id)
    
    def remove_folder(self, folder_id: str) -> bool:
        """Remove folder and all associated data."""
        return self.folder_registry.remove_folder(folder_id)
    
    def update_folder(
        self,
        folder_id: str,
        ignore_patterns: Optional[List[str]] = None,
        enabled: Optional[bool] = None
    ) -> bool:
        """Update folder settings."""
        return self.folder_registry.update_folder(
            folder_id=folder_id,
            ignore_patterns=ignore_patterns,
            enabled=enabled
        )

