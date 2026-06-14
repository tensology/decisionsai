"""Google OAuth configuration helpers."""

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


def load_google_oauth_config() -> Optional[dict[str, Any]]:
    """
    Load Google OAuth client configuration from the canonical location.

    File path: <project_root>/secrets/google_oauth_client_secret.json
    (defined as GOOGLE_OAUTH_SECRET_PATH in distr.core.paths)
    """
    from distr.core.paths import GOOGLE_OAUTH_SECRET_PATH

    if os.path.isfile(GOOGLE_OAUTH_SECRET_PATH):
        try:
            with open(GOOGLE_OAUTH_SECRET_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Error loading OAuth config from %s: %s", GOOGLE_OAUTH_SECRET_PATH, e)
            return None

    if not getattr(load_google_oauth_config, "_warned", False):
        logger.warning(
            "Google OAuth client secret not found at %s. "
            "Upload it via Settings > Advanced > Google.",
            GOOGLE_OAUTH_SECRET_PATH,
        )
        load_google_oauth_config._warned = True
    return None
