"""
Gradual test to find what causes the crash in ActionWindow
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
from distr.core.signals import signal_manager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import tempfile
import shutil

# Ensure signal_manager is initialized
if not hasattr(signal_manager, 'action_recording_started'):
    # Re-import to ensure it's initialized
    import importlib
    import distr.core.signals
    importlib.reload(distr.core.signals)
    from distr.core.signals import signal_manager


def test_action_window_basic():
    """Test 1: Just create the window"""
    print("=" * 80)
    print("TEST 1: Create ActionWindow")
    print("=" * 80)
    
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    
    try:
        window = ActionWindow()
        window.show()
        app.processEvents()
        print("✓ Window created and shown")
        window.close()
        app.processEvents()
        return True
    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_action_window_with_db():
    """Test 2: Create window with database"""
    print("\n" + "=" * 80)
    print("TEST 2: ActionWindow with Database")
    print("=" * 80)
    
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    
    temp_dir = tempfile.mkdtemp()
    temp_db = os.path.join(temp_dir, 'test.db')
    
    try:
        engine = create_engine(f'sqlite:///{temp_db}')
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        test_session = Session()
        
        def mock_get_session():
            class MockSession:
                def __enter__(self):
                    return test_session
                def __exit__(self, *args):
                    pass
            return MockSession()
        
        with patch('distr.gui.action.get_session', side_effect=mock_get_session):
            window = ActionWindow()
            window.show()
            app.processEvents()
            print("✓ Window created with database")
            
            window.load_actions()
            app.processEvents()
            print("✓ Actions loaded")
            
            window.close()
            app.processEvents()
            test_session.close()
            return True
    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_action_window_with_action():
    """Test 3: Create window, add action, select it"""
    print("\n" + "=" * 80)
    print("TEST 3: ActionWindow with Action Selected")
    print("=" * 80)
    
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    
    temp_dir = tempfile.mkdtemp()
    temp_db = os.path.join(temp_dir, 'test.db')
    
    try:
        engine = create_engine(f'sqlite:///{temp_db}')
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        test_session = Session()
        
        def mock_get_session():
            class MockSession:
                def __enter__(self):
                    return test_session
                def __exit__(self, *args):
                    pass
            return MockSession()
        
        with patch('distr.gui.action.get_session', side_effect=mock_get_session):
            window = ActionWindow()
            window.show()
            app.processEvents()
            
            # Create action
            new_action = Action(
                title="Test Action",
                description="",
                additional_trigger_words="[]",
                play_sticky=False,
                action="{}",
                recording_filename=None
            )
            test_session.add(new_action)
            test_session.commit()
            action_id = new_action.id
            
            window.load_actions()
            app.processEvents()
            
            window.current_action_id = action_id
            window.select_action_by_id(action_id)
            app.processEvents()
            
            print("✓ Action selected")
            
            window.close()
            app.processEvents()
            test_session.close()
            return True
    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_start_recording_button():
    """Test 4: Click start recording button (without actually starting)"""
    print("\n" + "=" * 80)
    print("TEST 4: Start Recording Button Click")
    print("=" * 80)
    
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    
    temp_dir = tempfile.mkdtemp()
    temp_db = os.path.join(temp_dir, 'test.db')
    test_recordings_dir = os.path.join(temp_dir, 'recordings')
    os.makedirs(test_recordings_dir, exist_ok=True)
    
    try:
        engine = create_engine(f'sqlite:///{temp_db}')
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        test_session = Session()
        
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
                    
                    window = ActionWindow()
                    window.show()
                    app.processEvents()
                    
                    # Create and select action
                    new_action = Action(
                        title="Test Action",
                        description="",
                        additional_trigger_words="[]",
                        play_sticky=False,
                        action="{}",
                        recording_filename=None
                    )
                    test_session.add(new_action)
                    test_session.commit()
                    action_id = new_action.id
                    
                    window.load_actions()
                    app.processEvents()
                    window.current_action_id = action_id
                    window.select_action_by_id(action_id)
                    app.processEvents()
                    
                    print("✓ Action selected, about to call start_recording()...")
                    print("   (This is where it might crash)")
                    
                    # This is the critical call
                    window.start_recording()
                    app.processEvents()
                    
                    print("✓ start_recording() called, processing events...")
                    
                    # Process events for a bit to let the timer fire
                    for _ in range(10):
                        app.processEvents()
                        time.sleep(0.05)
                    
                    print("✓ Events processed")
                    
                    # Clean up if recording started
                    if window.recorder and window.recorder.is_active():
                        print("   Recording is active, stopping...")
                        window.stop_recording()
                        app.processEvents()
                    
                    window.close()
                    app.processEvents()
                    test_session.close()
                    return True
                    
    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == '__main__':
    print("\nRunning gradual tests to find crash point...\n")
    
    results = []
    results.append(("Basic Window", test_action_window_basic()))
    if results[-1][1]:
        results.append(("Window with DB", test_action_window_with_db()))
    if results[-1][1]:
        results.append(("Window with Action", test_action_window_with_action()))
    if results[-1][1]:
        results.append(("Start Recording", test_start_recording_button()))
    
    print("\n" + "=" * 80)
    print("TEST RESULTS")
    print("=" * 80)
    for name, result in results:
        status = "✓ PASS" if result else "❌ FAIL"
        print(f"{status}: {name}")
    
    print("=" * 80)
    
    sys.exit(0 if all(r[1] for r in results) else 1)

