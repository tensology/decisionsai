"""ORM helpers for SQLAlchemy 1.4 vs 2.0 (Query.get removed in 2.0)."""

import time
from sqlite3 import OperationalError as SQLiteOperationalError
from typing import Any, Callable, Optional, Type, TypeVar

from sqlalchemy.exc import OperationalError

_T = TypeVar("_T")


def retry_sqlite_locked(operation: Callable[[], _T]) -> _T:
    """Retry a local transaction that opens a fresh session on each attempt."""
    delays = (0.05, 0.1)
    for delay in delays:
        try:
            return operation()
        except OperationalError as exc:
            if not isinstance(exc.orig, SQLiteOperationalError) or 'locked' not in str(exc.orig).lower():
                raise
            time.sleep(delay)
    return operation()


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
