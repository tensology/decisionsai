"""Backward-compatible re-export from core_mixin.py.

New code should import from core_mixin directly.
"""

from distr.core.agent.services.llm.core_mixin import LLMSharedMixin

__all__ = ["LLMSharedMixin"]
