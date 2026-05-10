"""Global keyboard combo listener for push-to-talk."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional, Set, Tuple


logger = logging.getLogger(__name__)


class GlobalPttHotkeyListener:
    @staticmethod
    def _modifier_tokens(modifier: str) -> Set[str]:
        if not modifier:
            return set()
        if "_" in modifier:
            return {token for token in modifier.split("_") if token}
        return {modifier}

    """Listen for a two-modifier combo and emit press/release callbacks."""

    def __init__(
        self,
        on_combo_pressed: Callable[[], None],
        on_combo_released: Callable[[], None],
        get_enabled: Callable[[], bool],
        get_combo: Callable[[], Set[str]],
        get_size_down_combo: Optional[Callable[[], Tuple[str, str]]] = None,
        get_size_up_combo: Optional[Callable[[], Tuple[str, str]]] = None,
        get_record_toggle_combo: Optional[Callable[[], Tuple[str, str]]] = None,
        get_action_combos: Optional[Callable[[], dict[str, Tuple[str, str]]]] = None,
        on_size_down: Optional[Callable[[], None]] = None,
        on_size_up: Optional[Callable[[], None]] = None,
        on_record_toggle: Optional[Callable[[], None]] = None,
        on_hotkey_action: Optional[Callable[[str], None]] = None,
        get_dictation_combo: Optional[Callable[[], Tuple[str, str]]] = None,
        on_dictation_pressed: Optional[Callable[[], None]] = None,
        on_dictation_released: Optional[Callable[[], None]] = None,
    ) -> None:
        self._on_combo_pressed = on_combo_pressed
        self._on_combo_released = on_combo_released
        self._get_enabled = get_enabled
        self._get_combo = get_combo
        self._get_size_down_combo = get_size_down_combo
        self._get_size_up_combo = get_size_up_combo
        self._get_record_toggle_combo = get_record_toggle_combo
        self._get_action_combos = get_action_combos
        self._on_size_down = on_size_down
        self._on_size_up = on_size_up
        self._on_record_toggle = on_record_toggle
        self._on_hotkey_action = on_hotkey_action
        self._get_dictation_combo = get_dictation_combo
        self._on_dictation_pressed = on_dictation_pressed
        self._on_dictation_released = on_dictation_released
        self._pressed_modifiers: Set[str] = set()
        self._pressed_keys: Set[str] = set()
        self._combo_active = False
        self._dictation_active = False
        self._active_modifier_only_shortcuts: Set[Tuple[str, str]] = set()
        self._listener = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the global key listener."""
        self.stop()
        try:
            from pynput import keyboard

            self._listener = keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release,
            )
            self._listener.start()
            logger.info("[PTT HOTKEY] Global listener started")
        except Exception as exc:
            logger.warning("[PTT HOTKEY] Failed to start global listener: %s", exc, exc_info=True)
            self._listener = None

    def stop(self) -> None:
        """Stop the global key listener."""
        with self._lock:
            self._pressed_modifiers.clear()
            self._pressed_keys.clear()
            was_active = self._combo_active
            was_dictating = self._dictation_active
            self._combo_active = False
            self._dictation_active = False
            self._active_modifier_only_shortcuts.clear()

        if was_active:
            try:
                self._on_combo_released()
            except Exception:
                logger.debug("[PTT HOTKEY] Combo release callback failed during stop", exc_info=True)
        if was_dictating and self._on_dictation_released:
            try:
                self._on_dictation_released()
            except Exception:
                logger.debug("[PTT HOTKEY] Dictation release callback failed during stop", exc_info=True)

        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                logger.debug("[PTT HOTKEY] Listener stop failed", exc_info=True)
            finally:
                self._listener = None

    def refresh(self) -> None:
        """Refresh settings-backed hotkey state without restarting pynput.

        The listener callbacks read settings dynamically, so a save does not
        need a stop/start cycle. Restarting pynput on macOS can crash inside
        the system input-source APIs when it happens from an app callback.
        """
        self.reset_state()

    def reset_state(self) -> None:
        """Clear pressed-key bookkeeping while keeping the OS listener alive."""
        with self._lock:
            self._pressed_modifiers.clear()
            self._pressed_keys.clear()
            was_active = self._combo_active
            was_dictating = self._dictation_active
            self._combo_active = False
            self._dictation_active = False
            self._active_modifier_only_shortcuts.clear()

        if was_active:
            try:
                self._on_combo_released()
            except Exception:
                logger.debug("[PTT HOTKEY] Combo release callback failed during reset", exc_info=True)
        if was_dictating and self._on_dictation_released:
            try:
                self._on_dictation_released()
            except Exception:
                logger.debug("[PTT HOTKEY] Dictation release callback failed during reset", exc_info=True)

    def _on_press(self, key) -> None:
        normalized_key = self._normalize_key(key)
        if not normalized_key:
            return

        mod = self._normalize_modifier(key)
        with self._lock:
            # Ignore repeat press events for NON-modifier keys only.
            # Modifiers must always be processed so stale state can recover.
            if normalized_key in self._pressed_keys and not mod:
                return
            self._pressed_keys.add(normalized_key)
        emit_combo_pressed = False
        emit_combo_released = False
        emit_size_down = False
        emit_size_up = False
        emit_record_toggle = False
        emit_action_names: list[str] = []
        emit_dictation_pressed = False

        with self._lock:
            down_combo = self._get_size_down_combo() if self._get_size_down_combo else None
            up_combo = self._get_size_up_combo() if self._get_size_up_combo else None
            record_combo = self._get_record_toggle_combo() if self._get_record_toggle_combo else None
            if down_combo and len(down_combo) == 2 and self._on_size_down:
                down_modifier, down_key = down_combo
                down_modifiers = self._modifier_tokens(down_modifier)
                if down_modifiers.issubset(self._pressed_modifiers) and normalized_key == down_key:
                    emit_size_down = True
            if up_combo and len(up_combo) == 2 and self._on_size_up:
                up_modifier, up_key = up_combo
                up_modifiers = self._modifier_tokens(up_modifier)
                if up_modifiers.issubset(self._pressed_modifiers) and normalized_key == up_key:
                    emit_size_up = True
            if record_combo and len(record_combo) == 2 and self._on_record_toggle:
                record_modifier, record_key = record_combo
                record_modifiers = self._modifier_tokens(record_modifier)
                if record_modifiers.issubset(self._pressed_modifiers) and normalized_key == record_key:
                    emit_record_toggle = True
            if self._get_action_combos and self._on_hotkey_action:
                for action_name, combo in (self._get_action_combos() or {}).items():
                    if not combo or len(combo) != 2:
                        continue
                    action_modifier, action_key = combo
                    action_modifiers = self._modifier_tokens(action_modifier)
                    if action_modifiers.issubset(self._pressed_modifiers) and normalized_key == action_key:
                        emit_action_names.append(action_name)
            if self._get_dictation_combo and self._on_dictation_pressed:
                dict_combo = self._get_dictation_combo()
                if dict_combo and len(dict_combo) == 2:
                    dict_modifier, dict_key = dict_combo
                    dict_modifiers = self._modifier_tokens(dict_modifier)
                    if dict_key and dict_modifiers.issubset(self._pressed_modifiers) and normalized_key == dict_key:
                        if not self._dictation_active:
                            self._dictation_active = True
                            emit_dictation_pressed = True

            if not mod:
                # Non-modifier key press handled only for option+bracket shortcuts.
                pass
            else:
                self._pressed_modifiers.add(mod)
                enabled = bool(self._get_enabled())
                combo = self._get_combo()
                matched = enabled and combo.issubset(self._pressed_modifiers)
                if self._get_dictation_combo and self._on_dictation_pressed:
                    dict_combo = self._get_dictation_combo()
                    if dict_combo and len(dict_combo) == 2:
                        dict_modifier, dict_key = dict_combo
                        dict_modifiers = self._modifier_tokens(dict_modifier)
                        if not dict_key and dict_modifiers.issubset(self._pressed_modifiers):
                            if not self._dictation_active:
                                self._dictation_active = True
                                emit_dictation_pressed = True
                for action_name, combo_tuple, callback_enabled in (
                    ("__size_down__", down_combo, bool(self._on_size_down)),
                    ("__size_up__", up_combo, bool(self._on_size_up)),
                    ("__record_toggle__", record_combo, bool(self._on_record_toggle)),
                ):
                    if not callback_enabled or not combo_tuple or len(combo_tuple) != 2:
                        continue
                    action_modifier, action_key = combo_tuple
                    if action_key:
                        continue
                    action_modifiers = self._modifier_tokens(action_modifier)
                    signature = (action_name, action_modifier)
                    if action_modifiers.issubset(self._pressed_modifiers) and signature not in self._active_modifier_only_shortcuts:
                        self._active_modifier_only_shortcuts.add(signature)
                        if action_name == "__size_down__":
                            emit_size_down = True
                        elif action_name == "__size_up__":
                            emit_size_up = True
                        elif action_name == "__record_toggle__":
                            emit_record_toggle = True
                if self._get_action_combos and self._on_hotkey_action:
                    for action_name, combo_tuple in (self._get_action_combos() or {}).items():
                        if not combo_tuple or len(combo_tuple) != 2:
                            continue
                        action_modifier, action_key = combo_tuple
                        if action_key:
                            continue
                        action_modifiers = self._modifier_tokens(action_modifier)
                        signature = (action_name, action_modifier)
                        if action_modifiers.issubset(self._pressed_modifiers) and signature not in self._active_modifier_only_shortcuts:
                            self._active_modifier_only_shortcuts.add(signature)
                            emit_action_names.append(action_name)
                if matched and not self._combo_active:
                    self._combo_active = True
                    emit_combo_pressed = True
                    logger.debug(
                        "[PTT HOTKEY] combo matched on press key=%s modifiers=%s combo=%s",
                        normalized_key,
                        sorted(self._pressed_modifiers),
                        sorted(combo),
                    )
                elif self._combo_active and not matched:
                    # Recover from missed key-up events by releasing when the
                    # currently observed modifier set no longer satisfies combo.
                    self._combo_active = False
                    emit_combo_released = True
                    logger.debug(
                        "[PTT HOTKEY] combo released on press recovery key=%s modifiers=%s combo=%s",
                        normalized_key,
                        sorted(self._pressed_modifiers),
                        sorted(combo),
                    )

        if emit_size_down:
            self._on_size_down()
        if emit_size_up:
            self._on_size_up()
        if emit_record_toggle:
            self._on_record_toggle()
        for action_name in emit_action_names:
            self._on_hotkey_action(action_name)
        if emit_combo_pressed:
            self._on_combo_pressed()
        if emit_combo_released:
            self._on_combo_released()
        if emit_dictation_pressed and self._on_dictation_pressed:
            self._on_dictation_pressed()

    def _on_release(self, key) -> None:
        normalized_key = self._normalize_key(key)
        if normalized_key:
            with self._lock:
                self._pressed_keys.discard(normalized_key)

        mod = self._normalize_modifier(key)
        if not mod and normalized_key in {"option", "command", "control", "shift"}:
            mod = normalized_key

        # Dictation release — must run when the letter key lifts too (mod may be None).
        # Previously we returned early on non-modifier releases, so release never fired if
        # the user let go of D before the modifiers.
        dictation_released = False
        with self._lock:
            if self._dictation_active and self._get_dictation_combo and self._on_dictation_released:
                dict_combo = self._get_dictation_combo()
                if dict_combo and len(dict_combo) == 2:
                    dict_modifier, dict_key = dict_combo
                    dict_modifiers = self._modifier_tokens(dict_modifier)
                    if not dict_key and mod:
                        remaining_modifiers = set(self._pressed_modifiers)
                        remaining_modifiers.discard(mod)
                        still_dict_active = dict_modifiers.issubset(remaining_modifiers)
                    else:
                        still_dict_active = (
                            dict_modifiers.issubset(self._pressed_modifiers)
                            and dict_key in self._pressed_keys
                        )
                    if not still_dict_active:
                        self._dictation_active = False
                        dictation_released = True

        if dictation_released:
            self._on_dictation_released()

        if not mod:
            return

        with self._lock:
            self._pressed_modifiers.discard(mod)
            for signature in list(self._active_modifier_only_shortcuts):
                _name, action_modifier = signature
                action_modifiers = self._modifier_tokens(action_modifier)
                if not action_modifiers.issubset(self._pressed_modifiers):
                    self._active_modifier_only_shortcuts.discard(signature)
            combo = self._get_combo()
            still_matched = combo.issubset(self._pressed_modifiers)
            if self._combo_active and not still_matched:
                self._combo_active = False
                should_emit = True
                logger.debug(
                    "[PTT HOTKEY] combo released on key=%s modifiers=%s combo=%s",
                    normalized_key or mod,
                    sorted(self._pressed_modifiers),
                    sorted(combo),
                )
            else:
                should_emit = False

        if should_emit:
            self._on_combo_released()

    @staticmethod
    def _normalize_key(key) -> Optional[str]:
        key_name = str(getattr(key, "name", "") or "").lower()
        key_char = str(getattr(key, "char", "") or "")
        key_char = key_char.lower()

        # Ctrl+letter often arrives from pynput as ASCII control characters
        # (Ctrl+D => "\x04") rather than the printable letter. Hotkey chords
        # are configured as letters, so map those back before matching.
        if len(key_char) == 1:
            code = ord(key_char)
            if 1 <= code <= 26:
                return chr(ord("a") + code - 1)

        # macOS Option/Alt modified glyphs (with or without Command pressed)
        # should still map back to the intended base shortcut key.
        option_glyph_map = {
            "å": "a",
            "ç": "c",
            "∆": "j",
            "∂": "j",
            "ñ": "n",
            "ß": "s",
            "∑": "w",
            "¡": "1",
            "™": "2",
            "£": "3",
            "¢": "4",
            "∞": "5",
            "§": "6",
            "¶": "7",
            "•": "8",
            "ª": "9",
        }
        if key_char in option_glyph_map:
            return option_glyph_map[key_char]

        if key_char in {"[", "{"}:
            return "left_bracket"
        if key_char in {"]", "}"}:
            return "right_bracket"
        if key_char in {"-", "_"}:
            return "minus"
        if key_char == "+":
            return "plus"
        if key_char == "=":
            return "equal"
        if key_char in {"`", "~"}:
            return "grave"
        if key_char in {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9"}:
            return key_char
        if key_char in {"a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m",
                        "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z"}:
            return key_char
        if key_char == ",":
            return "comma"
        if key_char == ".":
            return "period"
        if key_char == "/":
            return "slash"
        if key_char == ";":
            return "semicolon"
        if key_char == "'":
            return "quote"
        if key_char == "\\":
            return "backslash"

        # Some environments encode key codes in repr.
        key_str = str(key).lower()
        vk = getattr(key, "vk", None)
        if vk in {33, 219} or ("vk=33" in key_str) or ("vk=219" in key_str):
            return "left_bracket"
        if vk in {30, 221} or ("vk=30" in key_str) or ("vk=221" in key_str):
            return "right_bracket"
        if vk in {27, 189} or ("vk=27" in key_str) or ("vk=189" in key_str):
            return "minus"
        if vk in {24, 187} or ("vk=24" in key_str) or ("vk=187" in key_str):
            return "equal"

        if "cmd" in key_name:
            return "command"
        if "alt" in key_name or "option" in key_name:
            return "option"
        if "ctrl" in key_name or "control" in key_name:
            return "control"
        if "shift" in key_name:
            return "shift"
        if "bracketleft" in key_name or "left bracket" in key_name:
            return "left_bracket"
        if "bracketright" in key_name or "right bracket" in key_name:
            return "right_bracket"
        if "minus" in key_name:
            return "minus"
        if "equal" in key_name:
            return "equal"
        if key_name in {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9"}:
            return key_name
        if len(key_name) == 1 and key_name in "abcdefghijklmnopqrstuvwxyz":
            return key_name
        if key_name in {"plus"}:
            return "plus"
        if key_name in {"comma"}:
            return "comma"
        if key_name in {"period", "dot"}:
            return "period"
        if key_name in {"slash", "forward_slash"}:
            return "slash"
        if key_name in {"semicolon"}:
            return "semicolon"
        if key_name in {"quote", "apostrophe"}:
            return "quote"
        if key_name in {"backslash"}:
            return "backslash"
        if key_name in {"left", "left_arrow"} or "left arrow" in key_name:
            return "left_arrow"
        if key_name in {"right", "right_arrow"} or "right arrow" in key_name:
            return "right_arrow"
        if key_name in {"up", "up_arrow"} or "up arrow" in key_name:
            return "up_arrow"
        if key_name in {"down", "down_arrow"} or "down arrow" in key_name:
            return "down_arrow"
        if "grave" in key_name:
            return "grave"
        return None

    @staticmethod
    def _normalize_modifier(key) -> Optional[str]:
        key_name = str(getattr(key, "name", "") or "").lower()
        if not key_name:
            key_name = str(key).lower()

        if "cmd" in key_name:
            return "command"
        if "alt" in key_name or "option" in key_name:
            return "option"
        if "ctrl" in key_name or "control" in key_name:
            return "control"
        if "shift" in key_name:
            return "shift"
        return None
