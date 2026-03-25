"""
Tools module for dynamic LangChain tool integration.

All tool class imports are lazy to avoid circular imports.
Use load_tools() to get tool instances, or import directly from submodules.
"""

from distr.core.agent.tools.base import BaseActionTool

# Lazy imports via __getattr__ to avoid circular import with services
_LAZY_IMPORTS = {
    'load_tools': 'distr.core.agent.tools.loader',
    'TOOL_REGISTRY': 'distr.core.agent.tools.loader',
}


def __getattr__(name):
    if name in _LAZY_IMPORTS:
        import importlib
        module = importlib.import_module(_LAZY_IMPORTS[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    'load_tools',
    'BaseActionTool',
    'TOOL_REGISTRY',
]
