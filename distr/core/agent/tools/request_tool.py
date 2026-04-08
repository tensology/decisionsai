"""
RequestToolTool — meta-tool that lets the LLM request a tool not currently
in its active tool set.

The tool accepts a query (exact tool name or natural-language description)
and delegates to an injection callback set by the session layer.  The
callback performs fuzzy matching against the full TOOL_REGISTRY and, on a
match, injects the cached tool instance into the session's active set.
"""

import logging
from typing import Callable, Optional, Tuple

from distr.core.agent.tools.base import BaseActionTool

logger = logging.getLogger(__name__)


class RequestToolTool(BaseActionTool):
    """Request a tool that is not currently available in the active tool set."""

    def __init__(
        self,
        on_tool_requested: Optional[Callable[[str], Tuple[bool, str]]] = None,
        **kwargs,
    ):
        super().__init__(
            name="request_tool",
            description=(
                "Request a tool that is not currently available in your active tool set. "
                "Use this when you need a capability you don't have access to right now. "
                "Provide either the exact tool name or a description of what you need."
            ),
            **kwargs,
        )
        object.__setattr__(self, "_on_tool_requested", on_tool_requested)

    # ------------------------------------------------------------------
    # Core execution
    # ------------------------------------------------------------------

    def _run(self, text: str = "", transcription: list = None, **kwargs) -> str:
        """Handle a tool-request query from the LLM.

        *text* is the query string — either an exact tool name or a
        natural-language description of the needed capability.
        """
        if self._on_tool_requested is None:
            logger.warning("RequestToolTool: injection callback not configured")
            return "Tool injection is not available in this session."

        success, message = self._on_tool_requested(text)

        if success:
            logger.info("RequestToolTool: injected tool for query %r", text)

        return message

    async def _arun(self, text: str = "", transcription: list = None, **kwargs) -> str:
        """Async variant — delegates to synchronous ``_run``."""
        return self._run(text=text, transcription=transcription, **kwargs)
