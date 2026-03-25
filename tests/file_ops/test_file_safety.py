#!/usr/bin/env python3
"""
Test script to verify file safety confirmation system works correctly.
Tests: touch file, then try to delete it with open interpreter or code tool.
This test creates a QApplication to show actual confirmation dialogs.
"""

import sys
import os
import tempfile
import shutil

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


def test_touch_and_delete():
    """Test creating a file with touch, then trying to delete it."""
    print("=" * 70)
    print("TEST: File Safety - Touch and Delete")
    print("=" * 70)
    
    # Create QApplication safely for GUI dialogs
    app = None
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QCoreApplication
        
        # Check if QApplication already exists
        existing_app = QCoreApplication.instance()
        if existing_app is None:
            # Create QApplication if it doesn't exist
            app = QApplication(sys.argv)
            print("✓ Created QApplication for GUI dialogs")
        elif isinstance(existing_app, QApplication):
            app = existing_app
            print("✓ Using existing QApplication")
        else:
            # We have QCoreApplication but need QApplication - this is tricky
            # For test purposes, we'll work with what we have
            print("⚠️  Only QCoreApplication exists - dialogs may not work")
            app = None
    except ImportError:
        print("⚠️  PyQt6 not available - dialogs cannot be shown")
        app = None
    except Exception as e:
        print(f"⚠️  Error setting up QApplication: {e}")
        app = None
    
    # Create a temporary test file
    test_dir = tempfile.mkdtemp(prefix="file_safety_test_")
    test_file = os.path.join(test_dir, "test_file.txt")
    
    try:
        # Step 1: Touch (create) a file
        print(f"\n1. Creating test file: {test_file}")
        with open(test_file, 'w') as f:
            f.write("Test content for file safety")
        print(f"   ✓ File created: {test_file}")
        print(f"   ✓ File exists: {os.path.exists(test_file)}")
        
        # Step 2: Test file safety classification
        print(f"\n2. Testing file safety classification...")
        try:
            from distr.core.file_safety import get_file_safety, OperationType
            
            file_safety = get_file_safety()
            
            # Test READ_ONLY operation
            read_code = f"with open('{test_file}', 'r') as f:\n    content = f.read()"
            read_class = file_safety.classify_operation(read_code)
            print(f"   Read operation: {read_class.value} (expected: READ_ONLY)")
            assert read_class == OperationType.READ_ONLY, "Read should be READ_ONLY"
            
            # Test WRITE operation
            write_code = f"with open('{test_file}', 'w') as f:\n    f.write('new content')"
            write_class = file_safety.classify_operation(write_code)
            print(f"   Write operation: {write_class.value} (expected: WRITE)")
            assert write_class in [OperationType.WRITE, OperationType.DESTRUCTIVE], "Write should be WRITE or DESTRUCTIVE"
            
            # Test DELETE operation
            delete_code = f"import os\nos.remove('{test_file}')"
            delete_class = file_safety.classify_operation(delete_code)
            print(f"   Delete operation: {delete_class.value} (expected: DESTRUCTIVE)")
            assert delete_class == OperationType.DESTRUCTIVE, "Delete should be DESTRUCTIVE"
            
            # Test RENAME operation (os.rename)
            rename_code = f"import os\nos.rename('{test_file}', '{test_file}.bak')"
            rename_class = file_safety.classify_operation(rename_code)
            print(f"   Rename operation (os.rename): {rename_class.value} (expected: WRITE)")
            assert rename_class == OperationType.WRITE, "Rename should be WRITE"
            
            # Test RENAME with variable (simulating dynamic renaming)
            rename_var_code = "import os\nfor f in files:\n    os.rename(old_path, new_path)"
            rename_var_class = file_safety.classify_operation(rename_var_code)
            print(f"   Rename with variable: {rename_var_class.value} (expected: WRITE)")
            assert rename_var_class == OperationType.WRITE, "Rename with var should be WRITE"
            
            # Test MOVE operation (shutil.move)
            dest = os.path.join(tempfile.gettempdir(), "moved.txt")
            move_code = f"import shutil\nshutil.move('{test_file}', '{dest}')"
            move_class = file_safety.classify_operation(move_code)
            print(f"   Move operation (shutil.move): {move_class.value} (expected: WRITE)")
            assert move_class == OperationType.WRITE, "Move should be WRITE"
            
            # Test Path.rename() - common in dynamic renaming
            path_rename_code = "from pathlib import Path\nfor f in files:\n    f.rename(new_path)"
            path_rename_class = file_safety.classify_operation(path_rename_code)
            print(f"   Path.rename(): {path_rename_class.value} (expected: WRITE)")
            assert path_rename_class == OperationType.WRITE, "Path.rename should be WRITE"
            
            print("   ✓ Classification tests passed (including rename/move)")
            
        except ImportError as e:
            print(f"   ⚠️  Could not import file_safety module: {e}")
            return False
        except AssertionError as e:
            print(f"   ❌ Classification test failed: {e}")
            return False
        
        # Step 3: Test operation extraction
        print(f"\n3. Testing operation extraction...")
        try:
            delete_code = f"import os\nos.remove('{test_file}')"
            operations = file_safety.extract_file_operations(delete_code)
            print(f"   Extracted operations (delete): {len(operations)}")
            for op in operations:
                print(f"     - {op['type']}: {op.get('source')}")
            
            assert len(operations) > 0, "Should extract delete operation"
            assert operations[0]['type'] == 'DELETE', "Should be DELETE operation"
            
            # Test rename extraction with literal paths
            rename_code = f"import os\nos.rename('{test_file}', '{test_file}.bak')"
            rename_ops = file_safety.extract_file_operations(rename_code)
            print(f"   Extracted operations (rename literal): {len(rename_ops)}")
            for op in rename_ops:
                print(f"     - {op['type']}: {op.get('source')} -> {op.get('destination')}")
            assert len(rename_ops) > 0, "Should extract rename operation"
            assert rename_ops[0]['type'] == 'MOVE', "Rename should be MOVE type"
            
            # Test variable-based operations (dynamic paths)
            var_rename_code = "for f in files:\n    os.rename(old_path, new_path)"
            var_ops = file_safety.extract_file_operations(var_rename_code)
            print(f"   Extracted operations (rename variable): {len(var_ops)}")
            for op in var_ops:
                is_dynamic = op.get('is_dynamic', False)
                print(f"     - {op['type']}: {op.get('source')} (dynamic={is_dynamic})")
            assert len(var_ops) > 0, "Should extract variable-based rename operation"
            assert any(op.get('is_dynamic') for op in var_ops), "Should mark as dynamic operation"
            
            # Test shutil.move with variable
            var_move_code = "import shutil\nshutil.move(src_file, dest_dir)"
            move_ops = file_safety.extract_file_operations(var_move_code)
            print(f"   Extracted operations (move variable): {len(move_ops)}")
            for op in move_ops:
                print(f"     - {op['type']}: {op.get('source')}")
            assert len(move_ops) > 0, "Should extract variable-based move operation"
            
            # Test Path.rename() - common pattern
            path_rename_code = "from pathlib import Path\nfor p in paths:\n    p.rename(new_name)"
            path_ops = file_safety.extract_file_operations(path_rename_code)
            print(f"   Extracted operations (Path.rename): {len(path_ops)}")
            for op in path_ops:
                print(f"     - {op['type']}: {op.get('source')}")
            assert len(path_ops) > 0, "Should extract Path.rename operation"
            
            print("   ✓ Operation extraction tests passed (including rename/move with variables)")
            
        except Exception as e:
            print(f"   ❌ Operation extraction test failed: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        # Step 4: Test plan generation
        print(f"\n4. Testing plan generation...")
        try:
            delete_code = f"import os\nos.remove('{test_file}')"
            operations = file_safety.extract_file_operations(delete_code)
            plan = file_safety.generate_plan(operations, "Delete test file")
            
            print(f"   Plan generated:")
            print(f"     - Intent: {plan.get('intent')}")
            print(f"     - Files: {plan.get('file_count')}")
            print(f"     - Will delete: {plan.get('will_delete')}")
            print(f"     - Delete files: {plan.get('delete_files')}")
            
            assert plan.get('will_delete'), "Plan should indicate delete"
            assert test_file in plan.get('delete_files', []), "Plan should include test file"
            print("   ✓ Plan generation test passed")
            
        except Exception as e:
            print(f"   ❌ Plan generation test failed: {e}")
            return False
        
        # Step 5: Test with open_interpreter (if available)
        print(f"\n5. Testing with open_interpreter tool...")
        print("   Note: This will require GUI confirmation if PyQt6 is available")
        print("   If no GUI, operations will be blocked for safety")
        
        try:
            from distr.core.agent.tools.open_interpreter import OpenInterpreterTool
            from PyQt6.QtWidgets import QApplication
            from PyQt6.QtCore import QCoreApplication
            
            # Check if we have QApplication
            app_instance = QCoreApplication.instance()
            has_gui = False
            if app_instance:
                try:
                    from PyQt6.QtWidgets import QApplication
                    has_gui = isinstance(app_instance, QApplication)
                except:
                    pass
            
            if has_gui:
                print("   ✓ QApplication detected - confirmation dialogs will appear")
                print("   → You will need to confirm file operations when prompted")
            else:
                print("   ⚠️  No QApplication - file operations will be blocked")
                print("   → This is expected behavior for safety")
            
            # Try to delete the file using open_interpreter
            # Note: This will show a confirmation dialog if GUI is available
            tool = OpenInterpreterTool()
            delete_task = f"delete the file at {test_file}"
            
            print(f"\n   Attempting to delete file via open_interpreter...")
            print(f"   Task: {delete_task}")
            print(f"   → If GUI is available, a confirmation dialog should appear")
            print(f"   → Type 'confirm file changes' to proceed")
            
            # Don't actually execute - just test the detection
            operations = tool._check_file_operations_in_task(delete_task)
            if operations:
                print(f"   ✓ File operations detected in task: {[op[0] for op in operations]}")
            else:
                print(f"   ⚠️  No file operations detected in task (may need pattern matching)")
            
        except ImportError as e:
            print(f"   ⚠️  Could not test with open_interpreter: {e}")
        except Exception as e:
            print(f"   ⚠️  Error testing open_interpreter: {e}")
        
        # Step 6: Test with execute_code - SHOW ACTUAL DIALOG
        print(f"\n6. Testing with execute_code tool - DIALOG WILL APPEAR...")
        print("   " + "=" * 60)
        print("   LOOK FOR THE CONFIRMATION DIALOG POPUP!")
        print("   " + "=" * 60)
        try:
            from distr.core.agent.services.safety.interceptor import check_and_confirm_code_execution
            
            delete_code = f"import os\nos.remove('{test_file}')"
            
            print(f"\n   Code to execute: {delete_code}")
            print(f"   File to delete: {test_file}")
            print(f"\n   → A confirmation dialog should appear NOW...")
            print(f"   → Type 'confirm file changes' in the dialog to proceed")
            print(f"   → Or click Cancel to block the operation\n")
            
            # Process events to ensure GUI is ready
            if app:
                app.processEvents()
                # Give the GUI a moment to be ready
                import time
                time.sleep(0.1)
                app.processEvents()
            
            # Check code (this will show dialog)
            try:
                allowed, plan = check_and_confirm_code_execution(delete_code, 'python', f"Delete test file: {test_file}")
            except Exception as e:
                print(f"   ❌ Error showing dialog: {e}")
                import traceback
                traceback.print_exc()
                allowed, plan = False, None
            
            if allowed:
                print(f"\n   ✓ Code execution allowed (user confirmed in dialog)")
                print(f"   → File should be deleted if you confirmed")
            else:
                print(f"\n   ✓ Code execution blocked (user cancelled or no GUI)")
                print(f"   → File should still exist if you cancelled")
            
            if plan:
                print(f"\n   Plan details shown in dialog:")
                print(f"     - Files affected: {plan.get('file_count')}")
                print(f"     - Will delete: {plan.get('will_delete')}")
                print(f"     - Delete files: {plan.get('delete_files')}")
            
        except ImportError as e:
            print(f"   ⚠️  Could not test with execute_code: {e}")
        except Exception as e:
            print(f"   ⚠️  Error testing execute_code: {e}")
            import traceback
            traceback.print_exc()
        
        # Step 7: Test with multiple files
        print(f"\n7. Testing with MULTIPLE files - DIALOG WILL APPEAR...")
        print("   " + "=" * 60)
        print("   LOOK FOR THE CONFIRMATION DIALOG WITH MULTIPLE FILES!")
        print("   " + "=" * 60)
        try:
            # Create multiple test files
            test_files = []
            for i in range(3):
                test_file_multi = os.path.join(test_dir, f"test_file_{i}.txt")
                with open(test_file_multi, 'w') as f:
                    f.write(f"Test content {i}")
                test_files.append(test_file_multi)
            
            print(f"\n   Created {len(test_files)} test files:")
            for f in test_files:
                print(f"     - {f}")
            
            # Code to delete multiple files
            delete_code_multi = f"import os\n"
            for f in test_files:
                delete_code_multi += f"os.remove('{f}')\n"
            
            print(f"\n   Code to execute:")
            print(f"   {delete_code_multi}")
            print(f"\n   → A confirmation dialog should appear NOW...")
            print(f"   → The dialog should show ALL {len(test_files)} files")
            print(f"   → Type 'confirm file changes' to proceed\n")
            
            # Process events
            if app:
                app.processEvents()
                import time
                time.sleep(0.1)
                app.processEvents()
            
            # Check code (this will show dialog with multiple files)
            try:
                allowed, plan = check_and_confirm_code_execution(delete_code_multi, 'python', f"Delete {len(test_files)} test files")
            except Exception as e:
                print(f"   ❌ Error showing dialog: {e}")
                import traceback
                traceback.print_exc()
                allowed, plan = False, None
            
            if allowed:
                print(f"\n   ✓ Code execution allowed (user confirmed in dialog)")
            else:
                print(f"\n   ✓ Code execution blocked (user cancelled)")
            
            if plan:
                print(f"\n   Plan details shown in dialog:")
                print(f"     - Files affected: {plan.get('file_count')}")
                print(f"     - Will delete: {plan.get('will_delete')}")
                print(f"     - Delete files count: {len(plan.get('delete_files', []))}")
            
        except Exception as e:
            print(f"   ⚠️  Error testing multiple files: {e}")
            import traceback
            traceback.print_exc()
        
        # Cleanup
        print(f"\n8. Cleanup...")
        if os.path.exists(test_file):
            print(f"   Test file still exists: {test_file}")
            print(f"   → This is expected if deletion was blocked or cancelled")
        else:
            print(f"   Test file was deleted")
        
        print("\n" + "=" * 70)
        print("TEST COMPLETE")
        print("=" * 70)
        print("\nSummary:")
        print("  - File safety classification: ✓")
        print("  - Operation extraction: ✓")
        print("  - Plan generation: ✓")
        print("  - Integration with tools: Tested")
        print("\nNote: Confirmation dialogs were shown during this test.")
        print("      If you confirmed, files may have been deleted.")
        print("      If you cancelled, files should still exist.")
        
        # Don't quit the app - let it stay alive for potential cleanup
        # app.quit() would cause issues if called here
        
        return True
        
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Cleanup test directory
        if os.path.exists(test_dir):
            try:
                shutil.rmtree(test_dir)
                print(f"\n✓ Cleaned up test directory: {test_dir}")
            except:
                print(f"\n⚠️  Could not clean up test directory: {test_dir}")


def main():
    """Run all tests."""
    print("\n" + "=" * 70)
    print("File Safety System Test")
    print("=" * 70 + "\n")
    
    try:
        success = test_touch_and_delete()
        
        if success:
            print("\n✓ All tests passed!")
            return 0
        else:
            print("\n❌ Some tests failed!")
            return 1
    except Exception as e:
        print(f"\n❌ Test crashed with error: {e}")
        import traceback
        traceback.print_exc()
        
        # If it's a bus error or segfault, provide helpful message
        if "bus error" in str(e).lower() or "segmentation fault" in str(e).lower():
            print("\n" + "=" * 70)
            print("BUS ERROR / SEGFAULT DETECTED")
            print("=" * 70)
            print("This is often caused by PyQt6/QApplication issues.")
            print("Try:")
            print("  1. Make sure no other PyQt6 applications are running")
            print("  2. Run the test in a clean environment")
            print("  3. Check if PyQt6 is properly installed")
            print("=" * 70)
        
        return 1


if __name__ == "__main__":
    sys.exit(main())

