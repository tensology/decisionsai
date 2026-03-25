#!/usr/bin/env python3
"""
Visual test for file operation confirmation dialog.
This test actually shows the dialog so you can interact with it.

Run with: python tests/test_open_interpreter_dialog_visual.py
"""

import sys
import os
import tempfile
import time

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


def test_show_confirmation_dialog():
    """Show the actual confirmation dialog for a delete operation."""
    print("=" * 70)
    print("VISUAL TEST: File Operation Confirmation Dialog")
    print("=" * 70)
    
    # Create QApplication
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QTimer
    
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    
    print("✓ QApplication created")
    
    # Create a test file
    test_dir = tempfile.mkdtemp(prefix="dialog_test_")
    test_file = os.path.join(test_dir, "test_delete_me.txt")
    
    with open(test_file, 'w') as f:
        f.write("This is a test file that will be 'deleted' after confirmation.")
    
    print(f"✓ Created test file: {test_file}")
    
    # Import the confirmation dialog
    try:
        from distr.gui.file_operation_confirmation_dialog import confirm_file_operations_with_plan
        from distr.core.file_safety import get_file_safety
        
        print("✓ Imported dialog module")
        
        # Generate a plan for the delete operation
        file_safety = get_file_safety()
        delete_code = f"os.remove('{test_file}')"
        
        operations = file_safety.extract_file_operations(delete_code)
        print(f"✓ Extracted {len(operations)} operations")
        
        if operations:
            plan = file_safety.generate_plan(operations, "Delete test file")
            # Add operation_type if not present
            if 'operation_type' not in plan and operations:
                plan['operation_type'] = operations[0].get('type', 'DELETE')
            
            print(f"✓ Generated plan: {plan.get('operation_type', 'UNKNOWN')}")
            print(f"  Files affected: {plan.get('delete_files', [])}")
            
            print("\n" + "=" * 70)
            print("SHOWING CONFIRMATION DIALOG...")
            print("Type 'confirm file changes' and click Confirm to approve")
            print("Or click Cancel to deny")
            print("=" * 70 + "\n")
            
            # Show the dialog
            confirmed = confirm_file_operations_with_plan(
                plan,
                require_confirmation_phrase=True,
                confirmation_phrase="confirm file changes"
            )
            
            print("\n" + "=" * 70)
            if confirmed:
                print("✓ USER CONFIRMED - File would be deleted")
                # Actually delete the file to demonstrate
                if os.path.exists(test_file):
                    os.remove(test_file)
                    print(f"✓ File deleted: {test_file}")
            else:
                print("✗ USER DENIED - File protected")
                print(f"  File still exists: {os.path.exists(test_file)}")
            print("=" * 70)
        else:
            print("⚠ No operations extracted - cannot show dialog")
            
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Cleanup
        import shutil
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir)
            print(f"✓ Cleaned up test directory")


def test_show_interpreter_log_dialog():
    """Show the interpreter log dialog."""
    print("\n" + "=" * 70)
    print("VISUAL TEST: Interpreter Log Dialog")
    print("=" * 70)
    
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QTimer
    
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    
    try:
        from distr.gui.interpreter_log_dialog import InterpreterLogDialog
        
        print("✓ Imported InterpreterLogDialog")
        
        # Create and show the dialog (cross-platform path)
        tmp_path = os.path.join(tempfile.gettempdir(), "test.txt").replace("\\", "/")
        dialog = InterpreterLogDialog(task_description=f"Delete file {tmp_path}")
        
        # Add some simulated log entries
        dialog.append_log("info", "Initializing open-interpreter...")
        dialog.append_log("info", f"Analyzing task: Delete file {tmp_path}")
        dialog.append_log("processing", "Generating code...")
        dialog.append_log("code", f"Code: os.remove('{tmp_path}')")
        dialog.append_log("warning", "⚠️ File operation detected - waiting for confirmation...")
        
        print("✓ Created dialog with sample log entries")
        print("\n" + "=" * 70)
        print("SHOWING INTERPRETER LOG DIALOG...")
        print("Close the dialog when done viewing")
        print("=" * 70 + "\n")
        
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        
        # Auto-close after 10 seconds
        QTimer.singleShot(10000, dialog.close)
        
        # Run event loop until dialog closes
        while dialog.isVisible():
            app.processEvents()
            time.sleep(0.1)
        
        print("✓ Dialog closed")
        
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()


def main():
    """Run visual tests."""
    print("\n" + "=" * 70)
    print("OPEN-INTERPRETER VISUAL DIALOG TESTS")
    print("These tests show actual dialogs - interact with them!")
    print("=" * 70 + "\n")
    
    # Test 1: Confirmation Dialog
    test_show_confirmation_dialog()
    
    # Test 2: Interpreter Log Dialog  
    test_show_interpreter_log_dialog()
    
    print("\n" + "=" * 70)
    print("VISUAL TESTS COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()

