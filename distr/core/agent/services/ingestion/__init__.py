"""
Folder Ingestion System - Comprehensive folder indexing and retrieval.

This package provides:
- FolderRegistry: Persistent folder registration
- MetadataStore: SQLite metadata tracking
- IngestionWorker: Async ingestion processing
- Enhanced retrieval with citations
"""

from .metadata_store import MetadataStore
from .folder_registry import FolderRegistry
from .worker import IngestionWorker
from .api import FolderIngestionAPI

__all__ = ['MetadataStore', 'FolderRegistry', 'IngestionWorker', 'FolderIngestionAPI']

