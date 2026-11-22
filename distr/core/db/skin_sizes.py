"""Per-skin size persistence helpers."""
import logging
from . import get_session
from . import SkinSize
from distr.core.skin_config import DEFAULT_SKIN_SIZE_PX

logger = logging.getLogger(__name__)

def get_skin_size(skin_slug: str, default: int = DEFAULT_SKIN_SIZE_PX) -> int:
    """Return the saved size in pixels for *skin_slug*, or the default."""
    try:
        with get_session() as db:
            row = db.query(SkinSize).filter(SkinSize.skin_slug == skin_slug).first()
            return row.size_px if row else default
    except Exception as e:
        logger.debug("get_skin_size(%s) failed: %s", skin_slug, e)
        return default


def set_skin_size(skin_slug: str, size_px: int) -> None:
    """Persist *size_px* for *skin_slug*, upserting the row."""
    try:
        with get_session() as db:
            row = db.query(SkinSize).filter(SkinSize.skin_slug == skin_slug).first()
            if row:
                row.size_px = size_px
            else:
                db.add(SkinSize(skin_slug=skin_slug, size_px=size_px))
            db.commit()
    except Exception as e:
        logger.debug("set_skin_size(%s, %d) failed: %s", skin_slug, size_px, e)
