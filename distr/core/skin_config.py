"""SkinConfig data model, parser, serializer, and validator.

Defines the dataclasses for skin.json configuration files and provides
functions to parse JSON into SkinConfig, serialize SkinConfig to JSON,
and validate SkinConfig objects.

Requirements: 1.1-1.14, 2.1-2.4
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Literal, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EVENT_HOOKS: List[str] = [
    "idle",
    "hands_free_listening",
    "ptt_active",
    "dictation",
    "recording_action",
    "file_drop_success",
    "tts_response",
    "running_action",
    "running_step_runner",
    "snippet_copied",
    "thinking",
    "needs_attention",
]

GLOW_STYLES: List[str] = ["breathing", "pulse", "fade", "flash"]

PLAYBACK_MODES: List[str] = ["loop", "pingpong"]

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class EventResponse:
    """Behavioral response triggered by an Event_Hook."""

    animation: str
    show_player: bool = False
    show_chat_bubble: bool = False
    glow: bool = False
    glow_color: Tuple[int, int, int] = (0, 0, 0)
    glow_speed: int = 1000
    glow_style: str = "breathing"
    tray_icon: str = "default"
    playback: str = "loop"  # "loop" (forward restart) or "pingpong" (forward then backward)


@dataclass
class RenderingConfig:
    """Rendering metadata for a skin."""

    shape: Literal["round", "square"]
    border: bool
    shadow: bool
    glow_on_hold: bool
    image_scale: float = 1.0
    image_offset_x: int = 0
    image_offset_y: int = 0
    chroma_key: Tuple[int, int, int] | None = None  # RGB color to make transparent, e.g. (137, 218, 239)
    chroma_threshold: int = 30  # Color distance threshold for chroma-key removal


@dataclass
class SkinConfig:
    """Complete behavioral specification for a skin."""

    type: Literal["oracle", "avatar"]
    name: str
    rendering: RenderingConfig
    events: Dict[str, EventResponse]
    transitions: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Defaults for backward-compatible parsing of glow fields
# ---------------------------------------------------------------------------

_EVENT_RESPONSE_DEFAULTS = {
    "glow_color": [0, 0, 0],
    "glow_speed": 1000,
    "glow_style": "breathing",
    "tray_icon": "default",
    "playback": "loop",
}


# ---------------------------------------------------------------------------
# parse()
# ---------------------------------------------------------------------------


def parse(json_str: str) -> SkinConfig:
    """Parse a JSON string into a SkinConfig.

    Raises ``ValueError`` with a descriptive message on failure.
    Missing glow fields are filled with defaults for backward compatibility.
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Skin config must be a JSON object")

    # --- top-level required fields ---
    if "type" not in data:
        raise ValueError("Missing required field: 'type'")
    if "name" not in data:
        raise ValueError("Missing required field: 'name'")
    if "rendering" not in data:
        raise ValueError("Missing required field: 'rendering'")
    if "events" not in data:
        raise ValueError("Missing required field: 'events'")

    # --- rendering ---
    rdata = data["rendering"]
    if not isinstance(rdata, dict):
        raise ValueError("'rendering' must be a JSON object")
    for rfield in ("shape", "border", "shadow", "glow_on_hold"):
        if rfield not in rdata:
            raise ValueError(f"Missing required rendering field: '{rfield}'")
    rendering = RenderingConfig(
        shape=rdata["shape"],
        border=rdata["border"],
        shadow=rdata["shadow"],
        glow_on_hold=rdata["glow_on_hold"],
        image_scale=rdata.get("image_scale", 1.0),
        image_offset_x=rdata.get("image_offset_x", 0),
        image_offset_y=rdata.get("image_offset_y", 0),
        chroma_key=tuple(rdata["chroma_key"]) if "chroma_key" in rdata and rdata["chroma_key"] else None,
        chroma_threshold=rdata.get("chroma_threshold", 30),
    )

    # --- events ---
    events_data = data["events"]
    if not isinstance(events_data, dict):
        raise ValueError("'events' must be a JSON object")

    events: Dict[str, EventResponse] = {}
    for hook_name, resp_data in events_data.items():
        if not isinstance(resp_data, dict):
            raise ValueError(f"Event response for '{hook_name}' must be a JSON object")
        if "animation" not in resp_data:
            raise ValueError(f"Event '{hook_name}' missing required field: 'animation'")

        # Apply defaults for optional glow fields (backward compat)
        for key, default in _EVENT_RESPONSE_DEFAULTS.items():
            if key not in resp_data:
                resp_data[key] = default

        glow_color_raw = resp_data.get("glow_color", [0, 0, 0])
        if not isinstance(glow_color_raw, (list, tuple)) or len(glow_color_raw) != 3:
            raise ValueError(
                f"Event '{hook_name}': glow_color must be a 3-element list of ints"
            )
        glow_color = tuple(glow_color_raw)

        events[hook_name] = EventResponse(
            animation=resp_data["animation"],
            show_player=resp_data.get("show_player", False),
            show_chat_bubble=resp_data.get("show_chat_bubble", False),
            glow=resp_data.get("glow", False),
            glow_color=glow_color,
            glow_speed=resp_data.get("glow_speed", 1000),
            glow_style=resp_data.get("glow_style", "breathing"),
            tray_icon=resp_data.get("tray_icon", "default"),
            playback=resp_data.get("playback", "loop"),
        )

    # --- transitions (optional) ---
    transitions = data.get("transitions", {})
    if not isinstance(transitions, dict):
        raise ValueError("'transitions' must be a JSON object")

    return SkinConfig(
        type=data["type"],
        name=data["name"],
        rendering=rendering,
        events=events,
        transitions=transitions,
    )


# ---------------------------------------------------------------------------
# to_json()
# ---------------------------------------------------------------------------


def to_json(config: SkinConfig) -> str:
    """Serialize a SkinConfig to a JSON string.

    Uses consistent key ordering and 2-space indentation.
    """

    def _event_response_dict(er: EventResponse) -> dict:
        return {
            "animation": er.animation,
            "show_player": er.show_player,
            "show_chat_bubble": er.show_chat_bubble,
            "glow": er.glow,
            "glow_color": list(er.glow_color),
            "glow_speed": er.glow_speed,
            "glow_style": er.glow_style,
            "tray_icon": er.tray_icon,
            "playback": er.playback,
        }

    obj = {
        "type": config.type,
        "name": config.name,
        "rendering": {
            "shape": config.rendering.shape,
            "border": config.rendering.border,
            "shadow": config.rendering.shadow,
            "glow_on_hold": config.rendering.glow_on_hold,
            "image_scale": config.rendering.image_scale,
            "image_offset_x": config.rendering.image_offset_x,
            "image_offset_y": config.rendering.image_offset_y,
            "chroma_key": list(config.rendering.chroma_key) if config.rendering.chroma_key else None,
            "chroma_threshold": config.rendering.chroma_threshold,
        },
        "events": {
            hook: _event_response_dict(resp)
            for hook, resp in config.events.items()
        },
        "transitions": config.transitions,
    }
    return json.dumps(obj, indent=2)


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


def validate(config: SkinConfig) -> List[str]:
    """Return a list of validation errors for *config*. Empty list means valid."""
    errors: List[str] = []

    # type
    if config.type not in ("oracle", "avatar"):
        errors.append(f"type must be 'oracle' or 'avatar', got '{config.type}'")

    # name
    if not isinstance(config.name, str) or not config.name:
        errors.append("name must be a non-empty string")

    # events must contain 'idle'
    if "idle" not in config.events:
        errors.append("events must contain an 'idle' key")

    # validate each EventResponse
    for hook, resp in config.events.items():
        # animation
        if not isinstance(resp.animation, str) or not resp.animation:
            errors.append(f"events.{hook}.animation must be a non-empty string")

        # booleans
        if not isinstance(resp.show_player, bool):
            errors.append(f"events.{hook}.show_player must be a bool")
        if not isinstance(resp.show_chat_bubble, bool):
            errors.append(f"events.{hook}.show_chat_bubble must be a bool")
        if not isinstance(resp.glow, bool):
            errors.append(f"events.{hook}.glow must be a bool")

        # glow_color
        gc = resp.glow_color
        if (
            not isinstance(gc, (tuple, list))
            or len(gc) != 3
            or not all(isinstance(v, int) and 0 <= v <= 255 for v in gc)
        ):
            errors.append(
                f"events.{hook}.glow_color must be a 3-element tuple of ints 0-255"
            )

        # glow_speed
        if not isinstance(resp.glow_speed, int) or resp.glow_speed <= 0:
            errors.append(f"events.{hook}.glow_speed must be a positive integer")

        # glow_style
        if resp.glow_style not in GLOW_STYLES:
            errors.append(
                f"events.{hook}.glow_style must be one of {GLOW_STYLES}, "
                f"got '{resp.glow_style}'"
            )

        # tray_icon
        if not isinstance(resp.tray_icon, str) or not resp.tray_icon:
            errors.append(f"events.{hook}.tray_icon must be a non-empty string")

        # playback
        if resp.playback not in PLAYBACK_MODES:
            errors.append(
                f"events.{hook}.playback must be one of {PLAYBACK_MODES}, "
                f"got '{resp.playback}'"
            )

    # rendering constraints based on type
    if config.type == "oracle":
        r = config.rendering
        if r.shape != "round":
            errors.append("oracle skin rendering.shape must be 'round'")
        if r.border is not True:
            errors.append("oracle skin rendering.border must be true")
        if r.shadow is not True:
            errors.append("oracle skin rendering.shadow must be true")
        if r.glow_on_hold is not True:
            errors.append("oracle skin rendering.glow_on_hold must be true")
    elif config.type == "avatar":
        r = config.rendering
        if r.shape != "square":
            errors.append("avatar skin rendering.shape must be 'square'")
        if r.border is not False:
            errors.append("avatar skin rendering.border must be false")
        if r.shadow is not False:
            errors.append("avatar skin rendering.shadow must be false")
        if r.glow_on_hold is not False:
            errors.append("avatar skin rendering.glow_on_hold must be false")

    return errors
