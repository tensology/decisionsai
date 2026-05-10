"""Shared hotkey constants for backend validation and web UI options."""

from __future__ import annotations

from itertools import combinations
from typing import Dict, List

MODIFIER_ORDER = ("control", "option", "shift", "command")
_BASE_MODIFIER_LABELS: Dict[str, str] = {
    "control": "Control",
    "option": "Option (Alt)",
    "shift": "Shift",
    "command": "Command",
}


def _modifier_combo_value(parts) -> str:
    part_set = set(parts)
    return "_".join(mod for mod in MODIFIER_ORDER if mod in part_set)


def _modifier_combo_label(value: str) -> str:
    return " + ".join(_BASE_MODIFIER_LABELS[token] for token in value.split("_"))

MODIFIER_LABELS: Dict[str, str] = {
    _modifier_combo_value(combo): _modifier_combo_label(_modifier_combo_value(combo))
    for length in range(1, len(MODIFIER_ORDER) + 1)
    for combo in combinations(MODIFIER_ORDER, length)
}

PTT_MODIFIERS = {"option", "command", "control", "shift"}
CHORD_MODIFIERS = set(MODIFIER_LABELS.keys())

KEY_LABELS: Dict[str, str] = {
    "left_arrow": "Left Arrow",
    "right_arrow": "Right Arrow",
    "up_arrow": "Up Arrow",
    "down_arrow": "Down Arrow",
    "left_bracket": "[",
    "right_bracket": "]",
    "minus": "-",
    "plus": "+",
    "equal": "=",
    "grave": "~ / `",
    "comma": ",",
    "period": ".",
    "slash": "/",
    "semicolon": ";",
    "quote": "'",
    "backslash": "\\",
}

for _digit in "0123456789":
    KEY_LABELS[_digit] = _digit
for _letter in "abcdefghijklmnopqrstuvwxyz":
    KEY_LABELS[_letter] = _letter.upper()

VALID_HOTKEY_KEYS = set(KEY_LABELS.keys())

# Canonical default shortcut profile (new DB rows, API fallbacks, web UI load defaults).
DEFAULTS = {
    "dictation_hotkey_enabled": True,
    "dictation_hotkey_modifier": "control_command",
    "dictation_hotkey_key": "",
    "global_ptt_hotkey_combo": "option_command",
    "oracle_size_hotkey_decrease_modifier": "control_command",
    "oracle_size_hotkey_decrease_key": "down_arrow",
    "oracle_size_hotkey_increase_modifier": "control_command",
    "oracle_size_hotkey_increase_key": "up_arrow",
    "recording_hotkey_modifier": "option_command",
    "recording_hotkey_key": "s",
    "skin_nav_hotkey_previous_modifier": "control_command",
    "skin_nav_hotkey_previous_key": "left_arrow",
    "skin_nav_hotkey_next_modifier": "control_command",
    "skin_nav_hotkey_next_key": "right_arrow",
    "skin_select_hotkey_modifier": "option_command",
    "web_hotkey_chat_modifier": "option_command",
    "web_hotkey_chat_key": "c",
    "web_hotkey_projects_modifier": "option_command",
    "web_hotkey_projects_key": "j",
    "web_hotkey_actions_modifier": "option_command",
    "web_hotkey_actions_key": "a",
    "web_hotkey_snippets_modifier": "option_command",
    "web_hotkey_snippets_key": "n",
    "web_hotkey_workflows_modifier": "option_command",
    "web_hotkey_workflows_key": "w",
    "web_hotkey_preferences_modifier": "option_command",
    "web_hotkey_preferences_key": "grave",
}


def modifier_options() -> List[dict]:
    return [{"value": value, "label": label} for value, label in MODIFIER_LABELS.items()]


def ptt_modifier_options() -> List[dict]:
    return [{"value": value, "label": MODIFIER_LABELS[value]} for value in ("option", "command", "control", "shift")]


def key_options() -> List[dict]:
    return [{"value": value, "label": label} for value, label in KEY_LABELS.items()]
