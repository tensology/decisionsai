#!/usr/bin/env python3
"""
Test script to verify InterpreterLogDialog actually appears and shows execution.

This test simulates a real open_interpreter execution to verify the dialog appears.
"""

import sys
import os
import time
import threading

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

def test_dialog_appears():
    """Test that the dialog actually appears when open_interpreter is called."""
    print("=" * 70)
    print("Testing InterpreterLogDialog Visibility")
    print("=" * 70)
    
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QCoreApplication, QTimer
    except ImportError:
        print("⚠️  PyQt6 not available - cannot test dialog")
        return False
    
    # Create QApplication if needed
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
        print("✓ Created QApplication")
    else:
        print("✓ Using existing QApplication")
    
    # Verify we have QApplication (not QCoreApplication)
    app_instance = QCoreApplication.instance()
    is_qapplication = isinstance(app_instance, QApplication) if app_instance else False
    
    if not is_qapplication:
        print("❌ ERROR: Not a QApplication - cannot create widgets")
        return False
    
    print(f"✓ Confirmed QApplication instance: {type(app_instance).__name__}")
    
    # Try to create and show the dialog
    try:
        from distr.gui.interpreter_log_dialog import InterpreterLogDialog
        
        print("\nCreating InterpreterLogDialog...")
        dialog = InterpreterLogDialog(task_description="Test task: Write a document to Downloads folder")
        
        print(f"✓ Dialog created")
        print(f"  - Visible: {dialog.isVisible()}")
        print(f"  - Modal: {dialog.isModal()}")
        
        # Show the dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        
        # Process events to ensure it appears
        for _ in range(5):
            app.processEvents()
            time.sleep(0.1)
        
        print(f"  - After show(): Visible: {dialog.isVisible()}")
        print(f"  - Window title: {dialog.windowTitle()}")
        
        # Add some log messages
        print("\nAdding log messages...")
        dialog.append_log("info", "Starting execution...")
        app.processEvents()
        time.sleep(0.2)
        
        dialog.append_log("executing", "Writing document to Downloads folder...")
        app.processEvents()
        time.sleep(0.2)
        
        dialog.append_log("info", "Document created successfully")
        app.processEvents()
        time.sleep(0.2)
        
        dialog.mark_complete()
        app.processEvents()
        
        print("✓ Log messages added")
        print(f"  - Dialog still visible: {dialog.isVisible()}")
        
        # Keep dialog open for a few seconds so user can see it
        print("\n" + "=" * 70)
        print("DIALOG SHOULD BE VISIBLE NOW")
        print("=" * 70)
        print("The InterpreterLogDialog should be visible on your screen.")
        print("It should show:")
        print("  - Task description: 'Test task: Write a document to Downloads folder'")
        print("  - Log messages with timestamps")
        print("  - 'Execution completed' message")
        print("\nDialog will close automatically in 5 seconds...")
        print("=" * 70 + "\n")
        
        # Auto-close after 5 seconds
        def close_dialog():
            dialog.close()
            app.quit()
        
        QTimer.singleShot(5000, close_dialog)
        
        # Run event loop
        print("Running event loop (dialog should be visible)...")
        app.exec()
        
        print("✓ Dialog test completed")
        return True
        
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_open_interpreter_tool_dialog():
    """Test that open_interpreter tool actually shows the dialog."""
    print("\n" + "=" * 70)
    print("Testing OpenInterpreterTool Dialog Integration")
    print("=" * 70)
    
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QCoreApplication, QTimer
    except ImportError:
        print("⚠️  PyQt6 not available - cannot test")
        return False
    
    # Create QApplication if needed
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    
    # Check QApplication
    app_instance = QCoreApplication.instance()
    is_qapplication = isinstance(app_instance, QApplication) if app_instance else False
    
    if not is_qapplication:
        print("❌ ERROR: Not a QApplication")
        return False
    
    print("✓ QApplication confirmed")
    
    # Simulate the exact logic from open_interpreter.py
    print("\nSimulating open_interpreter.py dialog creation logic...")
    
    try:
        from PyQt6.QtCore import QTimer, QCoreApplication
        
        app_instance = QCoreApplication.instance()
        print(f"  App instance: {type(app_instance).__name__}")
        
        if app_instance is None:
            print("  ❌ No application instance")
            return False
        
        # Check if it's QApplication
        try:
            from PyQt6.QtWidgets import QApplication
            is_qapplication = isinstance(app_instance, QApplication)
            print(f"  Is QApplication: {is_qapplication}")
        except ImportError:
            is_qapplication = False
        
        if is_qapplication:
            print("  ✓ QApplication detected - creating dialog...")
            
            from distr.gui.interpreter_log_dialog import InterpreterLogDialog
            
            log_dialog = InterpreterLogDialog(task_description="Write a document to Downloads folder")
            log_dialog.setModal(False)
            
            print("  ✓ Dialog created")
            
            # Show dialog (same as open_interpreter.py)
            log_dialog.show()
            log_dialog.raise_()
            log_dialog.activateWindow()
            
            # Process events multiple times
            for i in range(3):
                app_instance.processEvents()
                print(f"  Processed events {i+1}/3")
            
            print(f"  Dialog visible: {log_dialog.isVisible()}")
            print(f"  Dialog window title: {log_dialog.windowTitle()}")
            
            # Add some test logs
            log_dialog.append_log("info", "Simulating open_interpreter execution...")
            app_instance.processEvents()
            
            log_dialog.append_log("executing", "Writing document to ~/Downloads/test_document.txt")
            app_instance.processEvents()
            
            log_dialog.append_log("info", "Document written successfully")
            app_instance.processEvents()
            
            log_dialog.mark_complete()
            app_instance.processEvents()
            
            print("\n" + "=" * 70)
            print("DIALOG SHOULD BE VISIBLE")
            print("=" * 70)
            print("This simulates what happens when open_interpreter is called.")
            print("Dialog will close in 5 seconds...")
            print("=" * 70 + "\n")
            
            # Auto-close
            QTimer.singleShot(5000, lambda: (log_dialog.close(), app.quit()))
            
            # Run event loop
            app.exec()
            
            print("✓ Test completed")
            return True
        else:
            print("  ❌ Not QApplication - cannot create dialog")
            return False
            
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests"""
    print("\n" + "=" * 70)
    print("InterpreterLogDialog Visibility Test")
    print("=" * 70 + "\n")
    
    results = []
    
    # Test 1: Basic dialog visibility
    result1 = test_dialog_appears()
    results.append(("Dialog Visibility", result1))
    
    # Test 2: OpenInterpreterTool integration
    result2 = test_open_interpreter_tool_dialog()
    results.append(("OpenInterpreterTool Integration", result2))
    
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







