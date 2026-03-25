"""
Test Action Recording with Delayed Startup

This test simulates the actual usage scenario with QTimer delay.
"""

import sys
import os
import time
from unittest.mock import Mock, patch

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from distr.gui.action import ActionWindow
from distr.core.db import get_session, Action, Base
from distr.core.paths import RECORDINGS_DIR
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import tempfile
import shutil


def test_action_recording_with_delay():
    """Test action recording using the QTimer delay approach"""
    print("=" * 80)
    print("ACTION RECORDING WITH DELAYED STARTUP TEST")
    print("=" * 80)
    
    # Create QApplication
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    
    # Create temporary database
    temp_dir = tempfile.mkdtemp()
    temp_db = os.path.join(temp_dir, 'test.db')
    
    try:
        # Create test database
        engine = create_engine(f'sqlite:///{temp_db}')
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        test_session = Session()
        
        # Ensure recordings directory exists
        test_recordings_dir = os.path.join(temp_dir, 'recordings')
        os.makedirs(test_recordings_dir, exist_ok=True)
        
        # Patch get_session
        def mock_get_session():
            class MockSession:
                def __enter__(self):
                    return test_session
                def __exit__(self, *args):
                    pass
            return MockSession()
        
        with patch('distr.gui.action.get_session', side_effect=mock_get_session):
            with patch('distr.gui.action.RECORDINGS_DIR', test_recordings_dir):
                with patch('distr.core.action_recorder.RECORDINGS_DIR', test_recordings_dir):
                    
                    print("\n1. Creating ActionWindow...")
                    window = ActionWindow()
                    window.show()
                    app.processEvents()
                    print("   ✓ Window created")
                    
                    print("\n2. Creating action in database...")
                    new_action = Action(
                        title="Test Delayed Action",
                        description="",
                        additional_trigger_words="[]",
                        play_sticky=False,
                        action="{}",
                        recording_filename=None
                    )
                    test_session.add(new_action)
                    test_session.commit()
                    action_id = new_action.id
                    print(f"   ✓ Action created with ID: {action_id}")
                    
                    print("\n3. Loading actions into window...")
                    window.load_actions()
                    app.processEvents()
                    print(f"   ✓ Actions loaded. Count: {window.action_list.count()}")
                    
                    print("\n4. Selecting the action...")
                    window.current_action_id = action_id
                    window.select_action_by_id(action_id)
                    app.processEvents()
                    print(f"   ✓ Action selected. Current ID: {window.current_action_id}")
                    
                    print("\n5. Starting recording with delayed startup...")
                    print("   Calling window.start_recording()...")
                    
                    # Track if recording started
                    recording_started = [False]
                    recording_failed = [False]
                    error_message = [None]
                    
                    def check_recording():
                        try:
                            if window.recorder and window.recorder.is_active():
                                recording_started[0] = True
                                print("   ✓ Recording started successfully!")
                                print("   ✓ Listeners are active")
                                
                                # Verify UI state
                                if not window.start_recording_button.isEnabled():
                                    print("   ✓ Start button correctly disabled")
                                if window.stop_recording_button.isEnabled():
                                    print("   ✓ Stop button correctly enabled")
                                if not window.action_list.isEnabled():
                                    print("   ✓ Action list correctly locked")
                                
                                # Stop recording after a brief moment
                                print("\n6. Stopping recording...")
                                window.stop_recording()
                                app.processEvents()
                                print("   ✓ Recording stopped")
                                
                                # Verify UI unlocked
                                if window.start_recording_button.isEnabled():
                                    print("   ✓ Start button correctly re-enabled")
                                if not window.stop_recording_button.isEnabled():
                                    print("   ✓ Stop button correctly disabled")
                                if window.action_list.isEnabled():
                                    print("   ✓ Action list correctly unlocked")
                                
                                app.quit()
                            elif recording_failed[0]:
                                print(f"   ❌ Recording failed: {error_message[0]}")
                                app.quit()
                            else:
                                # Check again in 100ms (give it time for the 200ms delay + startup)
                                QTimer.singleShot(100, check_recording)
                        except Exception as e:
                            print(f"   ❌ Error checking recording: {e}")
                            import traceback
                            traceback.print_exc()
                            app.quit()
                    
                    try:
                        # Start recording
                        window.start_recording()
                        app.processEvents()
                        
                        # Verify "Starting..." state
                        if window.start_recording_button.text() == "Starting...":
                            print("   ✓ Button shows 'Starting...' state")
                        
                        # Start checking for recording status after delay
                        # Wait 300ms to account for the 200ms delay + listener startup time
                        QTimer.singleShot(300, check_recording)
                        
                        # Run event loop for up to 5 seconds
                        timeout = time.time() + 5
                        iterations = 0
                        while time.time() < timeout:
                            app.processEvents()
                            if recording_started[0] or recording_failed[0]:
                                break
                            time.sleep(0.05)
                            iterations += 1
                            if iterations > 100:  # Safety limit
                                break
                        
                        # Process any remaining events
                        app.processEvents()
                        
                        if recording_started[0]:
                            print("\n" + "=" * 80)
                            print("TEST PASSED - Recording started successfully with delayed startup")
                            print("=" * 80)
                            return True
                        elif recording_failed[0]:
                            print("\n" + "=" * 80)
                            print(f"TEST FAILED - Recording failed to start: {error_message[0]}")
                            print("=" * 80)
                            return False
                        else:
                            print("\n" + "=" * 80)
                            print("TEST TIMEOUT - Recording didn't start within 5 seconds")
                            print("=" * 80)
                            if window.recorder:
                                print(f"   Recorder exists: {window.recorder}")
                                print(f"   Is active: {window.recorder.is_active() if window.recorder else 'N/A'}")
                            return False
                            
                    except Exception as e:
                        print(f"\n   ❌ EXCEPTION: {e}")
                        import traceback
                        traceback.print_exc()
                        return False
                    
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Cleanup
        if 'window' in locals():
            if hasattr(window, 'recorder') and window.recorder:
                try:
                    if window.recorder.is_active():
                        window.recorder.stop_recording()
                except:
                    pass
            window.close()
        app.processEvents()
        
        # Clean up database
        if 'test_session' in locals():
            test_session.close()
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == '__main__':
    success = test_action_recording_with_delay()
    sys.exit(0 if success else 1)




