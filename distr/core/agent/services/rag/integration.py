"""
RAG Integration Utility - Integrates LlamaIndex RAG with settings and indexed folders

This module provides utilities to integrate the LlamaIndex RAG service with
the application's indexed folders from settings.
"""

import logging
import os
from typing import Optional, Dict, Any, List
from distr.core.settings import load_settings_from_db, resolve_folder_path

logger = logging.getLogger(__name__)

# Global RAG service instance (for settings folders)
_rag_service = None

# Per-chat RAG service instances (chat_id -> service)
_chat_rag_services = {}


def get_rag_service(model_name: str = "qwen3:8b", embedding_model: str = "nomic-embed-text") -> Optional[Any]:
    """Get or create the global LlamaIndex RAG service instance."""
    global _rag_service
    
    # Detect if using OpenAI model
    from distr.core.llm_factory import is_openai_model as _is_openai
    is_openai_model = _is_openai(model_name)
    
    # Get OpenAI API key if needed
    openai_api_key = None
    if is_openai_model:
        settings = load_settings_from_db()
        openai_api_key = settings.get('openai_key') or settings.get('openai_api_key')
        if not openai_api_key:
            logger.warning("OpenAI model specified but no API key found in settings")
    
    # Create new service if model changed or doesn't exist
    if _rag_service is None or (
        hasattr(_rag_service, 'model_name') and _rag_service.model_name != model_name
    ):
        try:
            from distr.core.agent.services.rag.indexing import LlamaIndexRAGService
            _rag_service = LlamaIndexRAGService(
                model_name=model_name,
                embedding_model=embedding_model,
                openai_api_key=openai_api_key
            )
            logger.info(f"LlamaIndex RAG service initialized with model: {model_name} (OpenAI: {is_openai_model})")
        except ImportError as e:
            logger.warning(f"LlamaIndex not available: {e}. Install with: pip install llama-index llama-index-embeddings-ollama llama-index-llms-ollama")
            return None
        except Exception as e:
            logger.error(f"Failed to create RAG service: {e}")
            return None
    return _rag_service


def get_indexed_folders() -> List[str]:
    """
    Get indexed folders from settings.
    
    Returns:
        List of resolved folder paths
    """
    settings = load_settings_from_db()
    
    # Get indexed folders from settings
    indexed_folders = settings.get('indexed_folders', [])
    
    # Handle both string (JSON) and list formats
    if isinstance(indexed_folders, str):
        try:
            import json
            indexed_folders = json.loads(indexed_folders)
        except (json.JSONDecodeError, ValueError):
            indexed_folders = []
    
    if not isinstance(indexed_folders, list):
        indexed_folders = []
    
    # Resolve folder paths
    resolved_folders = []
    for folder in indexed_folders:
        resolved = resolve_folder_path(folder)
        if os.path.exists(resolved):
            resolved_folders.append(resolved)
        else:
            logger.warning(f"Indexed folder does not exist: {resolved}")
    
    return resolved_folders


def index_settings_folders(
    model_name: str = "qwen3:8b",
    embedding_model: str = "nomic-embed-text",
    exclude_extensions: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Index folders from settings using the comprehensive folder ingestion system.
    
    This uses the new FolderIngestionAPI which provides:
    - Persistent folder registration with stable IDs
    - Incremental sync (only process changed files)
    - Code-aware chunking
    - Metadata tracking for citations
    - Async processing
    
    Args:
        model_name: LLM model name
        embedding_model: Embedding model name
        exclude_extensions: Optional list of file extensions to exclude
        
    Returns:
        Dict with indexing results
    """
    rag_service = get_rag_service(model_name, embedding_model)
    if not rag_service:
        return {
            "success": False,
            "error": "RAG service not available. Please install LlamaIndex."
        }
    
    # Get indexed folders from settings
    folders = get_indexed_folders()
    
    if not folders:
        return {
            "success": False,
            "error": "No indexed folders found in settings"
        }
    
    # Remove folders that are children of other folders in the list
    # (parent already recurses into children, so indexing both is redundant)
    folders.sort()
    deduped = []
    for f in folders:
        if any(f.startswith(parent + os.sep) for parent in deduped):
            logger.info(f"Skipping {f} — already covered by a parent folder")
            continue
        deduped.append(f)
    folders = deduped
    
    logger.info(f"Indexing {len(folders)} folders from settings using folder ingestion system")
    
    try:
        # Use the comprehensive folder ingestion API
        from distr.core.agent.services.ingestion.api import FolderIngestionAPI
        
        ingestion_api = FolderIngestionAPI(rag_service=rag_service)
        
        # Convert exclude_extensions to ignore_patterns format
        ignore_patterns = None
        if exclude_extensions:
            ignore_patterns = [f"*{ext}" for ext in exclude_extensions]
        
        # Register and sync each folder
        total_files = 0
        total_chunks = 0
        folder_results = []
        
        for folder_path in folders:
            try:
                # Register folder (gets stable ID)
                folder_id = ingestion_api.register_folder(
                    folder_path=folder_path,
                    ignore_patterns=ignore_patterns,
                    enabled=True
                )
                
                # Sync folder (incremental - only changed files)
                sync_result = ingestion_api.sync_folder(
                    folder_id=folder_id,
                    force_full_sync=False,  # Use incremental sync
                    async_mode=False  # Sync synchronously for settings folders
                )
                
                if sync_result.get('success'):
                    total_files += sync_result.get('files_processed', 0)
                    total_chunks += sync_result.get('chunks_created', 0)
                    folder_results.append({
                        'folder_path': folder_path,
                        'folder_id': folder_id,
                        'files_processed': sync_result.get('files_processed', 0),
                        'chunks_created': sync_result.get('chunks_created', 0)
                    })
                else:
                    logger.warning(f"Failed to sync folder {folder_path}: {sync_result.get('error')}")
                    folder_results.append({
                        'folder_path': folder_path,
                        'error': sync_result.get('error')
                    })
            except Exception as e:
                logger.error(f"Error processing folder {folder_path}: {e}")
                folder_results.append({
                    'folder_path': folder_path,
                    'error': str(e)
                })
        
        return {
            "success": True,
            "folders_indexed": len(folders),
            "files_processed": total_files,
            "chunks_created": total_chunks,
            "folder_results": folder_results
        }
        
    except ImportError as e:
        # Fallback to old method if folder ingestion not available
        logger.warning(f"Folder ingestion API not available, using legacy method: {e}")
        result = rag_service.index_directories(folders, exclude_extensions)
        return result
    except Exception as e:
        logger.error(f"Error using folder ingestion API: {e}")
        # Fallback to old method
        result = rag_service.index_directories(folders, exclude_extensions)
        return result


def query_rag(query_text: str, model_name: str = "qwen3:8b") -> Dict[str, Any]:
    """
    Query the RAG system with indexed folders.
    
    Args:
        query_text: Query string
        model_name: LLM model name
        
    Returns:
        Dict with response and metadata
    """
    rag_service = get_rag_service(model_name)
    if not rag_service:
        return {
            "success": False,
            "error": "RAG service not available"
        }
    
    return rag_service.query(query_text)


def add_files_to_index(file_paths: List[str], model_name: str = "qwen3:8b") -> Dict[str, Any]:
    """
    Add specific files to the RAG index.
    
    Args:
        file_paths: List of file paths to index
        model_name: LLM model name
        
    Returns:
        Dict with indexing results
    """
    rag_service = get_rag_service(model_name)
    if not rag_service:
        return {
            "success": False,
            "error": "RAG service not available"
        }
    
    return rag_service.index_files(file_paths)


def add_directories_to_index(directory_paths: List[str], model_name: str = "qwen3:8b", exclude_extensions: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Add directories to the RAG index.
    
    Args:
        directory_paths: List of directory paths to index
        model_name: LLM model name
        exclude_extensions: Optional list of file extensions to exclude
        
    Returns:
        Dict with indexing results
    """
    rag_service = get_rag_service(model_name)
    if not rag_service:
        return {
            "success": False,
            "error": "RAG service not available"
        }
    
    return rag_service.index_directories(directory_paths, exclude_extensions)


def clear_rag_index(model_name: str = "qwen3:8b") -> Dict[str, Any]:
    """
    Clear/reset the RAG index (removes all indexed documents).
    
    Args:
        model_name: LLM model name (used to get the correct RAG service instance)
        
    Returns:
        Dict with success status
    """
    rag_service = get_rag_service(model_name)
    if not rag_service:
        return {
            "success": False,
            "error": "RAG service not available"
        }
    
    return rag_service.clear_index()


def get_chat_rag_service(chat_id: int, model_name: str = "qwen3:8b", embedding_model: str = "nomic-embed-text") -> Optional[Any]:
    """
    Get or create a per-chat RAG service instance (Chat Index Bucket).
    
    Each chat has its own ephemeral index that is deleted when the chat ends.
    This allows per-chat retrieval over dropped folders.
    
    Args:
        chat_id: Chat session ID
        model_name: LLM model name
        embedding_model: Embedding model name
        
    Returns:
        LlamaIndexRAGService instance for this chat, or None if unavailable
    """
    global _chat_rag_services
    
    # Detect if using OpenAI model
    from distr.core.llm_factory import is_openai_model as _is_openai
    is_openai_model = _is_openai(model_name)
    
    # Get OpenAI API key if needed
    openai_api_key = None
    if is_openai_model:
        settings = load_settings_from_db()
        openai_api_key = settings.get('openai_key') or settings.get('openai_api_key')
        if not openai_api_key:
            logger.warning("OpenAI model specified but no API key found in settings")
    
    # Get or create service for this chat
    if chat_id not in _chat_rag_services:
        try:
            import time
            import threading
            init_start = time.time()
            current_thread = threading.current_thread().name
            logger.info(f"[RAG] Creating new RAG service for chat {chat_id} on thread: {current_thread}")
            
            from distr.core.agent.services.rag.indexing import LlamaIndexRAGService
            
            # Create per-chat index directory
            home_dir = os.path.expanduser("~")
            chat_index_dir = os.path.join(home_dir, ".decisions", "chat_indexes", f"chat_{chat_id}")
            
            # CRITICAL: Always start with a fresh/empty index for per-chat indexes
            # This ensures that when switching chats, indexes are cleared and only re-indexed on-demand
            # The folder reference will still be available in context, but indexing happens fresh each time
            if os.path.exists(chat_index_dir):
                logger.info(f"[RAG] Clearing existing index directory for chat {chat_id} to ensure fresh start: {chat_index_dir}")
                import shutil
                try:
                    shutil.rmtree(chat_index_dir)
                    logger.info(f"[RAG] Cleared old index directory for chat {chat_id}")
                except Exception as e:
                    logger.warning(f"[RAG] Could not clear index directory for chat {chat_id}: {e}")
            
            os.makedirs(chat_index_dir, exist_ok=True)
            
            _chat_rag_services[chat_id] = LlamaIndexRAGService(
                model_name=model_name,
                embedding_model=embedding_model,
                index_path=chat_index_dir,
                persist_dir=chat_index_dir,
                openai_api_key=openai_api_key
            )
            init_duration = time.time() - init_start
            logger.info(f"[RAG] Created per-chat RAG service for chat {chat_id} at {chat_index_dir} in {init_duration:.3f}s (thread: {current_thread})")
        except ImportError as e:
            logger.warning(f"LlamaIndex not available: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to create per-chat RAG service: {e}")
            return None
    
    return _chat_rag_services[chat_id]


def index_chat_directory(chat_id: int, directory_path: str, model_name: str = "qwen3:8b", exclude_extensions: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Index a directory in a chat's per-chat index bucket.
    
    This is called when a folder is dropped on the oracle ball for a specific chat.
    
    Args:
        chat_id: Chat session ID
        directory_path: Directory path to index
        model_name: LLM model name
        exclude_extensions: Optional list of file extensions to exclude
        
    Returns:
        Dict with indexing results
    """
    rag_service = get_chat_rag_service(chat_id, model_name)
    if not rag_service:
        return {
            "success": False,
            "error": "RAG service not available for chat"
        }
    
    if not os.path.exists(directory_path):
        return {
            "success": False,
            "error": f"Directory does not exist: {directory_path}"
        }
    
    logger.info(f"Indexing directory {directory_path} in chat {chat_id} bucket")
    result = rag_service.index_directories([directory_path], exclude_extensions)
    return result


def clear_chat_index_cache(chat_id: int) -> bool:
    """
    Clear a chat's RAG service from in-memory cache (but keep persisted index).
    
    This is called when switching away from a chat to free memory.
    The persisted index will be cleared when the chat is accessed again.
    
    Args:
        chat_id: Chat session ID
        
    Returns:
        True if cache was cleared, False otherwise
    """
    global _chat_rag_services
    
    try:
        if chat_id in _chat_rag_services:
            del _chat_rag_services[chat_id]
            logger.info(f"Cleared RAG service cache for chat {chat_id}")
            return True
        return False
    except Exception as e:
        logger.error(f"Error clearing chat index cache for chat {chat_id}: {e}")
        return False


def cleanup_chat_index(chat_id: int) -> bool:
    """
    Clean up a chat's index bucket when the chat is deleted.
    
    This removes both the in-memory cache and the persisted index directory.
    
    Args:
        chat_id: Chat session ID
        
    Returns:
        True if cleanup was successful, False otherwise
    """
    global _chat_rag_services
    
    try:
        # Remove service from cache
        if chat_id in _chat_rag_services:
            del _chat_rag_services[chat_id]
        
        # Delete the index directory
        home_dir = os.path.expanduser("~")
        chat_index_dir = os.path.join(home_dir, ".decisions", "chat_indexes", f"chat_{chat_id}")
        
        if os.path.exists(chat_index_dir):
            import shutil
            shutil.rmtree(chat_index_dir)
            logger.info(f"Cleaned up chat index bucket for chat {chat_id}")
        
        return True
    except Exception as e:
        logger.error(f"Error cleaning up chat index for chat {chat_id}: {e}")
        return False


def initialize_global_index(model_name: str = "qwen3:8b", embedding_model: str = "nomic-embed-text") -> Dict[str, Any]:
    """
    Initialize the global index with folders from settings.
    
    This should be called when the app starts or when a new chat is created
    to ensure the global index is up-to-date with settings.
    
    Args:
        model_name: LLM model name
        embedding_model: Embedding model name
        
    Returns:
        Dict with initialization results
    """
    # Get indexed folders from settings
    folders = get_indexed_folders()
    
    if not folders:
        logger.info("No indexed folders in settings, global index will be empty")
        return {
            "success": True,
            "message": "No folders to index",
            "folders_indexed": 0
        }
    
    # Get excluded file types from settings
    settings = load_settings_from_db()
    exclude_text = settings.get('excluded_files', '')
    exclude_extensions = None
    if exclude_text:
        exclude_extensions = [
            ext.strip() if ext.strip().startswith('.') else f".{ext.strip()}"
            for ext in exclude_text.split(',')
            if ext.strip()
        ]
    
    # Index folders in global index
    result = index_settings_folders(
        model_name=model_name,
        embedding_model=embedding_model,
        exclude_extensions=exclude_extensions
    )
    
    if result.get('success'):
        logger.info(f"Global index initialized with {len(folders)} folder(s) from settings")
    else:
        logger.warning(f"Global index initialization had issues: {result.get('error')}")
    
    return result

