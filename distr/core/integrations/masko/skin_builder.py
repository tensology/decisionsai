"""
Skin builder — constructs valid SkinConfig objects from generated assets
and writes them to disk.

Generates avatar-type skins with all 12 event hooks mapped to their
respective animation files, and optional transitions for animated mode.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Literal, Optional

from distr.core.skin_config import (
    SkinConfig,
    RenderingConfig,
    EventResponse,
    validate,
    to_json,
    EVENT_HOOKS as SKIN_CONFIG_HOOKS,
)

logger = logging.getLogger(__name__)

# Default rendering config for generated avatar skins
AVATAR_RENDERING = RenderingConfig(
    shape="square",
    border=False,
    shadow=False,
    glow_on_hold=False,
    chroma_key=None,
    chroma_threshold=30,
    image_scale=1.0,
    image_offset_x=0,
    image_offset_y=0,
)


def build_skin_json(
    name: str,
    mode: Literal["static", "animated"],
    hook_to_file: Dict[str, str],
    transitions: Optional[Dict[str, str]] = None,
) -> SkinConfig:
    """Construct a SkinConfig from generated assets.

    Args:
        name: Display name for the skin.
        mode: "static" (WebP images) or "animated" (WebM videos).
        hook_to_file: Mapping from event hook name to filename
                      (e.g. {"idle": "idle.webp"}).
        transitions: Mapping from "hookA-hookB" to filename for transitions
                     (animated mode only).

    Returns:
        A validated SkinConfig object.

    Raises:
        ValueError: If the generated config fails validation.
    """
    # Build events dict — ensure all 12 hooks are present
    events: Dict[str, EventResponse] = {}
    for hook in SKIN_CONFIG_HOOKS:
        filename = hook_to_file.get(hook, f"{hook}.webp" if mode == "static" else f"{hook}.webm")
        events[hook] = EventResponse(
            animation=filename,
            show_player=False,
            show_chat_bubble=False,
            glow=False,
            glow_color=(0, 0, 0),
            glow_speed=1000,
            glow_style="breathing",
            tray_icon="default",
            playback="loop",  # Both static and animated use "loop"
        )

    transitions_dict = transitions if transitions is not None else {}

    config = SkinConfig(
        type="avatar",
        name=name,
        rendering=AVATAR_RENDERING,
        events=events,
        transitions=transitions_dict,
    )

    # Validate before returning
    errors = validate(config)
    if errors:
        raise ValueError(f"Generated skin.json validation errors: {'; '.join(errors)}")

    return config


def write_skin(skin_dir: Path, config: SkinConfig) -> Path:
    """Write a skin.json file to disk.

    Args:
        skin_dir: Directory to write skin.json into.
        config: Validated SkinConfig object.

    Returns:
        Path to the written skin.json file.
    """
    skin_dir.mkdir(parents=True, exist_ok=True)
    json_path = skin_dir / "skin.json"
    json_path.write_text(to_json(config), encoding="utf-8")
    logger.info("Wrote skin.json to %s", json_path)
    return json_path