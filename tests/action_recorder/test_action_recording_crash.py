"""
Simple test to reproduce the action recording crash.

Run this to identify the exact crash point.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from PyQt6.QtWidgets import QApplication
from distr.gui.action import ActionWindow
from distr.core.db import get_session, Action
import traceback


def test_crash_reproduction():
    """Reproduce the crash: create action, then start recording"""
    print("=" * 80)
    print("ACTION RECORDING CRASH REPRODUCTION TEST")
    print("=" * 80)
    
    # Create QApplication
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    
    try:
        print("\n1. Creating ActionWindow...")
        window = ActionWindow()
        window.show()
        app.processEvents()
        print("   ✓ Window created")
        
        print("\n2. Creating action in database...")
        with get_session() as session:
            new_action = Action(
                title="Test Crash Action",
                description="",
                additional_trigger_words="[]",
                play_sticky=False,
                action="{}",
                recording_filename=None
            )
            session.add(new_action)
            session.commit()
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
        
        print("\n5. Starting recording (THIS IS WHERE THE CRASH HAPPENS)...")
        print("   Calling window.start_recording()...")
        try:
            window.start_recording()
            app.processEvents()
            print("   ✓ Recording started without crash!")
            
            if window.recorder and window.recorder.is_active():
                print("   ✓ Recorder is active")
                print("\n6. Stopping recording...")
                window.stop_recording()
                app.processEvents()
                print("   ✓ Recording stopped")
            else:
                print("   ⚠ Recorder not active after start")
                
        except Exception as e:
            print(f"\n   ❌ CRASH DETECTED!")
            print(f"   Error type: {type(e).__name__}")
            print(f"   Error message: {str(e)}")
            print("\n   Full traceback:")
            traceback.print_exc()
            return False
        
        print("\n" + "=" * 80)
        print("TEST PASSED - No crash detected")
        print("=" * 80)
        return True
        
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
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


if __name__ == '__main__':
    success = test_crash_reproduction()
    sys.exit(0 if success else 1)




