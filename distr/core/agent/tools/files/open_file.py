"""
Open File Tool for LangChain.

This tool searches for and opens files by name, handling file path resolution,
folder references, and fuzzy matching.
"""

from typing import Optional
from langchain.tools import BaseTool
from pydantic import Field, BaseModel
import logging
import os
import subprocess
import platform
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


class OpenFileInput(BaseModel):
    """Input schema for open_file tool."""
    file_name: str = Field(description="The name of the file to open (e.g., 'dogbreeds.txt', 'report.pdf', 'image.jpg'). Can include partial path like 'Downloads/image.jpg'")
    search_folders: Optional[str] = Field(default=None, description="Optional comma-separated list of folders to search (e.g., 'Downloads,Documents,Desktop'). Defaults to common folders if not specified.")


class OpenFileTool(BaseTool):
    """Tool for finding and opening files by name."""
    
    name: str = "open_file"
    description: str = """🎯 Find and open a file by name.
    
    CRITICAL: Use this tool when the user wants to open a specific file.
    
    This tool will:
    - Search common folders (Downloads, Documents, Desktop, etc.)
    - Use fuzzy matching to find files even with slight name variations
    - Open the file with the default application
    
    Examples:
    - "open dogbreeds.txt"
    - "open the report.pdf in my Documents"
    - "open image.jpg from Downloads"
    - "open that file you just created"
    - "open the PDF in my desktop"
    
    The tool searches intelligently and handles:
    - Partial file names
    - Folder references ("my desktop", "Downloads", etc.)
    - File extensions (automatically searches common extensions)
    - Fuzzy matching for typos or variations
    
    CALL THIS TOOL immediately when user asks to open a file - never explain, just execute."""
    
    args_schema: type[BaseModel] = OpenFileInput
    
    def _resolve_folder_path(self, folder_name: str) -> str:
        """Resolve folder references like 'my desktop' to actual paths."""
        home = os.path.expanduser("~")
        folder_lower = folder_name.lower().strip()
        
        folder_map = {
            'desktop': os.path.join(home, 'Desktop'),
            'documents': os.path.join(home, 'Documents'),
            'downloads': os.path.join(home, 'Downloads'),
            'pictures': os.path.join(home, 'Pictures'),
            'music': os.path.join(home, 'Music'),
            'videos': os.path.join(home, 'Movies'),
            'movies': os.path.join(home, 'Movies'),
        }
        
        # Handle "my X" pattern
        if folder_lower.startswith('my '):
            folder_lower = folder_lower[3:]
        
        return folder_map.get(folder_lower, folder_name)
    
    def _find_file(self, file_name: str, search_folders: Optional[str] = None) -> Optional[str]:
        """Find a file by name using fuzzy matching."""
        # Normalize file name
        file_name_clean = file_name.strip().strip('"').strip("'")
        
        # Parse search folders
        if search_folders:
            folders = [self._resolve_folder_path(f.strip()) for f in search_folders.split(',')]
        else:
            # Default search folders
            home = os.path.expanduser("~")
            folders = [
                os.path.join(home, "Downloads"),
                os.path.join(home, "Documents"),
                os.path.join(home, "Desktop"),
                os.path.join(home, "Pictures"),
            ]
        
        # Extract base name and check for extension
        base_name = os.path.basename(file_name_clean)
        has_extension = '.' in base_name
        
        # If no extension, try common extensions
        extensions_to_try = []
        if has_extension:
            extensions_to_try = [base_name]
        else:
            # Common file extensions to try
            common_extensions = [
                '.txt', '.pdf', '.doc', '.docx', '.xls', '.xlsx',
                '.jpg', '.jpeg', '.png', '.gif', '.webp',
                '.mp3', '.mp4', '.mov', '.avi',
                '.zip', '.tar', '.gz',
                '.py', '.js', '.html', '.css', '.json', '.xml',
            ]
            extensions_to_try = [base_name + ext for ext in common_extensions]
        
        best_match = None
        best_score = 0.0
        
        logger.info(f"OpenFileTool: Searching for '{file_name_clean}' in {len(folders)} folder(s)")
        
        for folder in folders:
            if not os.path.exists(folder):
                continue
            
            # Search for files with each extension
            for search_name in extensions_to_try:
                # Try exact match first
                exact_path = os.path.join(folder, search_name)
                if os.path.exists(exact_path) and os.path.isfile(exact_path):
                    logger.info(f"OpenFileTool: Found exact match: {exact_path}")
                    return exact_path
                
                # Try case-insensitive match
                try:
                    for item in os.listdir(folder):
                        if item.lower() == search_name.lower():
                            item_path = os.path.join(folder, item)
                            if os.path.isfile(item_path):
                                logger.info(f"OpenFileTool: Found case-insensitive match: {item_path}")
                                return item_path
                except PermissionError:
                    continue
                
                # Try fuzzy matching
                try:
                    for item in os.listdir(folder):
                        item_path = os.path.join(folder, item)
                        if os.path.isfile(item_path):
                            # Calculate similarity
                            item_lower = item.lower()
                            search_lower = search_name.lower()
                            
                            # Remove extensions for comparison
                            item_base = os.path.splitext(item_lower)[0]
                            search_base = os.path.splitext(search_lower)[0]
                            
                            score = SequenceMatcher(None, search_base, item_base).ratio()
                            
                            # Bonus if search name is contained in filename
                            if search_base in item_base or item_base in search_base:
                                score += 0.3
                            
                            # Bonus for exact extension match
                            if os.path.splitext(item_lower)[1] == os.path.splitext(search_lower)[1]:
                                score += 0.2
                            
                            if score > best_score:
                                best_score = score
                                best_match = item_path
                except PermissionError:
                    continue
            
            # Also try recursive search in subdirectories (limited depth)
            try:
                for root, dirs, files in os.walk(folder):
                    # Limit depth to 2 levels
                    depth = root[len(folder):].count(os.sep)
                    if depth > 2:
                        dirs[:] = []  # Don't recurse deeper
                        continue
                    
                    for file in files:
                        file_lower = file.lower()
                        search_lower = base_name.lower()
                        
                        # Calculate similarity
                        file_base = os.path.splitext(file_lower)[0]
                        search_base = os.path.splitext(search_lower)[0]
                        
                        score = SequenceMatcher(None, search_base, file_base).ratio()
                        
                        if search_base in file_base or file_base in search_base:
                            score += 0.3
                        
                        if score > best_score:
                            best_score = score
                            best_match = os.path.join(root, file)
            except PermissionError:
                continue
        
        if best_match and best_score > 0.5:  # Minimum similarity threshold
            logger.info(f"OpenFileTool: Found file '{best_match}' with similarity {best_score:.2f}")
            return best_match
        else:
            logger.warning(f"OpenFileTool: No file found matching '{file_name_clean}' (best score: {best_score:.2f})")
            return None
    
    def _run(self, file_name: str = "", search_folders: Optional[str] = None, **kwargs) -> str:
        """Execute file opening."""
        try:
            if not file_name:
                return "Error: No file name provided. Please specify which file to open."
            
            # Find the file
            file_path = self._find_file(file_name, search_folders)
            
            if not file_path:
                return f"Error: Could not find file '{file_name}'. Please check the file name and location."
            
            if not os.path.exists(file_path):
                return f"Error: File does not exist: {file_path}"
            
            # Open the file with default application
            system = platform.system()
            try:
                if system == 'Darwin':  # macOS
                    subprocess.run(['open', file_path], check=True)
                elif system == 'Windows':
                    os.startfile(file_path)
                else:  # Linux
                    subprocess.run(['xdg-open', file_path], check=True)
                
                logger.info(f"OpenFileTool: Successfully opened file: {file_path}")
                return f"Opened file: {os.path.basename(file_path)}"
            except Exception as e:
                logger.error(f"OpenFileTool: Error opening file: {e}", exc_info=True)
                return f"Error opening file: {str(e)}"
                
        except Exception as e:
            logger.error(f"Error in OpenFileTool: {e}", exc_info=True)
            return f"Error: {str(e)}"
    
    async def _arun(self, file_name: str = "", search_folders: Optional[str] = None, **kwargs) -> str:
        """Async execution."""
        return self._run(file_name=file_name, search_folders=search_folders, **kwargs)












