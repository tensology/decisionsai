#!/usr/bin/env python3
"""
Standalone playback worker — launched as a subprocess to avoid fork/spawn crashes.
Communicates with the parent via stdin (JSON commands) and stdout (JSON responses).
"""
import json
import sys
import time
import logging
import select
from datetime import datetime
from pynput import mouse, keyboard
from pynput.keyboard import Key, KeyCode

_KEY_MAP = {
    'shift': Key.shift, 'shift_l': Key.shift_l, 'shift_r': Key.shift_r,
    'ctrl': Key.ctrl, 'ctrl_l': Key.ctrl_l, 'ctrl_r': Key.ctrl_r,
    'alt': Key.alt, 'alt_l': Key.alt_l, 'alt_r': Key.alt_r,
    'cmd': Key.cmd, 'command': Key.cmd,
    'enter': Key.enter, 'space': Key.space, 'backspace': Key.backspace,
    'tab': Key.tab, 'esc': Key.esc, 'escape': Key.esc,
    'up': Key.up, 'down': Key.down, 'left': Key.left, 'right': Key.right,
    'delete': Key.delete, 'home': Key.home, 'end': Key.end,
    'page_up': Key.page_up, 'page_down': Key.page_down,
    'caps_lock': Key.caps_lock, 'option': Key.alt,
    'f1': Key.f1, 'f2': Key.f2, 'f3': Key.f3, 'f4': Key.f4,
    'f5': Key.f5, 'f6': Key.f6, 'f7': Key.f7, 'f8': Key.f8,
    'f9': Key.f9, 'f10': Key.f10, 'f11': Key.f11, 'f12': Key.f12,
}


def _send(msg):
    sys.stdout.write(json.dumps(msg) + '\n')
    sys.stdout.flush()


def _recv_nowait():
    if select.select([sys.stdin], [], [], 0)[0]:
        line = sys.stdin.readline()
        if line:
            try:
                return json.loads(line.strip())
            except (json.JSONDecodeError, ValueError):
                pass
    return None


def _recv_blocking(timeout=0.2):
    """Blocking read with timeout."""
    if select.select([sys.stdin], [], [], timeout)[0]:
        line = sys.stdin.readline()
        if line:
            try:
                return json.loads(line.strip())
            except (json.JSONDecodeError, ValueError):
                pass
    return None


def get_key(key_string):
    if len(key_string) == 1:
        return key_string
    v = _KEY_MAP.get(key_string.lower())
    if v:
        return v
    try:
        return KeyCode.from_char(key_string)
    except (ValueError, TypeError):
        return None


def _handle_mouse(mc, details, play_sticky):
    try:
        parts = details.split(', ')
        if len(parts) < 2:
            return
        action = parts[0].strip()
        if play_sticky and action in ('move', 'initial_position'):
            return
        coords = parts[1].split(',')
        if len(coords) < 2:
            return
        x, y = int(float(coords[0])), int(float(coords[1]))
        if action in ('move', 'initial_position'):
            mc.position = (x, y)
        elif action == 'click':
            mc.position = (x, y)
            if len(parts) >= 3:
                btn = getattr(mouse.Button, parts[2].strip(), None)
                if btn:
                    mc.press(btn)
        elif action == 'release':
            mc.position = (x, y)
            if len(parts) >= 3:
                btn = getattr(mouse.Button, parts[2].strip(), None)
                if btn:
                    mc.release(btn)
        elif action in ('double_click', 'double click'):
            mc.position = (x, y)
            if len(parts) >= 3:
                btn = getattr(mouse.Button, parts[2].strip(), None)
                if btn:
                    mc.click(btn, 2)
        elif action == 'drag':
            mc.position = (x, y)
    except Exception as e:
        logging.getLogger("player_worker").error(f"Mouse event error: {e}")


def _handle_keyboard(kc, details, pressed_keys):
    try:
        if details.startswith('Press '):
            k = get_key(details[6:].strip())
            if k:
                kc.press(k)
                pressed_keys.add(k)
        elif details.startswith('Release '):
            k = get_key(details[8:].strip())
            if k:
                kc.release(k)
                pressed_keys.discard(k)
        else:
            for ch in details:
                if ch.isupper() or ch in '!@#$%^&*()_+{}|:"<>?':
                    with kc.pressed(Key.shift):
                        kc.press(ch.lower())
                        kc.release(ch.lower())
                else:
                    kc.press(ch)
                    kc.release(ch)
                time.sleep(0.01)
    except Exception as e:
        logging.getLogger("player_worker").error(f"Keyboard event error: {e}")


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    log = logging.getLogger("player_worker")

    # Read init config
    try:
        init_line = sys.stdin.readline()
        if not init_line:
            _send({'status': 'error', 'error': 'No init message'})
            return
        config = json.loads(init_line.strip())
    except Exception as e:
        _send({'status': 'error', 'error': f'Bad init: {e}'})
        return

    file_path = config.get('file_path')
    play_sticky = config.get('play_sticky', False)

    # Create controllers in this clean process
    try:
        mc = mouse.Controller()
        kc = keyboard.Controller()
    except Exception as e:
        _send({'status': 'error', 'error': f'Controller init failed: {e}'})
        return

    # Load action data
    try:
        with open(file_path, 'r') as f:
            action_data = json.load(f)
    except Exception as e:
        _send({'status': 'error', 'error': f'Cannot load file: {e}'})
        return

    _send({'status': 'started'})

    pressed_keys = set()
    last_event_time = datetime.now()

    try:
        sorted_events = sorted(action_data.items(), key=lambda x: int(x[0]))

        for _key, event in sorted_events:
            # Check commands
            msg = _recv_nowait()
            while msg:
                cmd = msg.get('command')
                if cmd == 'stop':
                    for k in list(pressed_keys):
                        try:
                            kc.release(k)
                        except Exception:
                            pass
                    pressed_keys.clear()
                    _send({'status': 'stopped'})
                    return
                elif cmd == 'pause':
                    _send({'command': 'pause_state', 'paused': True})
                    # Block until resume or stop
                    while True:
                        rmsg = _recv_blocking(0.2)
                        if rmsg:
                            rc = rmsg.get('command')
                            if rc == 'resume':
                                _send({'command': 'pause_state', 'paused': False})
                                break
                            elif rc == 'stop':
                                for k in list(pressed_keys):
                                    try:
                                        kc.release(k)
                                    except Exception:
                                        pass
                                pressed_keys.clear()
                                _send({'status': 'stopped'})
                                return
                msg = _recv_nowait()

            # Timing
            if not play_sticky:
                td = float(event.get('time_since_last_event', '0'))
                elapsed = (datetime.now() - last_event_time).total_seconds()
                sleep_t = max(0, td - elapsed)
                if sleep_t > 0:
                    time.sleep(sleep_t)

            etype = event.get('type')
            details = event.get('details', '')
            if etype == 'mouse':
                _handle_mouse(mc, details, play_sticky)
            elif etype == 'keyboard':
                _handle_keyboard(kc, details, pressed_keys)

            last_event_time = datetime.now()

        # Release held keys
        for k in pressed_keys:
            try:
                kc.release(k)
            except Exception:
                pass

        _send({'status': 'completed'})

    except Exception as e:
        log.error(f"Playback error: {e}", exc_info=True)
        _send({'status': 'error', 'error': str(e)})


if __name__ == '__main__':
    main()
