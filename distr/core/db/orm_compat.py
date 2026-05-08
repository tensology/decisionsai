"""ORM helpers for SQLAlchemy 1.4 vs 2.0 (Query.get removed in 2.0)."""

from typing import Any, Optional, Type, TypeVar

_T = TypeVar("_T")


def orm_get_by_id(session: Any, model: Type[_T], pk: Any) -> Optional[_T]:
    """Load a row by integer primary key ``id``. Works with SQLAlchemy 1.4 and 2.0 sessions."""
    if pk is None:
        return None
    get_fn = getattr(session, "get", None)
    if callable(get_fn):
        try:
            obj = get_fn(model, pk)
            if obj is not None:
                return obj
        except (TypeError, AttributeError):
            pass
    return session.query(model).filter_by(id=pk).first()
