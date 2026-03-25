"""
Folder Registry - Persistent folder registration with stable IDs.

Manages folder registrations, ignore patterns, and enable/disable state.
"""

import logging
from typing import Optional, List, Dict, Any
from .metadata_store import MetadataStore

logger = logging.getLogger(__name__)


class FolderRegistry:
    """
    Registry for managing folder registrations.
    
    Provides stable folder IDs and manages folder settings.
    """
    
    def __init__(self, metadata_store: Optional[MetadataStore] = None):
        """
        Initialize folder registry.
        
        Args:
            metadata_store: MetadataStore instance. If None, creates a new one.
        """
        self.metadata_store = metadata_store or MetadataStore()
    
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
        # Add default ignore patterns if not provided
        if ignore_patterns is None:
            ignore_patterns = self._get_default_ignore_patterns()
        
        folder_id = self.metadata_store.register_folder(
            folder_path=folder_path,
            ignore_patterns=ignore_patterns,
            enabled=enabled
        )
        
        logger.info(f"Registered folder: {folder_path} (ID: {folder_id})")
        return folder_id
    
    def _get_default_ignore_patterns(self) -> List[str]:
        """Get default ignore patterns for common junk."""
        return [
            'node_modules',
            'dist',
            'build',
            '.git',
            '.venv',
            '__pycache__',
            '.DS_Store',
            '*.pyc',
            '*.pyo',
            '*.pyd',
            '.pytest_cache',
            '.mypy_cache',
            '.coverage',
            'htmlcov',
            '.tox',
            'venv',
            'env',
            '.env'
        ]
    
    def get_folder(self, folder_id: str) -> Optional[Dict[str, Any]]:
        """Get folder by ID."""
        return self.metadata_store.get_folder(folder_id)
    
    def list_folders(self, enabled_only: bool = True) -> List[Dict[str, Any]]:
        """List all registered folders."""
        return self.metadata_store.list_folders(enabled_only=enabled_only)
    
    def update_folder(
        self,
        folder_id: str,
        ignore_patterns: Optional[List[str]] = None,
        enabled: Optional[bool] = None
    ) -> bool:
        """Update folder settings."""
        return self.metadata_store.update_folder(
            folder_id=folder_id,
            ignore_patterns=ignore_patterns,
            enabled=enabled
        )
    
    def remove_folder(self, folder_id: str) -> bool:
        """Remove folder and all associated data."""
        return self.metadata_store.remove_folder(folder_id)
    
    def get_folder_stats(self, folder_id: str) -> Dict[str, Any]:
        """Get statistics for a folder."""
        return self.metadata_store.get_folder_stats(folder_id)
    
    def enable_folder(self, folder_id: str) -> bool:
        """Enable folder for indexing."""
        return self.update_folder(folder_id, enabled=True)
    
    def disable_folder(self, folder_id: str) -> bool:
        """Disable folder (keeps data but stops indexing)."""
        return self.update_folder(folder_id, enabled=False)








