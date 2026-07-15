"""Project coding CLI backend registry."""

from .registry import (
    DEFAULT_BACKEND_ID,
    get_backend,
    get_backend_statuses,
    get_project_backend_id,
    list_backends,
    normalize_backend_id,
    resolve_backend_for_capabilities,
    run_project_task,
)

__all__ = [
    "DEFAULT_BACKEND_ID",
    "get_backend",
    "get_backend_statuses",
    "get_project_backend_id",
    "list_backends",
    "normalize_backend_id",
    "resolve_backend_for_capabilities",
    "run_project_task",
]
