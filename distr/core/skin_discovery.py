"""Dynamic skin discovery by scanning AVATARS_DIR for valid skin.json files.

Provides functions to discover all available skins and look up individual
skins by folder name.

Requirements: 11.1, 11.7, 8.3, 1.14
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple, Union

from distr.core.skin_config import SkinConfig, parse, validate

logger = logging.getLogger(__name__)


def discover_skins(
    avatars_dir: Union[str, Path],
) -> List[Tuple[str, SkinConfig]]:
    """Scan *avatars_dir* for folders containing valid ``skin.json`` files.

    Returns a list of ``(folder_name, SkinConfig)`` tuples sorted so that
    skins with ``type == "oracle"`` appear first, then alphabetically by name.

    Folders that are missing ``skin.json`` or contain invalid configs are
    silently excluded with a logged warning.
    """
    avatars_path = Path(avatars_dir)
    results: List[Tuple[str, SkinConfig]] = []

    if not avatars_path.is_dir():
        logger.warning("Avatars directory does not exist: %s", avatars_path)
        return results

    for entry in sorted(avatars_path.iterdir()):
        if not entry.is_dir():
            continue

        skin_json = entry / "skin.json"
        folder_name = entry.name

        if not skin_json.is_file():
            logger.warning(
                "Skin folder '%s' has no skin.json — skipping", folder_name
            )
            continue

        try:
            raw = skin_json.read_text(encoding="utf-8")
            config = parse(raw)
        except (ValueError, OSError) as exc:
            logger.warning(
                "Skin folder '%s' has invalid skin.json — skipping: %s",
                folder_name,
                exc,
            )
            continue

        errors = validate(config)
        if errors:
            logger.warning(
                "Skin folder '%s' failed validation — skipping: %s",
                folder_name,
                "; ".join(errors),
            )
            continue

        results.append((folder_name, config))

    # Sort: oracle type first, then alphabetically by name
    results.sort(key=lambda item: (item[1].type != "oracle", item[1].name.lower()))
    return results


def get_skin_by_name(
    avatars_dir: Union[str, Path],
    skin_name: str,
) -> Optional[Tuple[str, SkinConfig]]:
    """Return ``(folder_name, SkinConfig)`` for a single skin folder, or
    ``None`` if the folder does not exist or contains an invalid config."""
    skin_json = Path(avatars_dir) / skin_name / "skin.json"

    if not skin_json.is_file():
        logger.warning(
            "Skin '%s' not found — no skin.json at %s", skin_name, skin_json
        )
        return None

    try:
        raw = skin_json.read_text(encoding="utf-8")
        config = parse(raw)
    except (ValueError, OSError) as exc:
        logger.warning(
            "Skin '%s' has invalid skin.json: %s", skin_name, exc
        )
        return None

    errors = validate(config)
    if errors:
        logger.warning(
            "Skin '%s' failed validation: %s", skin_name, "; ".join(errors)
        )
        return None

    return (skin_name, config)
