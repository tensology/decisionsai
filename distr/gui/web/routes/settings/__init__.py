"""
Settings API routes — split into focused modules.

Each module exports a `register_routes(router, templates)` function
that attaches its endpoints to the shared APIRouter.
"""
from pathlib import Path
from fastapi import APIRouter
from fastapi.templating import Jinja2Templates

from . import (
    actions,
    snippets,
    projects,
    thirdparty,
    voices,
    general,
    audio,
    llms,
    advanced,
    logs,
    step_runner,
    workflows,
    skins,
)

_MODULES = [
    actions,
    snippets,
    projects,
    thirdparty,
    voices,
    general,
    audio,
    llms,
    advanced,
    logs,
    step_runner,
    workflows,
    skins,
]


def create_routes(templates_dir: Path, base_path: str = "") -> APIRouter:
    """
    Create and configure API routes for settings web UI.

    Args:
        templates_dir: Path to templates directory
        base_path: Base path prefix for static files

    Returns:
        Configured APIRouter with all settings endpoints
    """
    router = APIRouter()
    templates = Jinja2Templates(directory=str(templates_dir))

    for mod in _MODULES:
        mod.register_routes(router, templates)

    return router
