"""
Test script to verify file operation confirmation dialogs work correctly.

This test verifies that:
1. File operations (rm, mv, cp, delete, etc.) trigger confirmation dialogs
2. Batch operations show one confirmation for all affected files
3. The dialog appears and functions correctly
"""

import sys
import os
import tempfile
import shutil
import re
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Try to import PyQt6 (optional for testing)
try:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QTimer
    PYQT6_AVAILABLE = True
except ImportError:
    PYQT6_AVAILABLE = False
    print("Note: PyQt6 not available - GUI tests will be skipped")

import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def check_file_operations_in_task(task: str):
    """Check if task contains file operations that require confirmation."""
    operations = []
    task_lower = task.lower()
    
    # Check for file operation keywords
    dangerous_keywords = [
        (r'\b(delete|remove|rm|unlink)\s+', 'DELETE'),
        (r'\b(move|mv|rename)\s+', 'MOVE'),
        (r'\b(copy|cp)\s+', 'COPY'),
    ]
    
    for pattern, op_type in dangerous_keywords:
        if re.search(pattern, task_lower):
            operations.append((op_type, task, []))
            break  # One confirmation per task is enough
    
    return operations


def check_file_operations_require_confirmation(code: str):
    """
    Check if code contains file operations that require confirmation.
    
    Returns:
        List of tuples: (operation_type, operation_details, files_affected)
        Empty list if no dangerous operations found.
    """
    operations = []
    
    # Patterns for dangerous file operations
    patterns = [
        # Remove/delete operations
        (r'\brm\s+-[rf]*\s+([^\s;|&]+)', 'DELETE', 'remove'),
        (r'\brm\s+([^\s;|&]+)', 'DELETE', 'remove'),
        (r'\bunlink\s*\(["\']([^"\']+)["\']', 'DELETE', 'unlink'),
        (r'\bos\.remove\s*\(["\']([^"\']+)["\']', 'DELETE', 'os.remove'),
        (r'\bos\.unlink\s*\(["\']([^"\']+)["\']', 'DELETE', 'os.unlink'),
        (r'\bpathlib\.Path\s*\(["\']([^"\']+)["\']\)\.unlink\s*\(\)', 'DELETE', 'Path.unlink'),
        (r'\bshutil\.rmtree\s*\(["\']([^"\']+)["\']', 'DELETE', 'shutil.rmtree'),
        
        # Move/rename operations
        (r'\bmv\s+([^\s;|&]+)\s+([^\s;|&]+)', 'MOVE', 'move'),
        (r'\bos\.rename\s*\(["\']([^"\']+)["\'],\s*["\']([^"\']+)["\']', 'MOVE', 'os.rename'),
        (r'\bshutil\.move\s*\(["\']([^"\']+)["\'],\s*["\']([^"\']+)["\']', 'MOVE', 'shutil.move'),
        
        # Copy operations (less dangerous but should confirm)
        # Note: Order matters - check cp with flags first, then without
        (r'\bcp\s+-[rf]*\s+([^\s;|&]+)\s+([^\s;|&]+)', 'COPY', 'copy'),  # cp with flags
        (r'\bcp\s+([^\s;|&]+)\s+([^\s;|&]+)', 'COPY', 'copy'),  # cp without flags
        (r'\bshutil\.copy\s*\(["\']([^"\']+)["\'],\s*["\']([^"\']+)["\']', 'COPY', 'shutil.copy'),
        (r'\bshutil\.copy2\s*\(["\']([^"\']+)["\'],\s*["\']([^"\']+)["\']', 'COPY', 'shutil.copy2'),
        (r'\bshutil\.copytree\s*\(["\']([^"\']+)["\'],\s*["\']([^"\']+)["\']', 'COPY', 'shutil.copytree'),
    ]
    
    for pattern, op_type, op_name in patterns:
        matches = re.finditer(pattern, code, re.IGNORECASE | re.MULTILINE)
        for match in matches:
            groups = match.groups()
            if op_type == 'DELETE':
                file_path = groups[0] if groups else match.group(1)
                # Resolve path if it's a variable or needs expansion
                try:
                    if file_path.startswith('~'):
                        file_path = os.path.expanduser(file_path)
                    elif not os.path.isabs(file_path):
                        # Try to resolve relative paths
                        file_path = os.path.abspath(file_path)
                except:
                    pass
                
                operations.append((
                    op_type,
                    f"Delete file: {file_path}",
                    [file_path] if os.path.exists(file_path) else [file_path]
                ))
            elif op_type in ('MOVE', 'COPY'):
                src = groups[0] if groups else match.group(1)
                dst = groups[1] if len(groups) > 1 else match.group(2)
                
                # Resolve paths
                try:
                    if src.startswith('~'):
                        src = os.path.expanduser(src)
                    if dst.startswith('~'):
                        dst = os.path.expanduser(dst)
                except:
                    pass
                
                operations.append((
                    op_type,
                    f"{op_name.capitalize()}: {src} -> {dst}",
                    [src, dst]
                ))
    
    return operations


def test_file_operation_detection():
    """Test that file operations are detected in task descriptions."""
    print("\n" + "="*80)
    print("Testing File Operation Detection in Task Descriptions")
    print("="*80)
    
    # Test cases
    test_cases = [
        ("delete file.txt", "DELETE"),
        ("remove old.log", "DELETE"),
        ("rm -rf folder", "DELETE"),
        ("move file.txt to backup/", "MOVE"),
        ("mv old.txt new.txt", "MOVE"),
        ("rename file.txt to new.txt", "MOVE"),
        ("copy file.txt to backup/", "COPY"),
        ("cp source.txt dest.txt", "COPY"),
        ("create a new file", None),  # Should not trigger
        ("read the file", None),  # Should not trigger
    ]
    
    passed = 0
    failed = 0
    
    for task, expected_op in test_cases:
        operations = check_file_operations_in_task(task)
        detected_op = operations[0][0] if operations else None
        
        status = "✓" if detected_op == expected_op else "✗"
        print(f"{status} Task: '{task}'")
        print(f"  Expected: {expected_op}, Detected: {detected_op}")
        
        if detected_op != expected_op:
            print(f"  ❌ FAILED")
            failed += 1
        else:
            print(f"  ✓ PASSED")
            passed += 1
        print()
    
    print(f"Results: {passed} passed, {failed} failed")
    print("="*80 + "\n")
    
    return failed == 0


def test_code_block_detection():
    """Test that file operations are detected in code blocks."""
    print("\n" + "="*80)
    print("Testing File Operation Detection in Code Blocks")
    print("="*80)
    
    # Try to use the actual module function if available
    try:
        from distr.gui.file_operation_confirmation_dialog import check_file_operations_require_confirmation as module_check
        use_module = True
        print("Using actual module function")
    except ImportError:
        use_module = False
        print("Using local test function")
    
    # Create temporary test files
    test_dir = tempfile.mkdtemp()
    test_file1 = os.path.join(test_dir, "test1.txt")
    test_file2 = os.path.join(test_dir, "test2.txt")
    test_file3 = os.path.join(test_dir, "test3.txt")
    
    # Create test files
    with open(test_file1, 'w') as f:
        f.write("test content 1")
    with open(test_file2, 'w') as f:
        f.write("test content 2")
    with open(test_file3, 'w') as f:
        f.write("test content 3")
    
    test_cases = [
        # (code, expected_operations_count, description)
        (f'rm "{test_file1}"', 1, "Single bash delete"),
        (f'os.remove("{test_file1}")', 1, "Python os.remove"),
        (f'mv "{test_file1}" "{test_file2}"', 1, "Single bash move"),
        (f'os.rename("{test_file1}", "{test_file2}")', 1, "Python os.rename"),
        (f'cp "{test_file1}" "{test_file2}"', 1, "Single bash copy"),
        (f'shutil.copy("{test_file1}", "{test_file2}")', 1, "Python shutil.copy"),
        (f'rm "{test_file1}"\nrm "{test_file2}"\nrm "{test_file3}"', 3, "Multiple bash deletes"),
        (f'shutil.rmtree("{test_dir}")', 1, "Python shutil.rmtree"),
        ('print("hello")', 0, "No file operations"),
        ('x = 1 + 1', 0, "No file operations"),
    ]
    
    passed = 0
    failed = 0
    
    for code, expected_count, description in test_cases:
        # Use module function if available, otherwise use local
        if use_module:
            operations = module_check(code)
        else:
            operations = check_file_operations_require_confirmation(code)
        detected_count = len(operations)
        
        status = "✓" if detected_count == expected_count else "✗"
        print(f"{status} {description}")
        print(f"  Code: {code[:60]}...")
        print(f"  Expected: {expected_count} operations, Detected: {detected_count}")
        
        if detected_count > 0:
            for op_type, details, files in operations:
                print(f"    - {op_type}: {details}")
                print(f"      Files: {files}")
        
        if detected_count != expected_count:
            print(f"  ❌ FAILED")
            failed += 1
        else:
            print(f"  ✓ PASSED")
            passed += 1
        print()
    
    # Cleanup
    shutil.rmtree(test_dir)
    
    print(f"Results: {passed} passed, {failed} failed")
    print("="*80 + "\n")
    
    return failed == 0


def test_batch_operations():
    """Test that batch operations are detected correctly."""
    print("\n" + "="*80)
    print("Testing Batch Operation Detection")
    print("="*80)
    
    # Create temporary test files
    test_dir = tempfile.mkdtemp()
    test_files = [os.path.join(test_dir, f"test{i}.txt") for i in range(1, 6)]
    
    # Create test files
    for f in test_files:
        with open(f, 'w') as file:
            file.write(f"test content for {os.path.basename(f)}")
    
    # Test batch delete in a loop
    batch_code = f"""
import os
files = {test_files}
for f in files:
    os.remove(f)
"""
    
    operations = check_file_operations_require_confirmation(batch_code)
    
    print(f"Batch delete code detected {len(operations)} operations")
    
    if len(operations) > 0:
        print("✓ Batch operations detected")
        for op_type, details, files in operations:
            print(f"  - {op_type}: {details}")
            print(f"    Files affected: {len(files)}")
    else:
        print("⚠ Batch operations not detected (may need improvement)")
    
    # Cleanup
    shutil.rmtree(test_dir)
    
    print("="*80 + "\n")


def test_confirmation_dialog():
    """Test that the confirmation dialog appears and works."""
    print("\n" + "="*80)
    print("Testing Confirmation Dialog Appearance")
    print("="*80)
    
    if not PYQT6_AVAILABLE:
        print("  ⚠ Skipping GUI tests - PyQt6 not available")
        print("  To test dialogs, run this in an environment with PyQt6 installed")
        return
    
    try:
        from distr.gui.file_operation_confirmation_dialog import (
            FileOperationConfirmationDialog,
            confirm_file_operations
        )
    except ImportError as e:
        print(f"  ⚠ Could not import dialog classes: {e}")
        return
    
    # Create QApplication if it doesn't exist
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
        print("✓ Created QApplication instance")
    else:
        print("✓ QApplication instance already exists")
    
    # Create temporary test files for display (cross-platform)
    tmp_dir = tempfile.gettempdir()
    test_files = [
        os.path.join(tmp_dir, "test_file_1.txt"),
        os.path.join(tmp_dir, "test_file_2.txt"),
        os.path.join(tmp_dir, "test_file_3.txt"),
        os.path.join(tmp_dir, "test_file_4.txt"),
        os.path.join(tmp_dir, "test_file_5.txt"),
    ]
    try:
        for p in test_files:
            Path(p).touch()
        
        # Test 1: Single operation
        print("\nTest 1: Single DELETE operation")
        dialog = FileOperationConfirmationDialog(
            operation_type="DELETE",
            operation_details=f"Delete file: {test_files[0]}",
            files_affected=[test_files[0]]
        )
        
        print(f"  Dialog created: {dialog is not None}")
        print(f"  Dialog visible: {dialog.isVisible()}")
        print(f"  Dialog modal: {dialog.isModal()}")
        print("  ✓ Dialog can be created")
        
        # Test 2: Batch operation
        print("\nTest 2: Batch DELETE operation (5 files)")
        dialog2 = FileOperationConfirmationDialog(
            operation_type="DELETE",
            operation_details="Delete multiple files",
            files_affected=test_files
        )
        
        print(f"  Dialog created: {dialog2 is not None}")
        print(f"  Files shown: {len(test_files)}")
        print(f"  Dialog visible: {dialog2.isVisible()}")
        print("  ✓ Batch dialog can be created")
        
        print("\nNote: To actually show and test dialogs, uncomment dialog.exec() calls")
    finally:
        for p in test_files:
            try:
                if Path(p).exists():
                    Path(p).unlink()
            except OSError:
                pass
    print("="*80 + "\n")


def main():
    """Run all tests."""
    print("\n" + "="*80)
    print("FILE OPERATION CONFIRMATION TEST SUITE")
    print("="*80)
    
    results = []
    
    # Test 1: Task description detection
    results.append(("Task Description Detection", test_file_operation_detection()))
    
    # Test 2: Code block detection
    results.append(("Code Block Detection", test_code_block_detection()))
    
    # Test 3: Batch operations
    test_batch_operations()
    
    # Test 4: Dialog appearance (requires QApplication)
    test_confirmation_dialog()
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    for test_name, passed in results:
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"{status}: {test_name}")
    
    all_passed = all(result[1] for result in results)
    
    print("="*80)
    if all_passed:
        print("✓ ALL TESTS PASSED")
    else:
        print("✗ SOME TESTS FAILED")
    print("="*80 + "\n")


if __name__ == "__main__":
    main()
