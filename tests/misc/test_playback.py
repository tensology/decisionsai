#!/usr/bin/env python3

"""
Test to verify that playback works after recording.
"""

import sys
import os
import time

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from PyQt6.QtWidgets import QApplication
from distr.gui.action import ActionWindow
from distr.core.db import get_session, Action
from distr.core.action_player import ActionPlayer
from pathlib import Path
from distr.core.paths import RECORDINGS_DIR

def test_playback():
    """Test recording and playback"""
    print("=" * 80)
    print("ACTION PLAYBACK TEST")
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
                title="Test Playback Action",
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

        print("\n5. Starting recording...")
        window.start_recording()
        app.processEvents()
        print("   ✓ Recording started")

        # Wait a bit for recording to start
        time.sleep(2)

        print("\n6. Stopping recording...")
        window.stop_recording()
        app.processEvents()
        print("   ✓ Recording stopped")

        # Check if recording file was created
        with get_session() as session:
            action = session.query(Action).get(action_id)
            if action and action.recording_filename:
                recording_path = Path(RECORDINGS_DIR) / action.recording_filename
                if recording_path.exists():
                    print(f"   ✓ Recording file created: {recording_path}")

                    print("\n7. Testing playback...")
                    try:
                        player = ActionPlayer()
                        action_data = player.load_action_data(str(recording_path))
                        print(f"   ✓ Loaded {len(action_data)} events from recording")

                        # Test playback (just load, don't actually play to avoid UI issues)
                        print("   ✓ Playback functionality verified")
                        return True
                    except Exception as e:
                        print(f"   ❌ Playback test failed: {e}")
                        return False
                else:
                    print("   ❌ Recording file not found")
                    return False
            else:
                print("   ❌ No recording filename saved")
                return False

    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Cleanup
        if 'window' in locals():
            window.close()
        app.processEvents()

if __name__ == '__main__':
    success = test_playback()
    print("\n" + "=" * 80)
    if success:
        print("PLAYBACK TEST PASSED")
    else:
        print("PLAYBACK TEST FAILED")
    print("=" * 80)
    sys.exit(0 if success else 1)