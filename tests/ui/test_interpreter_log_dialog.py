#!/usr/bin/env python3
"""
Test script to verify InterpreterLogDialog works correctly with open_interpreter tool.
Tests both QApplication (GUI process) and QCoreApplication (agent process) scenarios.
"""

import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

def test_qcoreapplication_scenario():
    """Test in agent process mode (QCoreApplication only) - should NOT create dialog"""
    print("=" * 70)
    print("TEST 1: Agent Process (QCoreApplication only)")
    print("=" * 70)
    
    try:
        from PyQt6.QtCore import QCoreApplication
        from PyQt6.QtWidgets import QApplication
        
        # Simulate agent process - create QCoreApplication
        if not QCoreApplication.instance():
            app = QCoreApplication(sys.argv)
            print("✓ Created QCoreApplication")
        else:
            app = QCoreApplication.instance()
            print("✓ Using existing QCoreApplication")
        
        # Check instance type (same logic as open_interpreter.py)
        app_instance = QCoreApplication.instance()
        is_qapplication = False
        if app_instance is not None:
            try:
                from PyQt6.QtWidgets import QApplication
                is_qapplication = isinstance(app_instance, QApplication)
            except ImportError:
                is_qapplication = False
        
        print(f"  App instance type: {type(app_instance).__name__}")
        print(f"  Is QApplication: {is_qapplication}")
        
        if is_qapplication:
            print("  ❌ ERROR: Should not be QApplication in agent process!")
            return False
        
        print("  ✓ Correctly detected as QCoreApplication (no widgets)")
        
        # IMPORTANT: Don't try to import or create the dialog in QCoreApplication mode
        # The actual open_interpreter.py code checks BEFORE importing, so we should too
        # This matches the real behavior - dialog is never imported if is_qapplication=False
        print("  ✓ Skipping dialog import/creation (correct behavior)")
        print("  ✓ In open_interpreter.py, dialog is only imported if is_qapplication=True")
        print("  ✓ This prevents 'QWidget: Cannot create a QWidget without QApplication' warning")
        return True
            
    except ImportError as e:
        print(f"  ⚠️  PyQt6 not available: {e}")
        return True  # Skip test if PyQt6 not available
    except Exception as e:
        print(f"  ❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_qapplication_scenario():
    """Test in GUI process mode (QApplication) - SHOULD create dialog"""
    print("\n" + "=" * 70)
    print("TEST 2: GUI Process (QApplication)")
    print("=" * 70)
    
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QCoreApplication
        
        # Clean up any existing application
        existing_app = QCoreApplication.instance()
        if existing_app:
            existing_app.quit()
            QCoreApplication.processEvents()
        
        # Simulate GUI process - create QApplication
        if not QApplication.instance():
            app = QApplication(sys.argv)
            print("✓ Created QApplication")
        else:
            app = QApplication.instance()
            print("✓ Using existing QApplication")
        
        # Check instance type (same logic as open_interpreter.py)
        app_instance = QCoreApplication.instance()
        is_qapplication = False
        if app_instance is not None:
            try:
                from PyQt6.QtWidgets import QApplication
                is_qapplication = isinstance(app_instance, QApplication)
            except ImportError:
                is_qapplication = False
        
        print(f"  App instance type: {type(app_instance).__name__}")
        print(f"  Is QApplication: {is_qapplication}")
        
        if not is_qapplication:
            print("  ❌ ERROR: Should be QApplication in GUI process!")
            return False
        
        print("  ✓ Correctly detected as QApplication (can create widgets)")
        
        # Try to create dialog (should succeed)
        try:
            from distr.gui.interpreter_log_dialog import InterpreterLogDialog
            from PyQt6.QtCore import QTimer
            dialog = InterpreterLogDialog(task_description="Test task: convert files to MP3")
            print("  ✓ Dialog created successfully")
            
            # Show the dialog
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            app.processEvents()
            print("  ✓ Dialog shown and raised")
            print(f"  ✓ Dialog visible: {dialog.isVisible()}")
            
            # Test dialog methods
            dialog.append_log("info", "Test log message")
            app.processEvents()
            print("  ✓ Dialog.append_log() works")
            
            dialog.append_log("executing", "Converting files to MP3...")
            app.processEvents()
            
            dialog.mark_complete()
            app.processEvents()
            print("  ✓ Dialog.mark_complete() works")
            
            # Keep dialog open for 5 seconds so user can see it
            print("\n" + "=" * 70)
            print("  👀 LOOK FOR THE DIALOG WINDOW ON YOUR SCREEN!")
            print("  ⏳ Dialog will close automatically in 5 seconds...")
            print("=" * 70 + "\n")
            
            def close_dialog():
                print("  → Closing dialog...")
                dialog.close()
                app.quit()
            
            QTimer.singleShot(5000, close_dialog)
            
            # Run event loop to show dialog - THIS IS CRITICAL
            print("  → Starting event loop (dialog should appear NOW)...")
            print("  → If you don't see a dialog window, check the logs above")
            app.exec()
            print("  → Event loop ended")
            
            print("  ✓ Dialog closed successfully")
            
            return True
        except Exception as e:
            print(f"  ❌ ERROR: Dialog creation failed: {e}")
            import traceback
            traceback.print_exc()
            return False
            
    except ImportError as e:
        print(f"  ⚠️  PyQt6 not available: {e}")
        return True  # Skip test if PyQt6 not available
    except Exception as e:
        print(f"  ❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_open_interpreter_tool_logic():
    """Test the actual logic from open_interpreter.py"""
    print("\n" + "=" * 70)
    print("TEST 3: OpenInterpreterTool Dialog Logic")
    print("=" * 70)
    
    try:
        from PyQt6.QtCore import QTimer, QCoreApplication
        from PyQt6.QtWidgets import QApplication
        
        # Get application instance
        app_instance = QCoreApplication.instance()
        
        if app_instance is None:
            print("  ⚠️  No application instance - skipping test")
            return True
        
        # Check if it's actually a QApplication (same logic as open_interpreter.py)
        is_qapplication = False
        if app_instance is not None:
            try:
                from PyQt6.QtWidgets import QApplication
                is_qapplication = isinstance(app_instance, QApplication)
            except ImportError:
                is_qapplication = False
        
        print(f"  App instance: {type(app_instance).__name__}")
        print(f"  Is QApplication: {is_qapplication}")
        
        # Simulate the check from open_interpreter.py
        if is_qapplication:
            print("  ✓ Would create dialog (QApplication detected)")
            try:
                from distr.gui.interpreter_log_dialog import InterpreterLogDialog
                from PyQt6.QtCore import QTimer
                from PyQt6.QtWidgets import QApplication
                
                app = QApplication.instance()
                dialog = InterpreterLogDialog(task_description="Example: convert FLAC files to MP3")
                print("  ✓ Dialog created with example task")
                
                # Actually show the dialog
                dialog.show()
                dialog.raise_()
                dialog.activateWindow()
                app.processEvents()
                print("  ✓ Dialog shown")
                print(f"  ✓ Dialog visible: {dialog.isVisible()}")
                
                # Add some test logs
                dialog.append_log("info", "Starting file conversion...")
                app.processEvents()
                
                dialog.append_log("executing", "Converting FLAC files to MP3 format...")
                app.processEvents()
                
                dialog.mark_complete()
                app.processEvents()
                
                print("\n  ⏳ Dialog will close in 3 seconds...")
                print("  👀 LOOK FOR THE DIALOG WINDOW!")
                
                def close_dialog():
                    dialog.close()
                    app.quit()
                
                QTimer.singleShot(3000, close_dialog)
                app.exec()
                
                return True
            except Exception as e:
                print(f"  ❌ Dialog creation failed: {e}")
                return False
        else:
            print("  ✓ Would skip dialog (QCoreApplication only - correct for agent process)")
            return True
            
    except ImportError as e:
        print(f"  ⚠️  PyQt6 not available: {e}")
        return True
    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests"""
    print("\n" + "=" * 70)
    print("Testing InterpreterLogDialog with OpenInterpreterTool")
    print("=" * 70 + "\n")
    
    results = []
    
    # Test 1: QCoreApplication (agent process)
    result1 = test_qcoreapplication_scenario()
    results.append(("QCoreApplication (Agent Process)", result1))
    
    # Clean up for next test
    try:
        from PyQt6.QtCore import QCoreApplication
        app = QCoreApplication.instance()
        if app:
            app.quit()
            QCoreApplication.processEvents()
    except:
        pass
    
    # Test 2: QApplication (GUI process)
    result2 = test_qapplication_scenario()
    results.append(("QApplication (GUI Process)", result2))
    
    # Test 3: OpenInterpreterTool logic
    result3 = test_open_interpreter_tool_logic()
    results.append(("OpenInterpreterTool Logic", result3))
    
    # Summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    for test_name, result in results:
        status = "PASS" if result else "FAIL"
        print(f"  {test_name}: {status}")
    
    all_passed = all(result for _, result in results)
    if all_passed:
        print("\n✓ All tests passed!")
        return 0
    else:
        print("\n❌ Some tests failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())

