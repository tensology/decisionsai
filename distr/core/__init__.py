"""Core package helpers."""

import importlib
import types
import sys


def __getattr__(name):
    """Lazily expose selected submodules for test patch paths."""
    if name == "signals":
        # Avoid importing PyQt-bound signal module during headless/unit tests.
        module = types.ModuleType("distr.core.signals")
        module.signal_manager = types.SimpleNamespace()
        module.speak_text_directly_event_queue = lambda *_args, **_kwargs: None
        sys.modules["distr.core.signals"] = module
        globals()[name] = module
        return module
    if name == "settings":
        module = importlib.import_module(f"distr.core.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module 'distr.core' has no attribute '{name}'")
