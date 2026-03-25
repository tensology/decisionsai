"""
Test Action Recording Functionality

Tests for action creation, recording start/stop, and crash scenarios.
"""

import unittest
import sys
import os
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from distr.gui.action import ActionWindow
from distr.core.db import get_session, Action, Base
from distr.core.paths import RECORDINGS_DIR
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import tempfile
import shutil


class TestActionRecording(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Set up QApplication for all tests"""
        if not QApplication.instance():
            cls.app = QApplication([])
        else:
            cls.app = QApplication.instance()
    
    def setUp(self):
        """Set up test environment before each test"""
        # Create temporary database
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db = os.path.join(self.temp_dir, 'test.db')
        
        # Create test database
        engine = create_engine(f'sqlite:///{self.temp_db}')
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        self.test_session = Session()
        
        # Ensure recordings directory exists
        self.test_recordings_dir = os.path.join(self.temp_dir, 'recordings')
        os.makedirs(self.test_recordings_dir, exist_ok=True)
        
        # Patch get_session to return our test session
        # Use context manager pattern
        def mock_get_session():
            class MockSession:
                def __enter__(self):
                    return self.test_session
                def __exit__(self, *args):
                    pass
            return MockSession()
        
        self.session_patcher = patch('distr.gui.action.get_session', side_effect=mock_get_session)
        self.session_patcher.start()
        
        # Also patch in action_recorder
        self.session_patcher2 = patch('distr.core.action_recorder.get_session', return_value=self.test_session)
        self.session_patcher2.start()
        
        # Patch RECORDINGS_DIR
        self.recordings_patcher = patch('distr.gui.action.RECORDINGS_DIR', self.test_recordings_dir)
        self.recordings_patcher.start()
        self.recordings_patcher2 = patch('distr.core.action_recorder.RECORDINGS_DIR', self.test_recordings_dir)
        self.recordings_patcher2.start()
        
        # Create action window
        self.window = ActionWindow()
        self.window.show()
        self.app.processEvents()
    
    def tearDown(self):
        """Clean up after each test"""
        # Stop any active recording
        if hasattr(self.window, 'recorder') and self.window.recorder:
            try:
                if self.window.recorder.is_active():
                    self.window.recorder.stop_recording()
            except:
                pass
        
        # Close window
        self.window.close()
        self.app.processEvents()
        
        # Stop patchers
        self.session_patcher.stop()
        self.recordings_patcher.stop()
        self.recordings_patcher2.stop()
        
        # Clean up database
        self.test_session.close()
        
        # Remove temp directory
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_create_action_and_start_recording(self):
        """Test creating an action and starting recording - reproduces crash scenario"""
        print("\n=== Test: Create Action and Start Recording ===")
        
        try:
            # Step 1: Create a new action
            print("Step 1: Creating new action...")
            new_action = Action(
                title="Test Action",
                description="Test description",
                additional_trigger_words="[]",
                play_sticky=False,
                action="{}",
                recording_filename=None
            )
            self.test_session.add(new_action)
            self.test_session.commit()
            action_id = new_action.id
            print(f"✓ Action created with ID: {action_id}")
            
            # Step 2: Load actions into window
            print("Step 2: Loading actions into window...")
            self.window.load_actions()
            self.app.processEvents()
            print(f"✓ Actions loaded. List count: {self.window.action_list.count()}")
            
            # Step 3: Select the action
            print("Step 3: Selecting action...")
            self.window.current_action_id = action_id
            self.window.select_action_by_id(action_id)
            self.app.processEvents()
            print(f"✓ Action selected. Current ID: {self.window.current_action_id}")
            
            # Step 4: Start recording (this is where the crash happens)
            print("Step 4: Starting recording...")
            try:
                self.window.start_recording()
                self.app.processEvents()
                print("✓ Recording started successfully")
                
                # Verify recording state
                self.assertIsNotNone(self.window.recorder, "Recorder should be created")
                self.assertTrue(self.window.recorder.is_active(), "Recording should be active")
                print("✓ Recording state verified")
                
                # Verify UI is locked
                self.assertFalse(self.window.action_list.isEnabled(), "Action list should be disabled during recording")
                self.assertFalse(self.window.start_recording_button.isEnabled(), "Start button should be disabled")
                self.assertTrue(self.window.stop_recording_button.isEnabled(), "Stop button should be enabled")
                print("✓ UI locked correctly")
                
                # Step 5: Stop recording
                print("Step 5: Stopping recording...")
                self.window.stop_recording()
                self.app.processEvents()
                print("✓ Recording stopped successfully")
                
                # Verify recording file was created
                action = self.test_session.query(Action).get(action_id)
                self.assertIsNotNone(action.recording_filename, "Recording filename should be set")
                recording_path = Path(self.test_recordings_dir) / action.recording_filename
                self.assertTrue(recording_path.exists(), "Recording file should exist")
                print(f"✓ Recording file created: {action.recording_filename}")
                
            except Exception as e:
                print(f"❌ CRASH DETECTED in start_recording: {e}")
                import traceback
                traceback.print_exc()
                raise
            
        except Exception as e:
            print(f"❌ TEST FAILED: {e}")
            import traceback
            traceback.print_exc()
            raise
    
    def test_start_recording_without_action_selected(self):
        """Test starting recording when no action is selected"""
        print("\n=== Test: Start Recording Without Action ===")
        
        try:
            # Ensure no action is selected
            self.window.current_action_id = None
            
            # Mock QInputDialog to simulate user entering a name
            with patch('distr.gui.action.QInputDialog.getText') as mock_dialog:
                mock_dialog.return_value = ("New Test Action", True)
                
                self.window.start_recording()
                self.app.processEvents()
                
                # Should create new action and start recording
                self.assertIsNotNone(self.window.current_action_id, "Action should be created")
                self.assertIsNotNone(self.window.recorder, "Recorder should be created")
                print("✓ Action created and recording started")
                
        except Exception as e:
            print(f"❌ TEST FAILED: {e}")
            import traceback
            traceback.print_exc()
            raise
    
    def test_start_recording_with_existing_recording(self):
        """Test starting recording when action already has a recording"""
        print("\n=== Test: Start Recording With Existing Recording ===")
        
        try:
            # Create action with existing recording
            new_action = Action(
                title="Action With Recording",
                description="",
                additional_trigger_words="[]",
                play_sticky=False,
                action="{}",
                recording_filename="existing-recording.json"
            )
            self.test_session.add(new_action)
            self.test_session.commit()
            action_id = new_action.id
            
            # Create the recording file
            recording_path = Path(self.test_recordings_dir) / "existing-recording.json"
            recording_path.write_text('{"01": {"type": "mouse", "details": "move, 100,100"}}')
            
            # Select the action
            self.window.current_action_id = action_id
            self.window.load_action_details(action_id)
            self.app.processEvents()
            
            # Verify start recording is disabled
            self.assertFalse(self.window.start_recording_button.isEnabled(), 
                           "Start recording should be disabled when recording exists")
            print("✓ Start recording button correctly disabled")
            
            # Try to start recording anyway (should prompt to clear)
            with patch('distr.gui.action.QMessageBox.question') as mock_question:
                mock_question.return_value = self.window.StandardButton.No  # User cancels
                
                self.window.start_recording()
                self.app.processEvents()
                
                # Should not start recording
                self.assertIsNone(self.window.recorder, "Recording should not start if user cancels")
                print("✓ Recording correctly prevented when user cancels")
            
        except Exception as e:
            print(f"❌ TEST FAILED: {e}")
            import traceback
            traceback.print_exc()
            raise
    
    def test_navigation_locked_during_recording(self):
        """Test that navigation is locked during recording"""
        print("\n=== Test: Navigation Locked During Recording ===")
        
        try:
            # Create two actions
            action1 = Action(title="Action 1", additional_trigger_words="[]", action="{}")
            action2 = Action(title="Action 2", additional_trigger_words="[]", action="{}")
            self.test_session.add_all([action1, action2])
            self.test_session.commit()
            
            # Load and select first action
            self.window.load_actions()
            self.window.current_action_id = action1.id
            self.window.select_action_by_id(action1.id)
            self.app.processEvents()
            
            # Start recording
            self.window.start_recording()
            self.app.processEvents()
            
            # Try to select second action (should be prevented)
            item2 = None
            for i in range(self.window.action_list.count()):
                item = self.window.action_list.item(i)
                if item and item.data(Qt.ItemDataRole.UserRole) == action2.id:
                    item2 = item
                    break
            
            if item2:
                # Try to select it
                self.window.on_action_selected(item2)
                self.app.processEvents()
                
                # Should still have action1 selected
                self.assertEqual(self.window.current_action_id, action1.id,
                               "Should not be able to change action during recording")
                print("✓ Navigation correctly prevented during recording")
            
            # Stop recording
            self.window.stop_recording()
            self.app.processEvents()
            
            # Now should be able to select action2
            if item2:
                self.window.on_action_selected(item2)
                self.app.processEvents()
                self.assertEqual(self.window.current_action_id, action2.id,
                               "Should be able to change action after recording stops")
                print("✓ Navigation unlocked after recording")
            
        except Exception as e:
            print(f"❌ TEST FAILED: {e}")
            import traceback
            traceback.print_exc()
            raise
    
    def test_hotkey_toggle_recording(self):
        """Test global hotkey for toggling recording"""
        print("\n=== Test: Hotkey Toggle Recording ===")
        
        try:
            # Create action
            new_action = Action(title="Hotkey Test", additional_trigger_words="[]", action="{}")
            self.test_session.add(new_action)
            self.test_session.commit()
            
            # Select action
            self.window.current_action_id = new_action.id
            self.window.select_action_by_id(new_action.id)
            self.app.processEvents()
            
            # Trigger hotkey to start recording
            if hasattr(self.window, 'recording_hotkey'):
                self.window._toggle_recording_hotkey()
                self.app.processEvents()
                
                # Should start recording
                self.assertIsNotNone(self.window.recorder, "Recording should start via hotkey")
                print("✓ Recording started via hotkey")
                
                # Trigger hotkey again to stop
                self.window._toggle_recording_hotkey()
                self.app.processEvents()
                
                # Should stop recording
                self.assertFalse(self.window.recorder.is_active() if self.window.recorder else True,
                               "Recording should stop via hotkey")
                print("✓ Recording stopped via hotkey")
            else:
                print("⚠ Hotkey not set up (skipping hotkey test)")
            
        except Exception as e:
            print(f"❌ TEST FAILED: {e}")
            import traceback
            traceback.print_exc()
            raise


if __name__ == '__main__':
    unittest.main(verbosity=2)

