"""Settings migration for legacy selected_oracle values.

Maps GIF filenames (e.g. ``"0.gif"``) to the ``"oracle"`` skin folder name,
passes through valid folder names unchanged, and defaults empty/None to
``"oracle"``.

Requirements: 13.1, 13.2, 13.3
"""

from __future__ import annotations

import re

_GIF_PATTERN = re.compile(r"^\d+\.gif$")


def migrate_selected_oracle(value: str | None) -> str:
    """Map a legacy ``selected_oracle`` value to a skin folder name.

    * ``None`` or empty string → ``"oracle"``
    * GIF filename like ``"0.gif"``, ``"1.gif"``, ``"12.gif"`` → ``"oracle"``
    * Anything else (already a folder name) → returned unchanged
    """
    if not value:
        return "oracle"

    if _GIF_PATTERN.match(value):
        return "oracle"

    return value
