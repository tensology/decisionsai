"""
Test Action Recording with Worker Thread

This test simulates the actual usage scenario with the QThread worker.
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


def test_action_recording_with_worker():
    """Test action recording using the worker thread approach"""
    print("=" * 80)
    print("ACTION RECORDING WITH WORKER THREAD TEST")
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
                                title="Test Worker Action",
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
                            
                            print("\n5. Starting recording with worker thread...")
                            print("   Calling window.start_recording()...")
                            
                            # Track if recording started
                            recording_started = [False]
                            recording_failed = [False]
                            
                            def check_recording():
                                if window.recorder and window.recorder.is_active():
                                    recording_started[0] = True
                                    print("   ✓ Recording started successfully!")
                                    print("   ✓ Worker thread completed")
                                    
                                    # Stop recording
                                    print("\n6. Stopping recording...")
                                    window.stop_recording()
                                    app.processEvents()
                                    print("   ✓ Recording stopped")
                                    
                                    app.quit()
                                elif recording_failed[0]:
                                    print("   ❌ Recording failed")
                                    app.quit()
                                else:
                                    # Check again in 100ms
                                    QTimer.singleShot(100, check_recording)
                            
                            try:
                                window.start_recording()
                                app.processEvents()
                                
                                # Start checking for recording status
                                QTimer.singleShot(200, check_recording)
                                
                                # Run event loop for up to 5 seconds
                                timeout = time.time() + 5
                                while time.time() < timeout:
                                    app.processEvents()
                                    if recording_started[0] or recording_failed[0]:
                                        break
                                    time.sleep(0.1)
                                
                                if recording_started[0]:
                                    print("\n" + "=" * 80)
                                    print("TEST PASSED - Recording started successfully with worker thread")
                                    print("=" * 80)
                                    return True
                                elif recording_failed[0]:
                                    print("\n" + "=" * 80)
                                    print("TEST FAILED - Recording failed to start")
                                    print("=" * 80)
                                    return False
                                else:
                                    print("\n" + "=" * 80)
                                    print("TEST TIMEOUT - Recording didn't start within 5 seconds")
                                    print("=" * 80)
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
            if hasattr(window, 'recorder_worker') and window.recorder_worker:
                try:
                    window.recorder_worker.quit()
                    window.recorder_worker.wait(1000)
                except:
                    pass
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
    success = test_action_recording_with_worker()
    sys.exit(0 if success else 1)

