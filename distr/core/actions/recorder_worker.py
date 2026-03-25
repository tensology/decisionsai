#!/usr/bin/env python3
"""
Standalone recorder worker — launched as a subprocess to avoid both:
  - fork() crash on macOS (CoreFoundation is not fork-safe with Qt threads)
  - spawn() SemLock pickle crash on Python 3.12+

Communicates with the parent via stdin (JSON commands) and stdout (JSON responses).
"""
import json
import sys
import re
import time
import logging
import select
from datetime import datetime
from pathlib import Path

# pynput imports — these need their own process on macOS for CFRunLoop access
from pynput import keyboard as pynput_keyboard
from pynput import mouse as pynput_mouse


def _slugify(text):
    """Simple slugify without importing the full recorder module."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    return text.strip('-')[:80] or 'recording'


def _send(msg):
    """Send a JSON message to parent on stdout."""
    sys.stdout.write(json.dumps(msg) + '\n')
    sys.stdout.flush()


def _recv_nowait():
    """Non-blocking read of a JSON command from stdin. Returns None if nothing available."""
    if select.select([sys.stdin], [], [], 0)[0]:
        line = sys.stdin.readline()
        if line:
            try:
                return json.loads(line.strip())
            except (json.JSONDecodeError, ValueError):
                pass
    return None


def main():
    """Main entry point — reads config from initial JSON on stdin, then records."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    log = logging.getLogger("recorder_worker")

    # Read initial config from first line of stdin
    try:
        init_line = sys.stdin.readline()
        if not init_line:
            _send({'success': False, 'error': 'No init message received'})
            return
        config = json.loads(init_line.strip())
    except Exception as e:
        _send({'success': False, 'error': f'Bad init message: {e}'})
        return

    action_id = config.get('action_id')
    action_title = config.get('action_title', '')
    recordings_dir = config.get('recordings_dir', '.')

    # Build filename
    if action_title and action_id:
        recording_filename = f"{_slugify(action_title)}-{action_id}.json"
    elif action_title:
        recording_filename = f"{_slugify(action_title)}.json"
    else:
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        recording_filename = f"action-{action_id}-{ts}.json" if action_id else f"action-{ts}.json"

    Path(recordings_dir).mkdir(parents=True, exist_ok=True)

    # Recording state
    events = {}
    event_counter = 0
    start_dt = datetime.now()
    last_event_time = start_dt
    is_recording = True
    paused = False
    ctrl_held = False  # Track ctrl state to filter Ctrl+Space from recording

    def add_event(event_type, details):
        nonlocal event_counter, last_event_time
        event_counter += 1
        now = datetime.now()
        td = (now - last_event_time).total_seconds() if last_event_time else 0
        events[f"{event_counter:02d}"] = {
            "type": event_type,
            "details": details,
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "time_since_last_event": f"{td:.3f}",
        }
        last_event_time = now

    def get_key_name(key):
        try:
            return key.char
        except AttributeError:
            ks = str(key).replace("Key.", "")
            if ks in ("cmd", "cmd_l", "cmd_r"):
                return "command"
            return ks.lower()

    def _is_ctrl_key(key):
        try:
            ks = str(key).replace("Key.", "")
            return ks in ("ctrl", "ctrl_l", "ctrl_r")
        except Exception:
            return False

    # Callbacks
    def on_kb_press(key):
        nonlocal ctrl_held
        if _is_ctrl_key(key):
            ctrl_held = True
        if not is_recording or paused:
            return
        # Filter out Ctrl+Space (pause/resume hotkey) — don't record it
        try:
            if key == pynput_keyboard.Key.space and ctrl_held:
                return
        except Exception:
            pass
        add_event("keyboard", f"Press {get_key_name(key)}")

    def on_kb_release(key):
        nonlocal ctrl_held
        if _is_ctrl_key(key):
            ctrl_held = False
        if not is_recording or paused:
            return
        # Filter out Ctrl+Space release events too
        try:
            if key == pynput_keyboard.Key.space and ctrl_held:
                return
        except Exception:
            pass
        add_event("keyboard", f"Release {get_key_name(key)}")

    def on_mouse_click(x, y, button, pressed):
        if not is_recording or paused:
            return
        bn = str(button).split(".")[-1].lower()
        action = "click" if pressed else "release"
        add_event("mouse", f"{action}, {x},{y}, {bn}")

    def on_mouse_move(x, y):
        if not is_recording or paused:
            return
        add_event("mouse", f"move, {x},{y}")

    # Start listeners
    try:
        try:
            ix, iy = pynput_mouse.Controller().position
            add_event("mouse", f"initial_position, {ix},{iy}")
        except Exception as e:
            _send({'success': False, 'error': f'Cannot access mouse input: {e}'})
            return

        kb_listener = pynput_keyboard.Listener(on_press=on_kb_press, on_release=on_kb_release)
        ms_listener = pynput_mouse.Listener(on_move=on_mouse_move, on_click=on_mouse_click)
        kb_listener.start()
        ms_listener.start()

        # Tell parent we're ready
        _send({'success': True, 'filename': recording_filename})

        # Command loop
        while is_recording:
            msg = _recv_nowait()
            if msg:
                cmd = msg.get('command')
                if cmd == 'stop':
                    is_recording = False
                    break
                elif cmd == 'pause':
                    paused = not paused
                    _send({'command': 'pause_state', 'paused': paused})
            time.sleep(0.1)

        # Tear down listeners
        try:
            kb_listener.stop()
            ms_listener.stop()
            time.sleep(0.2)
        except Exception:
            pass

        # Save
        filepath = Path(recordings_dir) / recording_filename
        try:
            with open(filepath, 'w') as f:
                json.dump(events, f, indent=2)
            _send({'command': 'saved', 'filename': recording_filename, 'filepath': str(filepath)})
        except Exception as e:
            _send({'command': 'save_error', 'error': str(e)})

    except Exception as e:
        log.error(f"Recording error: {e}", exc_info=True)
        _send({'success': False, 'error': str(e)})


if __name__ == '__main__':
    main()
