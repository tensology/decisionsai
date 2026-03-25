"""
Minimal test to isolate the crash - just test ActionRecorder directly
"""

import sys
import os
import time

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

def test_minimal_recorder():
    """Minimal test - just create and start recorder"""
    print("=" * 80)
    print("MINIMAL ACTION RECORDER TEST")
    print("=" * 80)
    
    try:
        print("\n1. Importing ActionRecorder...")
        from distr.core.action_recorder import ActionRecorder
        print("   ✓ Import successful")
        
        print("\n2. Creating ActionRecorder instance...")
        recorder = ActionRecorder(action_id=1, action_title="Test")
        print("   ✓ Recorder created")
        
        print("\n3. Starting recording (this is where it might crash)...")
        recorder.start_recording(action_id=1, action_title="Test")
        print("   ✓ Recording started")
        
        print("\n4. Checking if active...")
        if recorder.is_active():
            print("   ✓ Recording is active")
        else:
            print("   ⚠ Recording is not active")
        
        print("\n5. Waiting 1 second...")
        time.sleep(1)
        
        print("\n6. Stopping recording...")
        filename = recorder.stop_recording()
        print(f"   ✓ Recording stopped, filename: {filename}")
        
        print("\n" + "=" * 80)
        print("TEST PASSED")
        print("=" * 80)
        return True
        
    except Exception as e:
        print(f"\n❌ EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_recorder_with_pyqt6():
    """Test recorder with PyQt6 app running"""
    print("\n" + "=" * 80)
    print("ACTION RECORDER WITH PYQT6 TEST")
    print("=" * 80)
    
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer
        from distr.core.action_recorder import ActionRecorder
        
        app = QApplication.instance()
        if not app:
            app = QApplication([])
        
        print("\n1. PyQt6 app created")
        
        recorder = None
        test_passed = [False]
        
        def start_recording():
            try:
                nonlocal recorder
                print("\n2. Creating recorder in QTimer callback...")
                recorder = ActionRecorder(action_id=1, action_title="Test")
                print("   ✓ Recorder created")
                
                print("\n3. Starting recording...")
                recorder.start_recording(action_id=1, action_title="Test")
                print("   ✓ Recording started")
                
                if recorder.is_active():
                    print("   ✓ Recording is active")
                    test_passed[0] = True
                    
                    # Stop after 1 second
                    QTimer.singleShot(1000, stop_recording)
                else:
                    print("   ⚠ Recording not active")
                    app.quit()
            except Exception as e:
                print(f"   ❌ Error: {e}")
                import traceback
                traceback.print_exc()
                app.quit()
        
        def stop_recording():
            try:
                print("\n4. Stopping recording...")
                if recorder:
                    recorder.stop_recording()
                    print("   ✓ Recording stopped")
                app.quit()
            except Exception as e:
                print(f"   ❌ Error stopping: {e}")
                app.quit()
        
        # Delay startup
        QTimer.singleShot(200, start_recording)
        
        print("Running event loop...")
        app.exec()
        
        if test_passed[0]:
            print("\n" + "=" * 80)
            print("TEST PASSED")
            print("=" * 80)
            return True
        else:
            print("\n" + "=" * 80)
            print("TEST FAILED")
            print("=" * 80)
            return False
            
    except Exception as e:
        print(f"\n❌ EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    print("\nRunning minimal test first...")
    result1 = test_minimal_recorder()
    
    if result1:
        print("\nMinimal test passed, testing with PyQt6...")
        result2 = test_recorder_with_pyqt6()
        sys.exit(0 if (result1 and result2) else 1)
    else:
        print("\nMinimal test failed - skipping PyQt6 test")
        sys.exit(1)




