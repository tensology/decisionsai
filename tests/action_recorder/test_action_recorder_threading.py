"""
Test Action Recorder Threading Issues

This test specifically checks for threading-related crashes when starting pynput listeners.
"""

import sys
import os
import threading
import time
from unittest.mock import Mock, patch

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

def test_pynput_in_main_thread():
    """Test pynput listeners in main thread"""
    print("\n=== Test 1: Pynput in Main Thread ===")
    try:
        from pynput import mouse, keyboard
        
        mouse_listener = None
        keyboard_listener = None
        
        def on_move(x, y):
            pass
        
        def on_click(x, y, button, pressed):
            pass
        
        def on_press(key):
            pass
        
        def on_release(key):
            pass
        
        print("Creating listeners...")
        mouse_listener = mouse.Listener(on_move=on_move, on_click=on_click)
        keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        
        print("Starting listeners...")
        mouse_listener.start()
        keyboard_listener.start()
        
        print("Listeners started, waiting 1 second...")
        time.sleep(1)
        
        print("Stopping listeners...")
        mouse_listener.stop()
        keyboard_listener.stop()
        
        print("✓ Test 1 PASSED")
        return True
    except Exception as e:
        print(f"❌ Test 1 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if mouse_listener:
            try:
                mouse_listener.stop()
            except:
                pass
        if keyboard_listener:
            try:
                keyboard_listener.stop()
            except:
                pass


def test_pynput_in_background_thread():
    """Test pynput listeners in background thread"""
    print("\n=== Test 2: Pynput in Background Thread ===")
    result = [None]
    error = [None]
    
    def run_in_thread():
        try:
            from pynput import mouse, keyboard
            
            mouse_listener = None
            keyboard_listener = None
            
            def on_move(x, y):
                pass
            
            def on_click(x, y, button, pressed):
                pass
            
            def on_press(key):
                pass
            
            def on_release(key):
                pass
            
            print("  [Thread] Creating listeners...")
            mouse_listener = mouse.Listener(on_move=on_move, on_click=on_click)
            keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            
            print("  [Thread] Starting listeners...")
            mouse_listener.start()
            keyboard_listener.start()
            
            print("  [Thread] Listeners started, waiting 1 second...")
            time.sleep(1)
            
            print("  [Thread] Stopping listeners...")
            mouse_listener.stop()
            keyboard_listener.stop()
            
            result[0] = True
        except Exception as e:
            error[0] = e
            import traceback
            traceback.print_exc()
        finally:
            if 'mouse_listener' in locals() and mouse_listener:
                try:
                    mouse_listener.stop()
                except:
                    pass
            if 'keyboard_listener' in locals() and keyboard_listener:
                try:
                    keyboard_listener.stop()
                except:
                    pass
    
    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    thread.join(timeout=5)
    
    if error[0]:
        print(f"❌ Test 2 FAILED: {error[0]}")
        return False
    elif result[0]:
        print("✓ Test 2 PASSED")
        return True
    else:
        print("❌ Test 2 FAILED: Thread timed out or didn't complete")
        return False


def test_action_recorder_direct():
    """Test ActionRecorder directly"""
    print("\n=== Test 3: ActionRecorder Direct ===")
    try:
        from distr.core.action_recorder import ActionRecorder
        
        recorder = ActionRecorder(action_id=1, action_title="Test Action")
        print("Recorder created")
        
        print("Starting recording...")
        recorder.start_recording()
        print("Recording started")
        
        time.sleep(0.5)
        
        print("Stopping recording...")
        recorder.stop_recording()
        print("Recording stopped")
        
        print("✓ Test 3 PASSED")
        return True
    except Exception as e:
        print(f"❌ Test 3 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_action_recorder_in_thread():
    """Test ActionRecorder in background thread"""
    print("\n=== Test 4: ActionRecorder in Background Thread ===")
    result = [None]
    error = [None]
    
    def run_in_thread():
        try:
            from distr.core.action_recorder import ActionRecorder
            
            recorder = ActionRecorder(action_id=1, action_title="Test Action")
            print("  [Thread] Recorder created")
            
            print("  [Thread] Starting recording...")
            recorder.start_recording()
            print("  [Thread] Recording started")
            
            time.sleep(0.5)
            
            print("  [Thread] Stopping recording...")
            recorder.stop_recording()
            print("  [Thread] Recording stopped")
            
            result[0] = True
        except Exception as e:
            error[0] = e
            import traceback
            traceback.print_exc()
    
    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    thread.join(timeout=5)
    
    if error[0]:
        print(f"❌ Test 4 FAILED: {error[0]}")
        return False
    elif result[0]:
        print("✓ Test 4 PASSED")
        return True
    else:
        print("❌ Test 4 FAILED: Thread timed out or didn't complete")
        return False


def test_pynput_with_pyqt6():
    """Test pynput with PyQt6 event loop running"""
    print("\n=== Test 5: Pynput with PyQt6 Event Loop ===")
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer
        from pynput import mouse, keyboard
        import sys
        
        app = QApplication.instance()
        if not app:
            app = QApplication([])
        
        mouse_listener = None
        keyboard_listener = None
        test_result = [False]
        
        def on_move(x, y):
            pass
        
        def on_click(x, y, button, pressed):
            pass
        
        def on_press(key):
            pass
        
        def on_release(key):
            pass
        
        def start_listeners():
            try:
                print("  [QTimer] Creating listeners...")
                nonlocal mouse_listener, keyboard_listener
                mouse_listener = mouse.Listener(on_move=on_move, on_click=on_click)
                keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
                
                print("  [QTimer] Starting listeners...")
                mouse_listener.start()
                keyboard_listener.start()
                
                print("  [QTimer] Listeners started")
                test_result[0] = True
                
                # Stop after 1 second
                QTimer.singleShot(1000, stop_listeners)
            except Exception as e:
                print(f"  [QTimer] Error: {e}")
                import traceback
                traceback.print_exc()
        
        def stop_listeners():
            try:
                print("  [QTimer] Stopping listeners...")
                if mouse_listener:
                    mouse_listener.stop()
                if keyboard_listener:
                    keyboard_listener.stop()
                print("  [QTimer] Listeners stopped")
                app.quit()
            except Exception as e:
                print(f"  [QTimer] Error stopping: {e}")
                app.quit()
        
        # Delay startup
        QTimer.singleShot(100, start_listeners)
        
        print("Running PyQt6 event loop...")
        app.exec()
        
        if test_result[0]:
            print("✓ Test 5 PASSED")
            return True
        else:
            print("❌ Test 5 FAILED: Listeners didn't start")
            return False
    except Exception as e:
        print(f"❌ Test 5 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    print("=" * 80)
    print("ACTION RECORDER THREADING TESTS")
    print("=" * 80)
    
    results = []
    
    # Run tests
    results.append(("Main Thread", test_pynput_in_main_thread()))
    results.append(("Background Thread", test_pynput_in_background_thread()))
    results.append(("ActionRecorder Direct", test_action_recorder_direct()))
    results.append(("ActionRecorder in Thread", test_action_recorder_in_thread()))
    results.append(("Pynput with PyQt6", test_pynput_with_pyqt6()))
    
    print("\n" + "=" * 80)
    print("TEST RESULTS")
    print("=" * 80)
    for name, result in results:
        status = "✓ PASS" if result else "❌ FAIL"
        print(f"{status}: {name}")
    
    print("=" * 80)
    
    # Exit with error if any test failed
    if not all(r[1] for r in results):
        sys.exit(1)




