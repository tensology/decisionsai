"""
File Safety System - Implements comprehensive file operation safety according to spec.

This module provides:
- Operation classification (READ_ONLY, WRITE, DESTRUCTIVE)
- Plan generation before writes
- Confirmation phrase requirement
- Quarantine for deletes
- Audit logging
"""

import logging
import os
import re
import json
from typing import List, Tuple, Dict, Optional, Set
from enum import Enum
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class OperationType(Enum):
    """Operation classification types."""
    READ_ONLY = "READ_ONLY"
    WRITE = "WRITE"
    DESTRUCTIVE = "DESTRUCTIVE"


class FileOperationSafety:
    """File operation safety system implementing the spec requirements."""
    
    # Default confirmation phrases
    CONFIRM_FILE_CHANGES = "confirm file changes"
    CONFIRM_PERMANENT_DELETE = "confirm permanent delete"
    
    # High-risk thresholds
    MAX_FILES_WITHOUT_EXTRA_CONFIRMATION = 10
    QUARANTINE_ENABLED = True
    
    # Default safe roots
    DEFAULT_SAFE_ROOTS = [
        os.path.expanduser("~/Downloads"),
        os.path.expanduser("~/Documents"),
    ]
    
    # Blocked roots
    BLOCKED_ROOTS = [
        "/System",
        "/Library",
        "/Applications",
        os.path.expanduser("~/.ssh"),
    ]
    
    def __init__(self, log_dir: Optional[str] = None):
        """
        Initialize file safety system.
        
        Args:
            log_dir: Directory for audit logs (default: ~/.decisionsai/file_safety_logs)
        """
        if log_dir is None:
            log_dir = os.path.join(os.path.expanduser("~"), ".decisionsai", "file_safety_logs")
        
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        
        # Quarantine directory
        self.quarantine_base = os.path.join(os.path.expanduser("~"), "Documents", "InterpreterQuarantine")
        os.makedirs(self.quarantine_base, exist_ok=True)
    
    def classify_operation(self, code: str, task: str = "") -> OperationType:
        """
        Classify an operation as READ_ONLY, WRITE, or DESTRUCTIVE.
        
        Args:
            code: The code to be executed
            task: Optional task description for context
            
        Returns:
            OperationType classification
        """
        code_lower = code.lower()
        task_lower = task.lower() if task else ""
        combined = f"{code_lower} {task_lower}"
        
        # Check for destructive operations first (highest priority)
        destructive_patterns = [
            r'\brm\s+-[rf]',  # rm -rf or rm -r
            r'\brm\s+.*\*',  # rm with wildcard
            r'\bos\.remove\s*\(',
            r'\bos\.unlink\s*\(',
            r'\bpathlib\.Path.*\.unlink\s*\(',
            r'\.unlink\s*\(',  # Any .unlink() call
            r'\bshutil\.rmtree\s*\(',
            r'\bunlink\s*\(',
            r'\bdelete\s+',
            r'\bremove\s+file',
            r'\btruncate\s*\(',
            r'\bformat\s+',
            r'\berase\s+',
            r'\brmdir\s*\(',  # os.rmdir
            r'\bos\.rmdir\s*\(',
        ]
        
        for pattern in destructive_patterns:
            if re.search(pattern, combined, re.IGNORECASE):
                return OperationType.DESTRUCTIVE
        
        # Check for write operations
        write_patterns = [
            r'\bopen\s*\([^)]*["\']w',  # open(..., 'w')
            r'\bopen\s*\([^)]*["\']a',  # open(..., 'a')
            r'\bopen\s*\([^)]*["\']x',  # open(..., 'x')
            r'\bopen\s*\([^)]*["\']\+',  # open(..., 'r+', 'w+', etc.)
            r'\bwith\s+open\s*\([^)]*["\']w',  # with open(..., 'w')
            r'\bwith\s+open\s*\([^)]*["\']a',  # with open(..., 'a')
            r'\bPath\s*\([^)]*\)\.write_text\s*\(',  # Path(...).write_text()
            r'\bPath\s*\([^)]*\)\.write_bytes\s*\(',  # Path(...).write_bytes()
            r'\bpathlib\.Path\s*\([^)]*\)\.write_text\s*\(',  # pathlib.Path(...).write_text()
            r'\bpathlib\.Path\s*\([^)]*\)\.write_bytes\s*\(',  # pathlib.Path(...).write_bytes()
            r'\.write_text\s*\(',  # Any .write_text() call
            r'\.write_bytes\s*\(',  # Any .write_bytes() call
            r'\bmv\s+',
            r'\bmove\s+',
            r'\brename\s+',
            r'\brenam',  # covers "renaming", "renamed"
            r'\bos\.rename\s*\(',
            r'\.rename\s*\(',  # Any .rename() call (Path.rename)
            r'\bos\.renames\s*\(',  # os.renames
            r'\bos\.replace\s*\(',  # os.replace (atomic rename)
            r'\bshutil\.move\s*\(',
            r'\bcp\s+',
            r'\bcopy\s+',
            r'\bshutil\.copy',
            r'\bshutil\.copy2',
            r'\bshutil\.copytree',
            r'\bwrite\s*\(',
            r'\bwritelines\s*\(',
            r'\bchmod\s*\(',
            r'\bchown\s*\(',
            r'\btouch\s+',  # touch creates files
            r'\bos\.makedirs\s*\(',  # Creating directories
            r'\bos\.mkdir\s*\(',
            r'\.mkdir\s*\(',  # Path.mkdir()
            r'\bclean\s*up',  # task keywords: "cleanup", "clean up"
            r'\breorganiz',  # task keywords: "reorganize", "reorganizing"
            r'\brestructur',  # task keywords: "restructure", "restructuring"
        ]
        
        for pattern in write_patterns:
            if re.search(pattern, combined, re.IGNORECASE):
                return OperationType.WRITE
        
        # Default to READ_ONLY if no write patterns found
        return OperationType.READ_ONLY
    
    def _analyze_code_for_file_creation(self, code: str) -> Dict[str, any]:
        """
        Analyze code to detect file creation patterns, especially in loops.
        Returns info about what files will be created.
        """
        analysis = {
            'creates_files_in_loop': False,
            'loop_variable': None,
            'output_pattern': None,
            'input_folder': None,
            'output_folder': None,
            'file_extension_pattern': None,
            'estimated_count': 0
        }
        
        code_lower = code.lower()
        
        # Detect loops (for, while)
        loop_patterns = [
            r'\bfor\s+(\w+)\s+in\s+',
            r'\bwhile\s+',
        ]
        
        has_loop = False
        loop_var = None
        for pattern in loop_patterns:
            match = re.search(pattern, code, re.IGNORECASE)
            if match:
                has_loop = True
                if match.groups():
                    loop_var = match.group(1)
                break
        
        if not has_loop:
            return analysis
        
        # Look for file creation in loops
        # Patterns that suggest file creation: subprocess with output, os.path.join creating output paths
        creation_patterns = [
            r'subprocess\.(run|call|Popen)',
            r'ffmpeg',
            r'os\.path\.join\s*\([^)]*,\s*[^)]*\.(mp3|wav|flac|jpg|png|txt|pdf)',
            r'\.mp3|\.wav|\.flac|\.jpg|\.png|\.txt|\.pdf',
        ]
        
        creates_in_loop = any(re.search(p, code, re.IGNORECASE) for p in creation_patterns)
        
        if creates_in_loop:
            analysis['creates_files_in_loop'] = True
            analysis['loop_variable'] = loop_var
            
            # Try to extract input folder and file pattern
            # Look for os.listdir or similar
            listdir_match = re.search(r'os\.listdir\s*\(["\']([^"\']+)["\']', code, re.IGNORECASE)
            if listdir_match:
                analysis['input_folder'] = listdir_match.group(1)
            
            # Look for folder variable assignments
            folder_match = re.search(r'folder\s*=\s*["\']([^"\']+)["\']', code, re.IGNORECASE)
            if folder_match:
                analysis['input_folder'] = folder_match.group(1)
            
            # Look for output folder - handle both string literals and variable references
            # Pattern 1: mp3_dir = os.path.join(folder, "MP3") or os.path.join("path", "MP3")
            output_match = re.search(r'(mp3_dir|output_dir|out_dir)\s*=\s*os\.path\.join\s*\(([^)]+)\)', code, re.IGNORECASE)
            if output_match:
                join_args = output_match.group(2)
                # Try to extract folder path from join arguments
                # If it's a variable like "folder", use the input_folder we found
                if 'folder' in join_args.lower() and analysis.get('input_folder'):
                    # Build output folder path
                    output_name_match = re.search(r'["\']([^"\']+)["\']', join_args, re.IGNORECASE)
                    if output_name_match:
                        output_subdir = output_name_match.group(1)
                        analysis['output_folder'] = os.path.join(analysis['input_folder'], output_subdir)
                else:
                    # Try to find string literal in join
                    folder_match = re.search(r'["\']([^"\']+)["\']', join_args, re.IGNORECASE)
                    if folder_match:
                        analysis['output_folder'] = folder_match.group(1)
            
            # Try to count actual files in input folder
            if analysis['input_folder']:
                try:
                    input_path = Path(analysis['input_folder'])
                    if input_path.exists() and input_path.is_dir():
                        # Count files that match the pattern (e.g., .flac files)
                        # Look for file extension filter in code
                        ext_match = re.search(r'\.endswith\s*\(["\']\.(\w+)["\']', code, re.IGNORECASE)
                        if ext_match:
                            ext = '.' + ext_match.group(1).lower()
                            files = [f for f in os.listdir(input_path) if f.lower().endswith(ext) and (input_path / f).is_file()]
                            analysis['estimated_count'] = len(files)
                            analysis['file_extension_pattern'] = ext
                except Exception as e:
                    logger.debug(f"Could not count files in {analysis['input_folder']}: {e}")
        
        return analysis
    
    def extract_file_operations(self, code: str) -> List[Dict[str, any]]:
        """
        Extract file operations from code.
        
        Returns:
            List of operation dicts with type, paths, and details
        """
        operations = []
        
        # First, analyze code for file creation patterns (especially in loops)
        creation_analysis = self._analyze_code_for_file_creation(code)
        
        # Patterns for file operations - with hardcoded paths (string literals)
        patterns = [
            # Delete operations
            (r'\brm\s+-[rf]*\s+([^\s;|&]+)', 'DELETE', 'rm'),
            (r'\brm\s+([^\s;|&]+)', 'DELETE', 'rm'),
            (r'\bos\.remove\s*\(["\']([^"\']+)["\']', 'DELETE', 'os.remove'),
            (r'\bos\.unlink\s*\(["\']([^"\']+)["\']', 'DELETE', 'os.unlink'),
            (r'\bpathlib\.Path\s*\(["\']([^"\']+)["\']\)\.unlink\s*\(\)', 'DELETE', 'Path.unlink'),
            (r'\bshutil\.rmtree\s*\(["\']([^"\']+)["\']', 'DELETE', 'shutil.rmtree'),
            (r'\bunlink\s*\(["\']([^"\']+)["\']', 'DELETE', 'unlink'),
            
            # Move/rename operations
            (r'\bmv\s+([^\s;|&]+)\s+([^\s;|&]+)', 'MOVE', 'mv'),
            (r'\bos\.rename\s*\(["\']([^"\']+)["\'],\s*["\']([^"\']+)["\']', 'MOVE', 'os.rename'),
            (r'\bshutil\.move\s*\(["\']([^"\']+)["\'],\s*["\']([^"\']+)["\']', 'MOVE', 'shutil.move'),
            
            # Copy operations
            (r'\bcp\s+-[rf]*\s+([^\s;|&]+)\s+([^\s;|&]+)', 'COPY', 'cp'),
            (r'\bcp\s+([^\s;|&]+)\s+([^\s;|&]+)', 'COPY', 'cp'),
            (r'\bshutil\.copy\s*\(["\']([^"\']+)["\'],\s*["\']([^"\']+)["\']', 'COPY', 'shutil.copy'),
            (r'\bshutil\.copy2\s*\(["\']([^"\']+)["\'],\s*["\']([^"\']+)["\']', 'COPY', 'shutil.copy2'),
            (r'\bshutil\.copytree\s*\(["\']([^"\']+)["\'],\s*["\']([^"\']+)["\']', 'COPY', 'shutil.copytree'),
            
            # Write operations (file creation/modification)
            (r'\bopen\s*\(["\']([^"\']+)["\'].*["\']w', 'WRITE', 'open(w)'),
            (r'\bopen\s*\(["\']([^"\']+)["\'].*["\']a', 'WRITE', 'open(a)'),
            (r'\bwith\s+open\s*\(["\']([^"\']+)["\'].*["\']w', 'WRITE', 'with open(w)'),
            (r'\bwith\s+open\s*\(["\']([^"\']+)["\'].*["\']a', 'WRITE', 'with open(a)'),
            (r'\bPath\s*\(["\']([^"\']+)["\']\)\.write_text\s*\(', 'WRITE', 'Path.write_text'),
            (r'\bPath\s*\(["\']([^"\']+)["\']\)\.write_bytes\s*\(', 'WRITE', 'Path.write_bytes'),
            (r'\bpathlib\.Path\s*\(["\']([^"\']+)["\']\)\.write_text\s*\(', 'WRITE', 'pathlib.Path.write_text'),
            (r'\bpathlib\.Path\s*\(["\']([^"\']+)["\']\)\.write_bytes\s*\(', 'WRITE', 'pathlib.Path.write_bytes'),
            (r'\btouch\s+([^\s;|&]+)', 'WRITE', 'touch'),
        ]
        
        # Additional patterns for operations with VARIABLE arguments (not just string literals)
        # These help detect dynamic operations even when we can't extract the actual paths
        variable_patterns = [
            # Delete operations with variables
            (r'\bos\.remove\s*\(\s*(\w+)', 'DELETE', 'os.remove(var)'),
            (r'\bos\.unlink\s*\(\s*(\w+)', 'DELETE', 'os.unlink(var)'),
            (r'\.unlink\s*\(\s*\)', 'DELETE', '.unlink()'),  # path_obj.unlink()
            (r'\bshutil\.rmtree\s*\(\s*(\w+)', 'DELETE', 'shutil.rmtree(var)'),
            (r'\bos\.rmdir\s*\(\s*(\w+)', 'DELETE', 'os.rmdir(var)'),
            
            # Move/rename operations with variables
            (r'\bos\.rename\s*\(\s*(\w+)\s*,', 'MOVE', 'os.rename(var,...)'),
            (r'\bos\.renames\s*\(\s*(\w+)\s*,', 'MOVE', 'os.renames(var,...)'),
            (r'\bos\.replace\s*\(\s*(\w+)\s*,', 'MOVE', 'os.replace(var,...)'),
            (r'\bshutil\.move\s*\(\s*(\w+)\s*,', 'MOVE', 'shutil.move(var,...)'),
            (r'\.rename\s*\(\s*(\w+)', 'MOVE', '.rename(var)'),  # path_obj.rename(new_path)
            
            # Copy operations with variables
            (r'\bshutil\.copy\s*\(\s*(\w+)\s*,', 'COPY', 'shutil.copy(var,...)'),
            (r'\bshutil\.copy2\s*\(\s*(\w+)\s*,', 'COPY', 'shutil.copy2(var,...)'),
            (r'\bshutil\.copytree\s*\(\s*(\w+)\s*,', 'COPY', 'shutil.copytree(var,...)'),
            
            # Write operations with variables
            (r'\bopen\s*\(\s*(\w+)\s*,\s*["\']w', 'WRITE', 'open(var, w)'),
            (r'\bopen\s*\(\s*(\w+)\s*,\s*["\']a', 'WRITE', 'open(var, a)'),
            (r'\.write_text\s*\(', 'WRITE', '.write_text()'),
            (r'\.write_bytes\s*\(', 'WRITE', '.write_bytes()'),
            
            # Directory creation
            (r'\bos\.makedirs\s*\(\s*(\w+)', 'WRITE', 'os.makedirs(var)'),
            (r'\bos\.mkdir\s*\(\s*(\w+)', 'WRITE', 'os.mkdir(var)'),
            (r'\.mkdir\s*\(', 'WRITE', '.mkdir()'),
        ]
        
        for pattern, op_type, op_name in patterns:
            matches = re.finditer(pattern, code, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                groups = match.groups()
                if op_type == 'DELETE':
                    file_path = groups[0] if groups else match.group(1)
                    file_path = self._resolve_path(file_path)
                    operations.append({
                        'type': op_type,
                        'name': op_name,
                        'source': file_path,
                        'destination': None,
                        'will_overwrite': False,
                        'will_delete': True,
                    })
                elif op_type in ('MOVE', 'COPY'):
                    src = groups[0] if groups else match.group(1)
                    dst = groups[1] if len(groups) > 1 else match.group(2)
                    src = self._resolve_path(src)
                    dst = self._resolve_path(dst)
                    will_overwrite = os.path.exists(dst) if dst else False
                    operations.append({
                        'type': op_type,
                        'name': op_name,
                        'source': src,
                        'destination': dst,
                        'will_overwrite': will_overwrite,
                        'will_delete': op_type == 'MOVE',
                    })
                elif op_type == 'WRITE':
                    file_path = groups[0] if groups else match.group(1)
                    file_path = self._resolve_path(file_path)
                    will_overwrite = os.path.exists(file_path)
                    operations.append({
                        'type': op_type,
                        'name': op_name,
                        'source': file_path,
                        'destination': None,
                        'will_overwrite': will_overwrite,
                        'will_delete': False,
                    })
        
        # Also check for variable-based operations (paths we can't resolve but operations we can detect)
        # This ensures we detect operations even when paths are in variables
        for pattern, op_type, op_name in variable_patterns:
            matches = re.finditer(pattern, code, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                groups = match.groups()
                # For variable patterns, we use the variable name or a placeholder as source
                var_name = groups[0] if groups else '(dynamic)'
                
                # Skip if this looks like a string literal path (already caught by patterns above)
                if var_name.startswith('"') or var_name.startswith("'"):
                    continue
                
                operations.append({
                    'type': op_type,
                    'name': op_name,
                    'source': f'${var_name}' if var_name != '(dynamic)' else '(dynamic path)',
                    'destination': '(dynamic)' if op_type in ('MOVE', 'COPY') else None,
                    'will_overwrite': op_type in ('WRITE', 'MOVE', 'COPY'),  # Assume possible overwrite
                    'will_delete': op_type in ('DELETE', 'MOVE'),
                    'is_dynamic': True,  # Flag to indicate path is from variable
                })
        
        # If we detected file creation in a loop, add a single operation representing all files
        if creation_analysis.get('creates_files_in_loop') and creation_analysis.get('estimated_count', 0) > 0:
            # Add a single CREATE operation that represents all files created in the loop
            output_folder = creation_analysis.get('output_folder')
            input_folder = creation_analysis.get('input_folder')
            file_count = creation_analysis.get('estimated_count', 0)
            ext_pattern = creation_analysis.get('file_extension_pattern', '')
            
            # Use output folder path if available, otherwise use input folder
            base_path = output_folder if output_folder else input_folder
            
            operations.append({
                'type': 'WRITE',
                'name': 'subprocess_file_creation_loop',
                'source': f'{base_path}/*{ext_pattern}' if base_path else '(dynamic output files)',
                'destination': None,
                'will_overwrite': False,  # Assume new files (can't know for sure without checking)
                'will_delete': False,
                'is_dynamic': True,
                'is_loop_creation': True,
                'loop_count': file_count,
                'input_folder': input_folder,
                'output_folder': output_folder,
                'file_extension_pattern': ext_pattern,
            })
        
        # Also detect subprocess calls that create files (like ffmpeg, imagemagick, etc.)
        subprocess_patterns = [
            (r'subprocess\.(run|call|Popen).*ffmpeg', 'WRITE', 'ffmpeg_conversion'),
            (r'subprocess\.(run|call|Popen).*convert', 'WRITE', 'imagemagick_conversion'),
            (r'subprocess\.(run|call|Popen).*magick', 'WRITE', 'imagemagick_conversion'),
        ]
        
        for pattern, op_type, op_name in subprocess_patterns:
            if re.search(pattern, code, re.IGNORECASE | re.DOTALL):
                # Look for output path in the subprocess call
                # Try to find output path pattern: os.path.join(...) or variable assignment
                output_path_match = re.search(r'out_path\s*=\s*os\.path\.join\s*\(([^)]+)\)', code, re.IGNORECASE)
                if not output_path_match:
                    output_path_match = re.search(r'["\']([^"\']+\.(mp3|wav|flac|jpg|png|txt|pdf))["\']', code, re.IGNORECASE)
                
                if output_path_match:
                    # We found an output path pattern, but it's likely in a loop
                    # The creation_analysis should have caught this, but add it here too if needed
                    pass
        
        # Deduplicate operations by (type, source) to avoid double-counting
        seen = set()
        unique_operations = []
        for op in operations:
            key = (op['type'], op['source'])
            if key not in seen:
                seen.add(key)
                unique_operations.append(op)
        
        return unique_operations
    
    def _resolve_path(self, path: str) -> str:
        """Resolve a path (expand ~, make absolute, etc.)."""
        try:
            if path.startswith('~'):
                path = os.path.expanduser(path)
            if not os.path.isabs(path):
                path = os.path.abspath(path)
            return os.path.normpath(path)
        except (OSError, ValueError):
            return path
    
    def check_high_risk(self, operations: List[Dict]) -> Tuple[bool, List[str]]:
        """
        Check if operations are high-risk.
        
        Returns:
            (is_high_risk, reasons)
        """
        reasons = []
        
        # Count files affected
        file_count = len(set([op['source'] for op in operations if op.get('source')]))
        if file_count > self.MAX_FILES_WITHOUT_EXTRA_CONFIRMATION:
            reasons.append(f"More than {self.MAX_FILES_WITHOUT_EXTRA_CONFIRMATION} files affected ({file_count})")
        
        # Check for operations in blocked roots
        for op in operations:
            source = op.get('source', '')
            dest = op.get('destination', '')
            for path in [source, dest]:
                if path:
                    for blocked in self.BLOCKED_ROOTS:
                        if path.startswith(blocked):
                            reasons.append(f"Operation in blocked root: {blocked}")
                            break
        
        # Check for recursive operations
        for op in operations:
            if op['type'] == 'DELETE' and '*' in str(op.get('source', '')):
                reasons.append("Recursive delete with wildcard detected")
        
        # Check for overwrites
        for op in operations:
            if op.get('will_overwrite'):
                path = op.get('destination') or op.get('source')
                # Only add to reasons if it's a real path (not a placeholder)
                if path and path not in ['(dynamic)', '(dynamic path)', '$target'] and not (isinstance(path, str) and path.startswith('$')):
                    reasons.append(f"Will overwrite existing file: {path}")
                elif op.get('is_dynamic'):
                    # For dynamic operations, just note that overwrites are possible
                    reasons.append("Will overwrite existing files (paths determined at runtime)")
        
        return len(reasons) > 0, reasons
    
    def generate_plan(self, operations: List[Dict], task: str = "") -> Dict[str, any]:
        """
        Generate a detailed plan for file operations.
        
        Returns:
            Plan dict with all required information
        """
        all_files = set()
        all_dirs = set()
        overwrites = []
        deletes = []
        
        def count_files_in_directory(dir_path: Path) -> None:
            """Recursively add all files and directories in a directory to the sets."""
            try:
                if dir_path.exists() and dir_path.is_dir():
                    for root, dirs, files in os.walk(dir_path):
                        # Add all files in this directory
                        for f in files:
                            all_files.add(str(Path(root) / f))
                        # Add all subdirectories
                        for d in dirs:
                            all_dirs.add(str(Path(root) / d))
            except (OSError, PermissionError) as e:
                logger.warning(f"Could not recursively count files in {dir_path}: {e}")
        
        for op in operations:
            op_type = op.get('type', '')
            source = op.get('source')
            destination = op.get('destination')
            
            # Handle source path
            if source and source not in ['(dynamic)', '(dynamic path)', '$target'] and not (isinstance(source, str) and source.startswith('$')):
                source_path = Path(source)
                if source_path.exists():
                    if source_path.is_file():
                        all_files.add(str(source_path))
                    elif source_path.is_dir():
                        all_dirs.add(str(source_path))
                        
                        # For DELETE, COPY, or MOVE operations on directories, count all contents
                        if op_type in ('DELETE', 'COPY', 'MOVE'):
                            count_files_in_directory(source_path)
            
            # Handle destination path
            if destination and destination not in ['(dynamic)', '(dynamic path)', '$target'] and not (isinstance(destination, str) and destination.startswith('$')):
                dest_path = Path(destination)
                if dest_path.exists():
                    if dest_path.is_file():
                        all_files.add(str(dest_path))
                    elif dest_path.is_dir():
                        all_dirs.add(str(dest_path))
                        # For COPY operations, if destination is a directory, files will be copied into it
                        # Count files that will be created (same as source if copying a directory)
                        if op_type == 'COPY' and source:
                            source_path = Path(source)
                            if source_path.exists() and source_path.is_dir():
                                # Files will be copied into destination directory
                                count_files_in_directory(source_path)
            
            # For WRITE operations, count files that will be created/modified
            if op_type == 'WRITE' and source:
                if source not in ['(dynamic)', '(dynamic path)', '$target'] and not (isinstance(source, str) and source.startswith('$')):
                    write_path = Path(source)
                    # If it's a new file, it will be created
                    if not write_path.exists():
                        all_files.add(str(write_path))
                    else:
                        # Existing file will be modified
                        all_files.add(str(write_path))
            
            # Track overwrites
            if op.get('will_overwrite'):
                path = destination or source
                # Filter out placeholder strings
                if path and path not in ['(dynamic)', '(dynamic path)', '$target'] and not (isinstance(path, str) and path.startswith('$')):
                    overwrites.append(path)
            
            # Track deletes
            if op.get('will_delete') or op_type == 'DELETE':
                path = source
                # Filter out placeholder strings
                if path and path not in ['(dynamic)', '(dynamic path)', '$target'] and not (isinstance(path, str) and path.startswith('$')):
                    deletes.append(path)
        
        # For dynamic operations, try to estimate counts from code patterns
        dynamic_file_count = 0
        dynamic_dir_count = 0
        has_dynamic = any(op.get('is_dynamic') for op in operations)
        
        if has_dynamic:
            # Try to estimate from code patterns in operations
            for op in operations:
                if op.get('is_dynamic'):
                    op_type = op.get('type', '')
                    # Look for common patterns that indicate multiple files
                    # This is a heuristic - we can't know exact count without executing
                    if op_type in ('DELETE', 'COPY', 'MOVE', 'WRITE'):
                        # If operation is in a loop or processes multiple items, estimate
                        # We'll show this as "at least X" or "multiple files"
                        dynamic_file_count += 1  # At least one file affected
                        if op_type in ('DELETE', 'COPY', 'MOVE'):
                            # These operations often affect multiple files
                            dynamic_file_count += 4  # Estimate: likely multiple files
        
        # Calculate operation-specific counts
        files_to_create = 0
        files_to_modify = 0
        files_to_delete = 0
        files_to_move = 0
        files_to_copy = 0
        files_to_rename = 0
        
        def is_rename_operation(source: str, destination: str) -> bool:
            """Check if a MOVE operation is actually a rename (same directory, different filename)."""
            if not source or not destination:
                return False
            if source in ['(dynamic)', '(dynamic path)', '$target'] or source.startswith('$'):
                return False
            if destination in ['(dynamic)', '(dynamic path)', '$target'] or destination.startswith('$'):
                return False
            try:
                source_path = Path(source)
                dest_path = Path(destination)
                # If source exists and is a file, check if it's a rename
                if source_path.exists() and source_path.is_file():
                    # Rename: same parent directory, different filename
                    if source_path.parent == dest_path.parent and source_path.name != dest_path.name:
                        return True
                # If source is a directory, check if destination is in same parent with different name
                elif source_path.exists() and source_path.is_dir():
                    if source_path.parent == dest_path.parent and source_path.name != dest_path.name:
                        return True
            except Exception:
                pass
            return False
        
        for op in operations:
            op_type = op.get('type', '')
            source = op.get('source')
            destination = op.get('destination')
            
            # Check if this MOVE operation is actually a rename
            is_rename = False
            if op_type == 'MOVE' and source and destination:
                is_rename = is_rename_operation(source, destination)
            
            if source and source not in ['(dynamic)', '(dynamic path)', '$target'] and not (isinstance(source, str) and source.startswith('$')):
                source_path = Path(source)
                if source_path.exists():
                    if source_path.is_file():
                        if op_type == 'DELETE':
                            files_to_delete += 1
                        elif op_type == 'MOVE':
                            if is_rename:
                                files_to_rename += 1
                            else:
                                files_to_move += 1
                        elif op_type == 'COPY':
                            files_to_copy += 1
                        elif op_type == 'WRITE':
                            files_to_modify += 1
                    elif source_path.is_dir():
                        # Count files in directory for this operation
                        try:
                            file_count = 0
                            for root, dirs, files in os.walk(source_path):
                                file_count += len(files)
                            if op_type == 'DELETE':
                                files_to_delete += file_count
                            elif op_type == 'MOVE':
                                if is_rename:
                                    files_to_rename += file_count
                                else:
                                    files_to_move += file_count
                            elif op_type == 'COPY':
                                files_to_copy += file_count
                        except (OSError, PermissionError):
                            pass
            elif op.get('is_dynamic'):
                # For dynamic operations, add to estimates
                if op_type == 'DELETE':
                    files_to_delete += dynamic_file_count
                elif op_type == 'MOVE':
                    # Can't determine if it's rename for dynamic operations, count as move
                    files_to_move += dynamic_file_count
                elif op_type == 'COPY':
                    files_to_copy += dynamic_file_count
                elif op_type == 'WRITE':
                    files_to_modify += dynamic_file_count
        
        # For WRITE operations that create new files
        # Check for loop-based file creation first
        loop_creation_ops = [op for op in operations if op.get('is_loop_creation')]
        if loop_creation_ops:
            for op in loop_creation_ops:
                loop_count = op.get('loop_count', 0)
                if loop_count > 0:
                    files_to_create += loop_count
        else:
            # Regular WRITE operations
            for op in operations:
                if op.get('type') == 'WRITE' and not op.get('is_loop_creation'):
                    source = op.get('source')
                    if source and source not in ['(dynamic)', '(dynamic path)', '$target'] and not (isinstance(source, str) and source.startswith('$')):
                        write_path = Path(source)
                        if not write_path.exists():
                            files_to_create += 1
                        else:
                            files_to_modify += 1
        
        # Generate detailed outcome description based on operations and actual paths
        outcome_parts = []
        outcome_details = []
        
        # Check for loop-based file creation operations (like ffmpeg conversion)
        loop_creation_ops = [op for op in operations if op.get('is_loop_creation')]
        if loop_creation_ops:
            # Get details from first loop creation op
            loop_op = loop_creation_ops[0]
            loop_count = loop_op.get('loop_count', 0)
            input_folder = loop_op.get('input_folder', '')
            output_folder = loop_op.get('output_folder', '')
            ext_pattern = loop_op.get('file_extension_pattern', '')
            
            if loop_count > 0:
                outcome_parts.append(f"Create {loop_count} new file(s)")
                if input_folder:
                    # Try to list actual input files
                    try:
                        input_path = Path(input_folder)
                        if input_path.exists() and input_path.is_dir():
                            if ext_pattern:
                                input_files = [f for f in os.listdir(input_path) 
                                             if f.lower().endswith(ext_pattern.lower()) and (input_path / f).is_file()]
                                if input_files:
                                    outcome_details.append(f"  Will convert {len(input_files)} file(s) from: {os.path.basename(input_folder)}")
                                    outcome_details.append(f"  Input files: {', '.join(input_files[:5])}")
                                    if len(input_files) > 5:
                                        outcome_details.append(f"  ... and {len(input_files) - 5} more")
                            else:
                                # No extension pattern, but we know the folder
                                outcome_details.append(f"  Will process files from: {os.path.basename(input_folder)}")
                    except Exception as e:
                        logger.debug(f"Could not list input files: {e}")
                
                if output_folder:
                    outcome_details.append(f"  Output location: {os.path.basename(output_folder)} folder")
        
        # Build outcome summary with specific details for non-loop operations
        if files_to_create > 0 and not loop_creation_ops:
            outcome_parts.append(f"Create {files_to_create} new file(s)")
            # Show specific files that will be created
            created_files = []
            for op in operations:
                if op.get('type') == 'WRITE' and not op.get('is_loop_creation'):
                    dest = op.get('destination') or op.get('source')
                    if dest and dest not in ['(dynamic)', '(dynamic path)', '$target'] and not dest.startswith('$'):
                        try:
                            dest_path = Path(dest)
                            if not dest_path.exists():
                                created_files.append(os.path.basename(dest))
                        except (OSError, ValueError):
                            pass
            if created_files:
                outcome_details.append(f"  New files: {', '.join(created_files[:5])}")
                if len(created_files) > 5:
                    outcome_details.append(f"  ... and {len(created_files) - 5} more")
        
        if files_to_modify > 0:
            outcome_parts.append(f"Modify {files_to_modify} existing file(s)")
        
        if files_to_rename > 0:
            outcome_parts.append(f"Rename {files_to_rename} file(s)")
            # Show rename details if available
            rename_details = []
            for op in operations:
                if op.get('type') == 'MOVE':
                    src = op.get('source', '')
                    dst = op.get('destination', '')
                    if src and dst and src not in ['(dynamic)', '(dynamic path)', '$target']:
                        try:
                            src_path = Path(src)
                            dst_path = Path(dst)
                            if src_path.parent == dst_path.parent:  # Same directory = rename
                                rename_details.append(f"  {os.path.basename(src)} → {os.path.basename(dst)}")
                        except (OSError, ValueError):
                            pass
            if rename_details:
                outcome_details.extend(rename_details[:5])
                if len(rename_details) > 5:
                    outcome_details.append(f"  ... and {len(rename_details) - 5} more renames")
        
        if files_to_move > 0:
            outcome_parts.append(f"Move {files_to_move} file(s)")
        
        if files_to_copy > 0:
            outcome_parts.append(f"Copy {files_to_copy} file(s)")
        
        if files_to_delete > 0:
            outcome_parts.append(f"Delete {files_to_delete} file(s)")
            # Show specific files/folders that will be deleted
            if deletes:
                delete_names = [os.path.basename(d) for d in deletes[:5]]
                outcome_details.append(f"  Will delete: {', '.join(delete_names)}")
                if len(deletes) > 5:
                    outcome_details.append(f"  ... and {len(deletes) - 5} more")
        
        # Build final outcome summary
        outcome_summary = ". ".join(outcome_parts) + "." if outcome_parts else "No files will be affected."
        if outcome_details:
            outcome_summary += "\n\n" + "\n".join(outcome_details)
        
        plan = {
            'intent': task or "File operations requested",
            'file_count': len(all_files) if not has_dynamic else max(len(all_files), dynamic_file_count),
            'directory_count': len(all_dirs) if not has_dynamic else max(len(all_dirs), dynamic_dir_count),
            'files': sorted(list(all_files)),
            'directories': sorted(list(all_dirs)),
            'operations': operations,
            'will_overwrite': len(overwrites) > 0,
            'overwrite_files': overwrites,
            'will_delete': len(deletes) > 0,
            'delete_files': deletes,
            'rollback_strategy': self._generate_rollback_strategy(operations),
            'timestamp': datetime.now().isoformat(),
            # Operation-specific counts
            'files_to_create': files_to_create,
            'files_to_modify': files_to_modify,
            'files_to_delete': files_to_delete,
            'files_to_move': files_to_move,
            'files_to_copy': files_to_copy,
            'files_to_rename': files_to_rename,
            # Outcome summary
            'outcome_summary': outcome_summary,
        }
        
        return plan
    
    def _generate_rollback_strategy(self, operations: List[Dict]) -> str:
        """Generate a rollback strategy description."""
        strategies = []
        
        for op in operations:
            source = op.get('source', '')
            destination = op.get('destination', '')
            
            # Skip placeholder strings
            if source in ['(dynamic)', '(dynamic path)', '$target'] or (isinstance(source, str) and source.startswith('$')):
                source = None
            if destination in ['(dynamic)', '(dynamic path)', '$target'] or (isinstance(destination, str) and destination.startswith('$')):
                destination = None
            
            if op['type'] == 'DELETE':
                if source:
                    strategies.append(f"Deleted file {source} can be recovered from quarantine")
                elif op.get('is_dynamic'):
                    strategies.append("Deleted files can be recovered from quarantine")
            elif op['type'] == 'MOVE':
                if source and destination:
                    strategies.append(f"Moved file {source} -> {destination} can be moved back")
                elif op.get('is_dynamic'):
                    strategies.append("Moved files can be moved back (paths determined at runtime)")
            elif op['type'] == 'COPY':
                if source and destination:
                    strategies.append(f"Copied file {source} -> {destination} can be deleted")
                elif op.get('is_dynamic'):
                    strategies.append("Copied files can be deleted (paths determined at runtime)")
            elif op['type'] == 'WRITE' and op.get('will_overwrite'):
                if source:
                    strategies.append(f"Overwritten file {source} - original content may be lost")
                elif op.get('is_dynamic'):
                    strategies.append("Overwritten files - original content may be lost (paths determined at runtime)")
        
        return "\n".join(strategies) if strategies else "No rollback needed (no destructive operations)"
    
    def get_quarantine_path(self, file_path: str) -> str:
        """Get quarantine path for a file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        quarantine_dir = os.path.join(self.quarantine_base, timestamp)
        os.makedirs(quarantine_dir, exist_ok=True)
        
        # Preserve directory structure in quarantine
        file_name = os.path.basename(file_path)
        return os.path.join(quarantine_dir, file_name)
    
    def log_operation(self, event_type: str, data: Dict[str, any]):
        """Log an operation event to audit trail."""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'event_type': event_type,
            'data': data,
        }
        
        log_file = os.path.join(self.log_dir, f"file_safety_{datetime.now().strftime('%Y%m%d')}.log")
        
        try:
            with open(log_file, 'a') as f:
                f.write(json.dumps(log_entry) + '\n')
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")
    
    def check_safe_root(self, path: str) -> bool:
        """Check if a path is in a safe root."""
        path = os.path.normpath(path)
        for safe_root in self.DEFAULT_SAFE_ROOTS:
            safe_root = os.path.normpath(safe_root)
            if path.startswith(safe_root):
                return True
        return False


# Global instance
_file_safety = None


def get_file_safety() -> FileOperationSafety:
    """Get or create global file safety instance."""
    global _file_safety
    if _file_safety is None:
        _file_safety = FileOperationSafety()
    return _file_safety

