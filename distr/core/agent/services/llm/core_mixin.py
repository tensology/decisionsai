"""
Shared LLM Service Mixin

Core mixin providing shared utility methods for all LLM services:
- Tool routing (semantic router integration)
- System prompt building and caching
- Chat lifecycle (on_chat_changed, on_chat_deleted, on_chat_cleared)
- process_frame (unified TranscriptionFrame handling)
- process_chat_input (unified text input from web/voice/telegram)
- send_welcome_message (with conversation summaries)
- Setters (listening, hands-free, speaker, agent name, TTS service)

Domain-specific behaviour is composed from separate mixins:
- VoiceDictationMixin  (voice_mixin.py)  — voice commands, dictation
- FastActionMixin      (fast_action_mixin.py) — fast action execution, read-this, clipboard
- TelegramMixin        (telegram_mixin.py) — telegram flag propagation, response emission
"""

import asyncio
import json
import logging
import os
import platform
import time
from typing import Optional

from distr.core.signals import signal_manager
from distr.core.agent.services.llm.text_utils import clean_text_for_tts
from distr.core.agent.services.llm.reflection import SelfReflectionMixin
from .mixins.voice import VoiceDictationMixin
from .mixins.fast_actions import FastActionMixin
from .mixins.telegram import TelegramMixin

logger = logging.getLogger(__name__)

# Fast-tool matcher runs tools immediately (before the LLM). These tools return
# user-facing prose — show the real result instead of the generic "Done" stub.
_FAST_MATCH_TOOLS_SHOW_FULL_RESULT = frozenset({
    "board_notes",
    "developer_context",
    "get_project_status",
    "list_projects",
    "open_page",
    "query_current_project",
    "switch_project",
    "system_info",
})


class LLMSharedMixin(SelfReflectionMixin, VoiceDictationMixin, FastActionMixin, TelegramMixin):
    MAX_DROPPED_ITEMS_IN_PROMPT = 30
    _MAX_CONTEXT_TURNS: int = 40
    """
    Mixin providing shared utility methods for LLM services.

    Expects the following attributes on self:
    - event_queue
    - chat_manager
    - _model_name
    - _is_dictating, _is_hands_free, _is_listening
    - _hands_free_before_dictation
    - set_hands_free(enabled)
    """

    def _get_provider_name(self) -> str:
        """Return the provider name for chat persistence. Inferred from class name."""
        return self.__class__.__name__.replace("LLMService", "")

    async def _surface_model_error(self, exc, operation: str = "generate a response") -> str:
        """Show model/provider failures in chat; speak a short human line only."""
        from distr.core.agent.libs import ErrorFrame, TextFrame
        from distr.core.llm_errors import format_model_error, format_model_error_for_tts
        from distr.core.agent.services.llm.text_utils import clean_text_for_tts

        msg = format_model_error(
            exc,
            provider=self._get_provider_name(),
            model=getattr(self, "_model_name", ""),
            operation=operation,
        )
        spoken = ""
        if getattr(self, "_speaker_enabled", False) and not getattr(self, "_is_telegram_request", False):
            spoken = clean_text_for_tts(
                format_model_error_for_tts(
                    exc,
                    provider=self._get_provider_name(),
                    model=getattr(self, "_model_name", ""),
                    operation=operation,
                ),
                spoken_prose=True,
            )
        try:
            if spoken:
                await self.push_frame(TextFrame(text=spoken), getattr(self, "_pipeline_direction", None))
        except Exception:
            pass
        try:
            await self.push_frame(ErrorFrame(error=msg), getattr(self, "_pipeline_direction", None))
        except Exception:
            pass
        try:
            self._messages.append({"role": "assistant", "content": msg})
            if self.chat_manager:
                current_chat = self.chat_manager.get_current_chat()
                if current_chat:
                    self.chat_manager.add_assistant_message(current_chat, msg)
        except Exception:
            pass
        if getattr(self, "event_queue", None):
            chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
            self.event_queue.put(("chat_stream_error", {"error": msg, "chat_id": chat_id}), block=False)
        if getattr(self, "_is_telegram_request", False):
            self._telegram_fallback_text = msg
        return msg

    def _build_tool_index_async(self):
        """Build the semantic tool retriever index in a background thread."""
        from distr.core.agent.tool_retriever import build_index_async

        tools = getattr(self, '_tools', None)
        if not tools:
            return

        build_index_async(list(tools))

        # Wire RequestToolTool callback now that _tools_dict is populated
        self._wire_request_tool_callback()

    def _wire_request_tool_callback(self):
        """Set the RequestToolTool injection callback on the cached instance.

        The callback fuzzy-matches the query against TOOL_REGISTRY keys and
        TOOL_DESCRIPTIONS values, then injects the matched tool into the
        session's active tool set (``self._tools`` / ``self._tools_dict``).
        """
        rtt = self._tools_dict.get("request_tool")
        if rtt is None:
            return

        def _on_tool_requested(query: str) -> tuple[bool, str, bool]:
            from distr.core.agent.tools.loader import (
                TOOL_REGISTRY,
                TOOL_DESCRIPTIONS,
                _get_tool_class,
                _tool_cache,
                list_all_cached_tool_instances,
            )
            from distr.core.agent.tool_telemetry import log_request_tool_event

            _mn = getattr(self, "_model_name", None)
            qnorm = (query or "").strip()
            qlow = qnorm.lower()

            if not qnorm:
                msg = "Provide a tool name or short description of the capability you need."
                log_request_tool_event(
                    query="",
                    success=False,
                    model_name=_mn,
                    message=msg,
                )
                return (False, msg, False)

            try:
                from thefuzz import fuzz
            except ImportError:
                try:
                    from fuzzywuzzy import fuzz
                except ImportError:
                    log_request_tool_event(
                        query=qnorm,
                        success=False,
                        model_name=_mn,
                        message="Fuzzy matching library not available.",
                    )
                    return (False, "Fuzzy matching library not available.", False)

            if not TOOL_REGISTRY:
                msg = "No tools are registered in this build."
                log_request_tool_event(
                    query=qnorm,
                    success=False,
                    model_name=_mn,
                    message=msg,
                )
                return (False, msg, False)

            # Deterministic Gmail routing guard:
            # If the query is clearly about Gmail/email, always route to
            # google_workspace when available instead of fuzzy fallback.
            gmail_query_tokens = ("gmail", "email", "inbox", "google workspace")
            if any(token in qlow for token in gmail_query_tokens):
                gw_tool = self._tools_dict.get("google_workspace")
                if gw_tool is not None:
                    msg = (
                        "For Gmail and email requests, use 'google_workspace' directly. "
                        "It is already available in your active set."
                    )
                    log_request_tool_event(
                        query=qnorm,
                        success=True,
                        injected_tool_name="google_workspace",
                        model_name=_mn,
                        injection_performed=False,
                        message=msg,
                    )
                    return (True, msg, False)

                cached_gw_tool = _tool_cache.get("google_workspace")
                if cached_gw_tool is not None:
                    self._tools.append(cached_gw_tool)
                    self._tools_dict[cached_gw_tool.name] = cached_gw_tool
                    if not hasattr(self, "_sticky_tool_names"):
                        self._sticky_tool_names = set()
                    self._sticky_tool_names.add(cached_gw_tool.name)
                    msg = (
                        "Tool 'google_workspace' is now available. "
                        "Please retry your Gmail/email request."
                    )
                    log_request_tool_event(
                        query=qnorm,
                        success=True,
                        injected_tool_name="google_workspace",
                        model_name=_mn,
                        injection_performed=True,
                        message=msg,
                    )
                    return (True, msg, True)

            # Google Calendar / scheduling (request_tool fuzzy match often misses GoogleWorkspaceTool).
            calendar_routing_tokens = (
                "google calendar",
                "gcal",
                "calendar event",
                "calendar events",
                "event creation",
                "create calendar",
                "bulk event",
                "recurring event",
                "create events",
            )
            if any(token in qlow for token in calendar_routing_tokens) or (
                "calendar" in qlow and any(w in qlow for w in ("create", "event", "schedule", "bulk", "recurring"))
            ):
                gw_tool = self._tools_dict.get("google_workspace")
                if gw_tool is not None:
                    msg = (
                        "For Google Calendar (create/read events, schedules), use the 'google_workspace' tool "
                        "directly. For several days or slots in one step use action='create_calendar_events_batch' "
                        "with params.events = list of {summary, start_time, end_time, description?, location?} (ISO times). "
                        "For a single event use create_calendar_event. Schedule reads: get_calendar_events / "
                        "get_schedule_tomorrow / get_schedule_this_week. Tool is already in your active set."
                    )
                    log_request_tool_event(
                        query=qnorm,
                        success=True,
                        injected_tool_name="google_workspace",
                        model_name=_mn,
                        injection_performed=False,
                        message=msg,
                    )
                    return (True, msg, False)

                cached_gw_tool = _tool_cache.get("google_workspace")
                if cached_gw_tool is not None:
                    self._tools.append(cached_gw_tool)
                    self._tools_dict[cached_gw_tool.name] = cached_gw_tool
                    if not hasattr(self, "_sticky_tool_names"):
                        self._sticky_tool_names = set()
                    self._sticky_tool_names.add(cached_gw_tool.name)
                    msg = (
                        "Tool 'google_workspace' is now available for Google Calendar and other Workspace APIs. "
                        "For multiple events use action='create_calendar_events_batch' with params.events as a list of "
                        "{summary, start_time, end_time, description?, location?} (ISO datetimes, max 100 per call). "
                        "For one event use create_calendar_event."
                    )
                    log_request_tool_event(
                        query=qnorm,
                        success=True,
                        injected_tool_name="google_workspace",
                        model_name=_mn,
                        injection_performed=True,
                        message=msg,
                    )
                    return (True, msg, True)

            # Screen capture / vision: fuzzy match often misses ScreenshotAnalyzerTool.
            screenshot_routing_tokens = (
                "screenshot",
                "screen capture",
                "screen shot",
                "screen interaction",
                "capture screen",
                "see my screen",
                "what do you see",
                "look at my screen",
                "see what i'm looking",
                "see what i am looking",
            )
            wants_screenshot_tool = any(token in qlow for token in screenshot_routing_tokens) or (
                "screen" in qlow
                and any(word in qlow for word in ("click", "see", "look", "capture", "shot", "assist"))
            )
            if wants_screenshot_tool:
                sa_tool = self._tools_dict.get("screenshot_analyzer")
                if sa_tool is not None:
                    msg = (
                        "For screenshots and on-screen vision tasks, use 'screenshot_analyzer' directly. "
                        "It is already available in your active set."
                    )
                    log_request_tool_event(
                        query=qnorm,
                        success=True,
                        injected_tool_name="screenshot_analyzer",
                        model_name=_mn,
                        injection_performed=False,
                        message=msg,
                    )
                    return (True, msg, False)

                cached_sa_tool = _tool_cache.get("screenshot_analyzer")
                if cached_sa_tool is not None:
                    self._tools.append(cached_sa_tool)
                    self._tools_dict[cached_sa_tool.name] = cached_sa_tool
                    if not hasattr(self, "_sticky_tool_names"):
                        self._sticky_tool_names = set()
                    self._sticky_tool_names.add(cached_sa_tool.name)
                    msg = (
                        "Tool 'screenshot_analyzer' is now available. "
                        "Call it to capture and analyze the screen."
                    )
                    log_request_tool_event(
                        query=qnorm,
                        success=True,
                        injected_tool_name="screenshot_analyzer",
                        model_name=_mn,
                        injection_performed=True,
                        message=msg,
                    )
                    return (True, msg, True)

            # Workflow questions: models often call request_tool with vague text instead of get_workflow.
            wf_topic = "workflow" in qlow or "automation" in qlow
            wf_detail = any(
                t in qlow
                for t in (
                    "step",
                    "pause",
                    "wait",
                    "confirm",
                    "approval",
                    "stuck",
                    "running",
                )
            )
            if wf_topic and wf_detail:
                for tn in ("get_workflow", "list_workflows"):
                    existing = self._tools_dict.get(tn)
                    if existing is not None:
                        msg = (
                            f"Use the '{tn}' tool directly — it is already in your active set. "
                            "Pass workflow_id from REFERENCE or from list_workflows."
                        )
                        log_request_tool_event(
                            query=qnorm,
                            success=True,
                            injected_tool_name=tn,
                            model_name=_mn,
                            injection_performed=False,
                            message=msg,
                        )
                        return (True, msg, False)
                    cached = _tool_cache.get(tn)
                    if cached is not None:
                        self._tools.append(cached)
                        self._tools_dict[cached.name] = cached
                        if not hasattr(self, "_sticky_tool_names"):
                            self._sticky_tool_names = set()
                        self._sticky_tool_names.add(cached.name)
                        msg = (
                            f"Tool '{tn}' is now available. "
                            "Call it with workflow_id from REFERENCE or list_workflows."
                        )
                        log_request_tool_event(
                            query=qnorm,
                            success=True,
                            injected_tool_name=tn,
                            model_name=_mn,
                            injection_performed=True,
                            message=msg,
                        )
                        return (True, msg, True)

            # Score against registry keys and description values
            scores: list[tuple[str, int]] = []
            for class_name in TOOL_REGISTRY:
                name_score = fuzz.token_set_ratio(qlow, class_name.lower())
                desc = TOOL_DESCRIPTIONS.get(class_name, "")
                desc_score = fuzz.token_set_ratio(qlow, desc.lower()) if desc else 0
                best = max(name_score, desc_score)
                if class_name == "GetWorkflowTool" and wf_topic and wf_detail:
                    best = min(100, best + 28)
                if class_name == "GoogleWorkspaceTool" and (
                    "calendar" in qlow
                    or "gcal" in qlow
                    or "schedule" in qlow
                    or "recurring" in qlow
                ):
                    best = min(100, best + 24)
                if class_name == "ScreenshotAnalyzerTool" and wants_screenshot_tool:
                    best = min(100, best + 30)
                scores.append((class_name, best))

            scores.sort(key=lambda x: x[1], reverse=True)

            if scores and scores[0][1] >= 75:
                matched_class = scores[0][0]
                best_score = scores[0][1]
                # Resolve by imported class — avoids collisions on duplicate short __name__
                matched_tool = None
                try:
                    expected_cls = _get_tool_class(matched_class)
                except Exception as exc:
                    logger.debug(
                        "request_tool: cannot import class %r: %s",
                        matched_class,
                        exc,
                    )
                    expected_cls = None
                if expected_cls is not None:
                    for tool in list_all_cached_tool_instances():
                        if isinstance(tool, expected_cls):
                            matched_tool = tool
                            break

                if matched_tool is None:
                    msg = f"Tool '{matched_class}' found in registry but not in cache."
                    log_request_tool_event(
                        query=qnorm,
                        success=False,
                        matched_registry_class=matched_class,
                        fuzzy_score=best_score,
                        top_candidates=[n for n, _ in scores[:5]],
                        model_name=_mn,
                        message=msg,
                    )
                    return (False, msg, False)

                # Already active — do not claim a fresh injection (misleading UX + telemetry)
                if matched_tool.name in self._tools_dict:
                    msg = (
                        f"Tool '{matched_tool.name}' is already in your active set — "
                        "you can call it directly."
                    )
                    log_request_tool_event(
                        query=qnorm,
                        success=True,
                        matched_registry_class=matched_class,
                        injected_tool_name=matched_tool.name,
                        fuzzy_score=best_score,
                        model_name=_mn,
                        injection_performed=False,
                        message=msg,
                    )
                    return (True, msg, False)

                self._tools.append(matched_tool)
                self._tools_dict[matched_tool.name] = matched_tool
                if not hasattr(self, "_sticky_tool_names"):
                    self._sticky_tool_names = set()
                self._sticky_tool_names.add(matched_tool.name)

                log_request_tool_event(
                    query=qnorm,
                    success=True,
                    matched_registry_class=matched_class,
                    injected_tool_name=matched_tool.name,
                    fuzzy_score=best_score,
                    model_name=_mn,
                    injection_performed=True,
                    message=f"Tool '{matched_tool.name}' is now available.",
                )
                return (
                    True,
                    f"Tool '{matched_tool.name}' is now available. Please retry your task.",
                    True,
                )

            # No match — return top 5 candidates
            top_5 = [name for name, _ in scores[:5]]
            msg = f"Tool not found. Closest matches: {', '.join(top_5)}"
            log_request_tool_event(
                query=qnorm,
                success=False,
                fuzzy_score=scores[0][1] if scores else None,
                top_candidates=top_5,
                model_name=_mn,
                message=msg,
            )
            return (False, msg, False)

        object.__setattr__(rtt, "_on_tool_requested", _on_tool_requested)

    def _get_filtered_tools(self, last_user_message: str = None):
        """Return tools filtered by semantic retriever with sticky-tools support.

        This is the single entry point for tool filtering across ALL providers.

        The sticky set contains only tools that were injected via RequestToolTool
        during this session (i.e. tools in self._tools_dict that are NOT in the
        original tool cache).  This ensures retrieval actually reduces the tool
        count while preserving on-demand injections.
        """
        if not last_user_message:
            return self._tools
        try:
            from distr.core.agent.tool_retriever import get_tool_retriever
            names = get_tool_retriever().retrieve(last_user_message, self._model_name)
            if names is None:  # kill switch active or index not ready
                return self._tools
            # Resolve retrieved names to tool instances from this session
            retrieved = [self._tools_dict[n] for n in names if n in self._tools_dict]
            retrieved_names = {t.name for t in retrieved}

            sticky_names = set(getattr(self, "_sticky_tool_names", set()))
            sticky = [t for t in self._tools if t.name not in retrieved_names and t.name in sticky_names]

            # Gmail/email exposure repair:
            # If user intent is Gmail-related and google_workspace is active in
            # this session, force-expose it even when semantic retrieval missed it.
            qlow = (last_user_message or "").lower()
            gmail_keywords = ("gmail", "email", "inbox", "google workspace")
            calendar_keywords = (
                "google calendar",
                "gcal",
                "calendar event",
                "calendar events",
                "event creation",
                "create calendar",
                "bulk event",
                "recurring event",
            )
            workspace_exposure_keywords = gmail_keywords + calendar_keywords
            if any(k in qlow for k in workspace_exposure_keywords) and "google_workspace" in self._tools_dict:
                gw_tool = self._tools_dict["google_workspace"]
                if gw_tool.name not in retrieved_names:
                    retrieved.append(gw_tool)
                    retrieved_names.add(gw_tool.name)
            delegated_source_keywords = ("telegram", "remote", "desktop", "browser", "phone")
            delegated_work_keywords = (
                "pdf",
                "document",
                "attachment",
                "file",
                "scope",
                "plan",
                "prep",
                "prepare",
                "http://",
                "https://",
                "website",
                "web page",
                "playwright",
                "browseruse",
                "browser use",
                "screenshot",
                "click",
            )
            delegated_handoff_keywords = ("codex", "cursor", "handoff", "implement", "report back")
            wants_delegated_plan = (
                any(k in qlow for k in delegated_source_keywords)
                and any(k in qlow for k in workspace_exposure_keywords + delegated_work_keywords)
                and any(k in qlow for k in delegated_handoff_keywords + ("mouse", "keyboard", "copy", "paste"))
            )
            if wants_delegated_plan and "delegated_workflow" in self._tools_dict:
                delegated_tool = self._tools_dict["delegated_workflow"]
                if delegated_tool.name not in retrieved_names:
                    retrieved.append(delegated_tool)
                    retrieved_names.add(delegated_tool.name)
            return retrieved + sticky
        except Exception as e:
            logger.debug("_get_filtered_tools fallback: %s", e)
            return self._tools

    def _check_fast_actions(self):
        """Check if the last user message triggers a fast action (bypasses LLM).

        Returns a DetectedAction if a fast action is found, or None.
        Shared across all providers.
        """
        from distr.core.agent.services.llm.fast_action_detector import detect_fast_action, ActionType
        from distr.core.agent.services.llm.bulk_instruction import should_bypass_fast_action_detection
        if getattr(self, "_bypass_fast_actions_for_turn", False):
            return None
        if not self._messages:
            return None
        last_message = self._messages[-1].get("content", "")
        if not isinstance(last_message, str):
            return None
        if should_bypass_fast_action_detection(last_message):
            return None
        if last_message in self._processed_fast_actions:
            return None
        fast_action = detect_fast_action(last_message)
        if fast_action and fast_action.confidence >= 0.9 and fast_action.action_type not in (ActionType.CONVERSATIONAL, ActionType.UNKNOWN):
            return fast_action
        return None

    def _fuzzy_match_tool(self, tool_name: str):
        """Find the closest matching tool when the LLM hallucinates a tool name.

        Returns the matched tool object, or None if no close match.
        """
        tools_dict = getattr(self, '_tools_dict', {})
        if not tools_dict:
            return None

        name_lower = tool_name.lower().replace('-', '_').replace(' ', '_')

        # Direct substring match — e.g. "list_boards" matches "kanban_ticket"
        for real_name, tool in tools_dict.items():
            desc = (getattr(tool, 'description', '') or '').lower()
            # Check if the hallucinated name's keywords appear in a tool's description
            keywords = name_lower.split('_')
            if len(keywords) >= 2 and all(kw in desc for kw in keywords):
                logger.info("Fuzzy tool match: '%s' → '%s' (keyword match in description)", tool_name, real_name)
                return tool

        # Partial name overlap — e.g. "list_workflows" matches "list_workflows_tool"
        for real_name, tool in tools_dict.items():
            if name_lower in real_name or real_name in name_lower:
                logger.info("Fuzzy tool match: '%s' → '%s' (substring match)", tool_name, real_name)
                return tool

        return None

    def _get_username(self) -> str:
        """Extract OS username (first name if available)."""
        import getpass
        try:
            system = platform.system()
            if system == "Windows":
                return os.getenv('USERNAME') or os.getenv('USER') or getpass.getuser()
            else:
                os_username = getpass.getuser()
                try:
                    import pwd
                    user_info = pwd.getpwnam(os_username)
                    full_name = user_info.pw_gecos.split(',')[0] if user_info.pw_gecos else os_username
                    if full_name and full_name != os_username:
                        return full_name.split()[0] if full_name.split() else os_username
                    return os_username
                except (ImportError, KeyError, AttributeError):
                    return os_username
        except Exception as e:
            logger.warning("Could not determine username: %s", e)
            return "User"

    def _ensure_user_message_persisted(
        self, text: str, *, skip: bool = False
    ) -> Optional[int]:
        """Ensure the user message is persisted to the current chat.

        NOTE: Does NOT auto-create chats. The web route should create chats
        explicitly before sending messages.
        """
        if skip:
            if getattr(self, "chat_manager", None):
                return self.chat_manager.get_current_chat()
            return None
        if not getattr(self, "chat_manager", None) or not (text or "").strip():
            return None
        current_chat_id = self.chat_manager.get_current_chat()
        if not current_chat_id:
            logger.debug("_ensure_user_message_persisted: No current chat, not auto-creating")
            return None
        src = (
            "telegram"
            if getattr(self, "_is_telegram_request", False)
            else None
        )
        self.chat_manager.add_user_message(
            current_chat_id, text.strip(), source_platform=src
        )
        try:
            signal_manager.chat_message_added.emit(int(current_chat_id), "user", text.strip())
            # Drop the web “live speech-to-text” preview row; the real user bubble follows via message_added.
            self._notify_transcription_progress(int(current_chat_id), "", True, True)
        except Exception:
            pass
        return current_chat_id

    # ------------------------------------------------------------------ #
    #  File/Folder context utilities                                      #
    # ------------------------------------------------------------------ #

    def _get_folder_structure_info(self, folder_path: str, max_depth: int = 2) -> str:
        """Get folder structure information for context."""
        try:
            if not os.path.exists(folder_path) or not os.path.isdir(folder_path):
                return "Folder not found"

            file_count = 0
            file_types = {}
            subdir_count = 0
            top_level_items = []

            for root, dirs, files in os.walk(folder_path):
                level = root.replace(folder_path, '').count(os.sep)

                if level > max_depth:
                    dirs[:] = []
                    continue

                dirs[:] = [d for d in dirs if not d.startswith('.')]
                visible_files = [f for f in files if not f.startswith('.')]
                file_count += len(visible_files)

                for f in visible_files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext:
                        file_types[ext] = file_types.get(ext, 0) + 1
                    else:
                        file_types['(no extension)'] = file_types.get('(no extension)', 0) + 1

                if level == 0:
                    subdir_count = len(dirs)
                    top_level_items = [d + '/' for d in dirs[:5]] + [f for f in visible_files[:5]]
                    if len(dirs) > 5 or len(visible_files) > 5:
                        top_level_items.append("...")

            parts = [f"{file_count} file(s)"]
            if file_types:
                type_summary = ", ".join(
                    f"{count} {ext}" for ext, count in
                    sorted(file_types.items(), key=lambda x: x[1], reverse=True)[:5]
                )
                parts.append(f"({type_summary})")
            if subdir_count > 0:
                parts.append(f"in {subdir_count} subdirector{'y' if subdir_count == 1 else 'ies'}")

            structure_info = " ".join(parts)
            if top_level_items:
                structure_info += f"\n    Top-level: {', '.join(top_level_items[:8])}"
            return structure_info
        except Exception as e:
            logger.debug("Error getting folder structure for %s: %s", folder_path, e)
            return "Unable to read folder structure"

    def _get_dropped_files_context(self, chat_id=None) -> str:
        """Get context about recently dropped files for the system prompt."""
        storage_dir = os.path.join(os.path.expanduser("~"), ".decisions", "dropped_files")
        storage_file = os.path.join(storage_dir, "current_files.json")

        logger.debug("_get_dropped_files_context: Checking storage_file=%s, chat_id=%s", storage_file, chat_id)

        if not os.path.exists(storage_file):
            return "No files have been dropped on the oracle ball yet."

        try:
            with open(storage_file, 'r') as f:
                data = json.load(f)
                files = data.get("files", [])
                dropped_folders = data.get("dropped_folders", [])
                chat_files_index = data.get("chat_files_index", {})

                file_chat_mapping = data.get("file_chat_mapping", {})
                folder_chat_mapping = data.get("folder_chat_mapping", {})
                file_timestamps = data.get("file_timestamps", {})
                folder_timestamps = data.get("folder_timestamps", {})

                if chat_id:
                    chat_bucket = chat_files_index.get(str(chat_id), {})
                    if isinstance(chat_bucket, dict):
                        bucket_files = chat_bucket.get("files", [])
                        bucket_folders = chat_bucket.get("dropped_folders", [])
                        if bucket_files or bucket_folders:
                            files = bucket_files
                            dropped_folders = bucket_folders
                            logger.info(
                                "_get_dropped_files_context: Using chat_files_index for chat_id=%s: %d folders, %d files",
                                chat_id, len(dropped_folders), len(files)
                            )

                    def is_associated_with_chat(item_path, mapping):
                        normalized_path = item_path.rstrip('/').rstrip('\\')
                        chat_ids = (
                            mapping.get(item_path) or
                            mapping.get(normalized_path) or
                            mapping.get(normalized_path + '/') or
                            []
                        )
                        if isinstance(chat_ids, list):
                            return chat_id in chat_ids
                        return chat_ids == chat_id

                    if not chat_bucket:
                        dropped_folders = [f for f in dropped_folders if is_associated_with_chat(f, folder_chat_mapping)]
                        files = [f for f in files if is_associated_with_chat(f, file_chat_mapping)]
                        logger.info("_get_dropped_files_context: Filtered for chat_id=%s: %d folders, %d files",
                                   chat_id, len(dropped_folders), len(files))

                if not files and not dropped_folders:
                    if chat_id:
                        return "No files have been dropped on the oracle ball in this chat yet."
                    return "No files have been dropped on the oracle ball yet."

                # Filter out files inside dropped folders
                individual_files = []
                for f in files:
                    is_inside_folder = False
                    for folder in dropped_folders:
                        try:
                            normalized_file = os.path.normpath(os.path.abspath(f))
                            normalized_folder = os.path.normpath(os.path.abspath(folder))
                            if normalized_file.startswith(normalized_folder + os.sep) or normalized_file == normalized_folder:
                                is_inside_folder = True
                                break
                        except (ValueError, OSError):
                            pass
                    if not is_inside_folder and os.path.exists(f):
                        individual_files.append(f)

                items_to_show = dropped_folders + individual_files
                existing_items = [item for item in items_to_show if os.path.exists(item)]

                if not existing_items:
                    return "Previously dropped files/folders are no longer available."

                # Sort by timestamp (most recent last)
                def get_item_timestamp(item_path):
                    return file_timestamps.get(item_path) or folder_timestamps.get(item_path) or 0

                existing_items.sort(key=get_item_timestamp)

                def format_timestamp(ts):
                    if not ts:
                        return "unknown time"
                    from datetime import datetime
                    dt = datetime.fromtimestamp(ts)
                    return dt.strftime("%Y-%m-%d %H:%M:%S")

                total_items = len(existing_items)
                folder_count = sum(1 for item in existing_items if os.path.isdir(item))
                file_count = sum(1 for item in existing_items if os.path.isfile(item))

                max_items = max(1, int(getattr(self, "MAX_DROPPED_ITEMS_IN_PROMPT", 30)))
                visible_items = existing_items[-max_items:] if total_items > max_items else existing_items

                if folder_count > 0 and file_count > 0:
                    context_parts = [f"Recently dropped items ({folder_count} folder(s), {file_count} file(s), {total_items} total, most recent is LAST):"]
                elif folder_count > 0:
                    context_parts = [f"Recently dropped folders ({folder_count} total, most recent is LAST). Folder contents are indexed and can be queried via RAG."]
                else:
                    context_parts = [f"Recently dropped files ({file_count} total, most recent is LAST):"]

                latest_item = existing_items[-1] if existing_items else None

                from distr.core.files.metadata import get_file_metadata

                visible_start = total_items - len(visible_items) + 1
                for i, item_path in enumerate(visible_items, visible_start):
                    item_ts = file_timestamps.get(item_path) or folder_timestamps.get(item_path)
                    timestamp_str = format_timestamp(item_ts) if item_ts else "unknown time"

                    if os.path.isdir(item_path):
                        folder_name = os.path.basename(item_path) or item_path
                        structure_info = self._get_folder_structure_info(item_path, max_depth=2)
                        marker = " [MOST RECENT]" if i == total_items else ""
                        context_parts.append(f"  [{i}] Folder: {folder_name} (full path: {item_path}){marker} - dropped at {timestamp_str}")
                        context_parts.append(f"      Contains: {structure_info}")
                        context_parts.append(f"      Path resolution: Files inside can be referenced as '{item_path}/filename.ext'")
                        context_parts.append(f"      Indexing: Use IndexFolderTool to index this folder for semantic search when needed")
                    elif os.path.isfile(item_path):
                        metadata = get_file_metadata(item_path)
                        info_parts = [f"Size: {metadata['size_human']}"]
                        if metadata['type_description'] != 'unknown':
                            info_parts.append(f"Type: {metadata['type_description']}")
                        if metadata['default_app']:
                            info_parts.append(f"Opens with: {metadata['default_app']}")
                        if metadata['image_info']:
                            img = metadata['image_info']
                            info_parts.append(f"Image: {img['width']}x{img['height']}px ({img['format']}, {img['mode']})")
                        if metadata['audio_info']:
                            audio = metadata['audio_info']
                            info_parts.append(f"Audio: {audio['duration_formatted']}, {audio['sample_rate']}Hz, {audio['channels']}ch ({audio.get('format', 'unknown')})")
                        file_info = " | ".join(info_parts)
                        marker = " [MOST RECENT]" if i == total_items else ""
                        context_parts.append(f"  [{i}] File: {item_path}{marker} - dropped at {timestamp_str}")
                        context_parts.append(f"      {file_info}")

                hidden_count = total_items - len(visible_items)
                if hidden_count > 0:
                    context_parts.append(f"  ... and {hidden_count} older item(s) not shown")

                context_parts.append("\nThese files/folders are available for file operations.")
                context_parts.append("When user says 'the file in that folder' or 'the file in the folder I dropped', resolve paths relative to the folder path shown above.")
                context_parts.append("Folders can be indexed on-demand using IndexFolderTool for semantic search when the user wants to search or query folder contents.")

                if visible_items and latest_item:
                    try:
                        folders = [item for item in visible_items if os.path.isdir(item)]
                        files_list = [item for item in visible_items if os.path.isfile(item)]
                        context_parts.append(f"\nIMPORTANT: When the user says 'the file I dropped', 'the folder I dropped', 'what I just dropped', 'the files I dropped', or similar phrases:")

                        if len(folders) == 1 and not files_list:
                            context_parts.append(f"  They are referring to the folder: {os.path.basename(folders[0]) or folders[0]} ({folders[0]})")
                        elif len(files_list) == 1 and not folders:
                            context_parts.append(f"  They are referring to the file: {os.path.basename(files_list[0])} ({files_list[0]})")
                        else:
                            context_parts.append(f"  They are referring to {len(visible_items)} item(s) (most recent is last):")
                            for idx, ip in enumerate(visible_items, 1):
                                fname = os.path.basename(ip) or ip
                                m = " [MOST RECENT]" if ip == latest_item else ""
                                kind = "Folder" if os.path.isdir(ip) else "File"
                                context_parts.append(f"    [{idx}] {kind}: {fname} ({ip}){m}")

                        latest_name = os.path.basename(latest_item) or latest_item
                        kind = "Folder" if os.path.isdir(latest_item) else "File"
                        context_parts.append(f"\n  MOST RECENT (when user says 'the last one'): {kind}: {latest_name} ({latest_item})")
                    except Exception as e:
                        logger.warning("Error generating explicit reference section: %s", e)

                return "\n".join(context_parts)
        except Exception as e:
            logger.debug("Error reading dropped files context: %s", e)
            return "Unable to retrieve dropped files information."

    # ------------------------------------------------------------------ #
    #  Interruption cleanup                                               #
    # ------------------------------------------------------------------ #

    def _emit_interruption_cleanup(self):
        """Emit signals to restore chat window UI after interruption."""
        try:
            current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
            if not current_chat_id:
                return
            if self.event_queue:
                self.event_queue.put(('chat_stream_finished', {'chat_id': current_chat_id, 'response_text': ''}), block=False)
                self.event_queue.put(('typing_indicator_changed', {'show': False}), block=False)
            else:
                signal_manager.chat_stream_finished.emit(current_chat_id)
                signal_manager.typing_indicator_changed.emit(False)
        except Exception as e:
            logger.warning("Error in _emit_interruption_cleanup: %s", e)

    def _prepare_agent_voice_response(self) -> None:
        """Clear stale PTT interrupt flags so the next spoken reply can play."""
        self._cancelled = False
        self._arm_desktop_tts()
        tts = getattr(self, "_tts_service", None)
        if not tts:
            return
        tts._cancelled = False
        if hasattr(tts, "_cancelled_since"):
            tts._cancelled_since = 0.0
        if hasattr(tts, "reset_tts_response_start"):
            tts.reset_tts_response_start()

    def _is_dictation_transcript(self) -> bool:
        return bool(
            self._is_dictating
            or getattr(self, "_one_shot_dictation_armed", False)
            or getattr(self, "_dictation_release_pending", False)
        )

    def _is_critical_tool_run_in_progress(self) -> bool:
        """Return True when a tool run is actively executing and should not be interrupted."""
        return bool(getattr(self, '_tool_execution_in_progress', False))

    def _notify_transcription_progress(
        self,
        chat_id: int,
        text: str,
        done: bool = False,
        clear_live_preview: bool = False,
        discard_live_preview: bool = False,
    ) -> None:
        """Forward live STT / preview to the desktop app and web (agent runs in a subprocess).

        Qt signals do not cross the agent boundary; event_queue is required for the web UI.
        """
        payload = {
            'chat_id': int(chat_id),
            'status_text': text or '',
            'done': bool(done),
            'clear_live_preview': bool(clear_live_preview),
            'discard_live_preview': bool(discard_live_preview),
        }
        if getattr(self, 'event_queue', None):
            try:
                self.event_queue.put(('transcription_progress', payload), block=False)
            except Exception:
                pass
            return
        try:
            signal_manager.transcription_progress.emit(
                int(chat_id), text or '', bool(done), bool(clear_live_preview), bool(discard_live_preview),
            )
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Conversation summary (LLM call — used by FastActionMixin)          #
    # ------------------------------------------------------------------ #

    async def _generate_conversation_summary(self, conversation_messages: list) -> str:
        """Generate a summary of the conversation using the LLM."""
        if not conversation_messages:
            return "We haven't talked about anything yet. This is the start of our conversation!"

        conversation_text = ""
        for msg in conversation_messages[-10:]:
            role = msg.get('role', 'user')
            content = msg.get('content', '').strip()
            if content:
                prefix = "User" if role == 'user' else "Assistant"
                conversation_text += f"{prefix}: {content}\n"

        if not conversation_text.strip():
            return "We haven't had much conversation yet."

        try:
            import ollama as _ollama
            client = _ollama.AsyncClient(timeout=120.0)
            summary_prompt = (
                "You are summarizing a conversation between YOURSELF (an AI assistant) and a user.\n\n"
                "CRITICAL INSTRUCTIONS:\n"
                "- Summarize what WE (you and the user) were ACTUALLY TALKING ABOUT\n"
                "- Use 'We' to refer to yourself and the user\n"
                "- Focus on TOPICS, STORIES, QUESTIONS, or TASKS discussed\n"
                "- DO NOT mention tools, capabilities, functions, or system features\n"
                "- Keep it conversational and natural (2-3 sentences max)\n\n"
                f"Conversation:\n{conversation_text}\n\n"
                "Provide a brief, natural summary of what we were actually talking about (using 'We'):"
            )
            response = await client.chat(
                model=self._model_name,
                messages=[{"role": "user", "content": summary_prompt}],
                options={"keep_alive": -1},
            )
            summary = response.get('message', {}).get('content', '').strip()
            bad_words = ['tools', 'functions', 'actions', 'capabilities', 'features',
                         'f1-f12', 'function keys', 'oracle/globe', 'chatbot system']
            if summary and not any(w in summary.lower() for w in bad_words):
                return summary
        except Exception as e:
            logger.error("Error generating conversation summary: %s", e, exc_info=True)

        user_messages = [m.get('content', '') for m in conversation_messages if m.get('role') == 'user']
        if user_messages:
            return f"We were talking about {user_messages[-1][:100].lower()}."
        return "We were having a conversation, but I can't provide a summary right now."

    # ------------------------------------------------------------------ #
    #  System prompt building                                             #
    # ------------------------------------------------------------------ #

    def _build_system_prompt_template(self, chat_id=None, include_tools_description=True):
        """Build the formatted system prompt template with folder paths and dropped files.

        Caches the result and skips the expensive rebuild when dropped-files
        context hasn't changed.
        """
        from distr.core.settings import get_system_folder_paths
        from distr.core.agent.services.llm.prompt import build_tools_description

        dropped_files_context = self._get_dropped_files_context(chat_id=chat_id)
        developer_context_text = ""
        try:
            from distr.core.developer_context import build_developer_context

            developer_context_text = build_developer_context(chat_id=chat_id).to_prompt_text(max_chars=2200)
        except Exception:
            logger.warning("Could not build developer workflow context", exc_info=True)

        desktop_inject = ""
        try:
            from distr.core.desktop_awareness import get_desktop_inject_block

            desktop_inject = get_desktop_inject_block(mark_injected=True) or ""
        except Exception:
            desktop_inject = ""

        ctx_hash = hash((dropped_files_context, developer_context_text, desktop_inject, include_tools_description))

        if (hasattr(self, '_cached_template_hash')
                and self._cached_template_hash == ctx_hash
                and hasattr(self, '_cached_template')
                and self._cached_template):
            return self._cached_template

        folder_paths = get_system_folder_paths()
        home_path = os.path.expanduser("~")

        tools_desc = build_tools_description(self._tools) if include_tools_description else ""

        template = self._default_template_raw.format(
            agent_name=self._agent_name,
            username=self._username,
            tools_description=tools_desc,
            model_name=self._model_name,
            desktop_path=folder_paths.get("Desktop", os.path.join(home_path, "Desktop")),
            documents_path=folder_paths.get("Documents", os.path.join(home_path, "Documents")),
            downloads_path=folder_paths.get("Downloads", os.path.join(home_path, "Downloads")),
            pictures_path=folder_paths.get("Pictures", os.path.join(home_path, "Pictures")),
            music_path=folder_paths.get("Music", os.path.join(home_path, "Music")),
            videos_path=folder_paths.get("Videos", os.path.join(home_path, "Videos")),
            home_path=home_path,
            dropped_files_context=dropped_files_context,
        )

        try:
            from distr.core.agent.services.rag.project import get_active_project_context
            project_context = get_active_project_context()
            if project_context:
                template += f"\n\n{project_context}"
        except Exception as e:
            logger.warning("Could not inject project context: %s", e)

        if developer_context_text:
            template += f"\n\n{developer_context_text}"

        if desktop_inject:
            template += (
                "\n\nAmbient desktop (cached accessibility summary; may be seconds old; "
                "not a live feed). For targeting use get_window_tree / find_element / "
                "get_desktop_snapshot:\n"
                f"{desktop_inject}"
            )

        self._cached_template = template
        self._cached_template_hash = ctx_hash
        return template

    def _build_system_message(self, chat_id=None, include_tools_description=True):
        """Build the full system message dict (persona + template)."""
        # Ollama receives tool schemas via the API — skip embedding them in the prompt
        provider = self._get_provider_name().lower() if hasattr(self, '_get_provider_name') else ''
        if provider == 'ollama':
            include_tools_description = False

        template = self._build_system_prompt_template(chat_id=chat_id, include_tools_description=include_tools_description)
        self.default_template = template

        # Condense for local models (Ollama) to reduce token count
        if provider == 'ollama':
            template = self._condense_for_local(template)

        persona = getattr(self, '_persona', None)
        content = f"{persona}\n\n{template}" if persona else template

        # Append recent tool reflection context so the LLM is aware of prior
        # tool outcomes (failures, loop patterns) when composing its response.
        if hasattr(self, 'get_session_reflection'):
            reflection = self.get_session_reflection()
            if reflection:
                content = f"{content}\n\n{reflection}"

        if chat_id:
            try:
                from distr.core.chat import get_compact_checkpoint_prompt

                checkpoint_prompt = get_compact_checkpoint_prompt(chat_id)
                if checkpoint_prompt:
                    content = f"{content}\n\n{checkpoint_prompt}"
            except Exception as e:
                logger.debug(
                    "%s: compact checkpoint prompt skipped: %s",
                    self._get_provider_name(),
                    e,
                )

        return {"role": "system", "content": content}

    @staticmethod
    def _condense_for_local(text: str) -> str:
        """Strip verbose sections from the system prompt for local models.

        Removes the REST API reference (available at /docs/) and trims
        excessive per-tool examples while keeping all behavioral rules.
        """
        import re

        # Remove the REST API REFERENCE section entirely (7-8K chars, available at /docs/)
        text = re.sub(
            r'═+\s*\nREST API REFERENCE\s*\n═+.*?(?=═{3,}|\Z)',
            '', text, flags=re.DOTALL,
        )

        # Remove the decorative ═══ separator lines (saves ~1.5K chars)
        text = re.sub(r'═{10,}\n?', '', text)

        # Collapse runs of 3+ blank lines into 2
        text = re.sub(r'\n{4,}', '\n\n\n', text)

        return text

    # ------------------------------------------------------------------ #
    #  One-liner setters                                                  #
    # ------------------------------------------------------------------ #

    def set_listening(self, enabled: bool):
        self._is_listening = enabled

    def set_hands_free(self, enabled: bool):
        self._is_hands_free = enabled

    def set_speaker_enabled(self, enabled: bool):
        self._speaker_enabled = enabled

    def set_agent_name(self, agent_name: str):
        """Update agent name and regenerate the system prompt template."""
        old_name = self._agent_name
        self._agent_name = agent_name

        if not hasattr(self, '_default_template_raw') or not self._default_template_raw:
            logger.warning(f"LLM agent name updated to '{agent_name}' but template not yet initialized")
            return

        self._build_system_prompt_template(
            chat_id=self.chat_manager.get_current_chat() if self.chat_manager else None
        )
        logger.info(f"LLM agent name updated: '{old_name}' -> '{agent_name}'")

    def set_tts_service(self, tts_service):
        """Set TTS service after initialization and reload tools.

        Uses the tool cache when available so tools are not re-instantiated.
        Re-wires the RequestToolTool callback since self._tools_dict changed.
        """
        from distr.core.agent.tools import load_tools
        from distr.core.agent.tools.loader import _tool_cache, get_warmed_tools_list
        self._tts_service = tts_service
        try:
            if _tool_cache:
                # Cache is warm — reuse cached instances (fast path)
                self._tools = get_warmed_tools_list()
            else:
                # Cache not warm — fall back to full instantiation
                self._tools = load_tools(
                    chat_manager=self.chat_manager, use_navigation_tools=True,
                    llm_service=self, tts_service=tts_service,
                    llm_model=self._model_name, event_queue=self.event_queue,
                    command_queue=self.command_queue,
                    confirmation_results_dict=self.confirmation_results_dict,
                )
            self._tools_dict = {tool.name: tool for tool in self._tools}
            self._wire_request_tool_callback()
            logger.debug(f"Reloaded {len(self._tools)} tools with TTS service")
        except Exception as e:
            logger.warning(f"Failed to reload tools with TTS service: {e}")

    # ------------------------------------------------------------------ #
    #  _on_files_indexed / _setup_system_prompt                           #
    # ------------------------------------------------------------------ #

    def _on_files_indexed(self, notification_message: str):
        """Handle notification when files are dropped."""
        try:
            current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
            system_msg = self._build_system_message(chat_id=current_chat_id)
            if self._messages and self._messages[0].get('role') == 'system':
                self._messages[0] = system_msg
            else:
                self._messages.insert(0, system_msg)

            self._messages.append({
                "role": "system",
                "content": f"[SYSTEM NOTIFICATION] {notification_message}",
            })
            logger.debug("%s: Updated system message with fresh dropped files context", self._get_provider_name())
        except Exception as e:
            logger.error("Error handling files_indexed notification: %s", e, exc_info=True)

    def _setup_system_prompt(self, system_prompt=None):
        """Initialize the system prompt template and build the first system message."""
        from distr.core.agent.services.llm.prompt import load_system_prompt_template
        self._default_template_raw = load_system_prompt_template(
            model_name=getattr(self, "_model_name", None),
            provider_name=self._get_provider_name() if hasattr(self, "_get_provider_name") else None,
        )
        self._persona = system_prompt if system_prompt else None

        system_msg = self._build_system_message(
            chat_id=self.chat_manager.get_current_chat() if self.chat_manager else None
        )
        self._messages = [system_msg]

    # ------------------------------------------------------------------ #
    #  Chat lifecycle                                                     #
    # ------------------------------------------------------------------ #

    def on_chat_changed(self, chat_id):
        """Update LLM context when the active chat changes."""
        if not self.chat_manager:
            return

        if hasattr(self, '_generation_task') and self._generation_task and not self._generation_task.done():
            self._cancelled = True
            self._generation_task.cancel()
            self._emit_interruption_cleanup()

        # Cancel any running background chain
        if hasattr(self, '_background_chain') and self._background_chain:
            self._background_chain.cancel()
            self._background_chain = None

        self._cancelled = False

        try:
            system_prompt = self._build_system_message(chat_id=chat_id)
        except Exception as e:
            logger.warning("%s: Failed to rebuild system prompt: %s", self._get_provider_name(), e)
            system_prompt = self._messages[0] if self._messages else {"role": "system", "content": getattr(self, 'default_template', '')}

        if not chat_id:
            self._messages = [system_prompt]
            return

        try:
            history = self.chat_manager.get_chat_history(chat_id)
            new_messages = [msg for msg in history if msg.get('role') != 'system']
            validated = self._validate_messages(new_messages) if hasattr(self, '_validate_messages') else new_messages
            self._messages = [system_prompt] + validated if validated else [system_prompt]
            self._apply_context_window()
            logger.debug("%s: Loaded %d messages for chat %s", self._get_provider_name(), len(validated), chat_id)
        except Exception as e:
            logger.error("Error loading chat history: %s", e, exc_info=True)
            self._messages = [system_prompt]

    def on_chat_deleted(self, chat_id):
        """Clear conversation history when a chat is deleted."""
        current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
        if current_chat_id == chat_id:
            system_prompt = self._messages[0] if self._messages else None
            self._messages = [system_prompt] if system_prompt else []

            new_current = self.chat_manager.get_current_chat() if self.chat_manager else None
            if new_current and new_current != chat_id:
                self.on_chat_changed(new_current)

    def on_chat_cleared(self, chat_id):
        """Reset messages to system prompt only when a chat is cleared."""
        current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
        if current_chat_id == chat_id:
            system_prompt = self._messages[0] if self._messages else {"role": "system", "content": getattr(self, 'default_template', '')}
            self._messages = [system_prompt]

    # ------------------------------------------------------------------ #
    #  Context window management                                         #
    # ------------------------------------------------------------------ #

    def _apply_context_window(self) -> None:
        """Truncate _messages to system prompt + last _MAX_CONTEXT_TURNS * 2.

        Keeps the system message intact. For all non-system messages, only the
        most recent `_MAX_CONTEXT_TURNS` conversational turns (user + assistant
        pairs) are retained. Older turns are dropped to prevent context-window
        overruns and degraded LLM coherence in long sessions.

        Run after any mutation to self._messages that adds non-system content.
        """
        if not self._messages:
            return
        # Separate system message(s) from conversation
        system_msgs = [m for m in self._messages if m.get("role") == "system"]
        conv_msgs = [m for m in self._messages if m.get("role") != "system"]
        max_conv = self._MAX_CONTEXT_TURNS * 2  # user + assistant per turn
        if len(conv_msgs) > max_conv:
            trimmed = conv_msgs[-max_conv:]
            self._messages = system_msgs + trimmed
            logger.info(
                "%s: context window trimmed from %d to %d messages (%d turns)",
                self._get_provider_name(),
                len(conv_msgs),
                len(trimmed),
                self._MAX_CONTEXT_TURNS,
            )                                                 #
    # ------------------------------------------------------------------ #

    async def process_chat_input(self, text: str, is_telegram: bool = False,
                                  uploaded_image_path: str = None, speaker_enabled=None,
                                  telegram_input_type: str = None,
                                  skip_user_persist: bool = False):
        """Process text input from chat window. Unified for all providers."""
        self._cancelled = False
        if hasattr(self, '_generation_requested_at'):
            self._generation_requested_at = time.monotonic()
        await asyncio.sleep(0.05)

        if speaker_enabled is not None:
            self._speaker_enabled = bool(speaker_enabled)

        logger.debug("%s: Processing chat input: '%s...' (is_telegram=%s)",
                     self._get_provider_name(), text[:50], is_telegram)

        self._is_telegram_request = is_telegram
        self._uploaded_image_path = uploaded_image_path
        self._telegram_input_type = telegram_input_type if telegram_input_type in ("text", "voice") else None

        if not is_telegram:
            self._arm_desktop_tts()
        elif is_telegram:
            import threading
            threading.current_thread().telegram_request = True
            if self._telegram_input_type:
                threading.current_thread().telegram_input_type = self._telegram_input_type

        # Verify chat provider matches this service
        provider_name = self._get_provider_name()
        if self.chat_manager:
            current_chat_id = self.chat_manager.get_current_chat()
            if current_chat_id:
                try:
                    from distr.core.db import get_session, Chat
                    session = get_session()
                    chat = session.get(Chat, current_chat_id)
                    if chat and chat.provider:
                        p = (chat.provider or "").strip().lower()
                        if p and p != provider_name.lower():
                            logger.error("%s cannot process chat with provider '%s'. Rejecting.",
                                         self._get_provider_name(), chat.provider)
                            session.close()
                            return
                    session.close()
                except Exception as e:
                    logger.warning("Could not verify chat provider: %s", e)

        from distr.core.agent.services.llm.bulk_instruction import should_bypass_fast_action_detection

        self._bypass_fast_actions_for_turn = bool(
            skip_user_persist or should_bypass_fast_action_detection(text)
        )
        self._ensure_user_message_persisted(text, skip=skip_user_persist)

        # Handle vision input
        from distr.core.agent.services.llm.image_utils import (
            get_image_path_from_context, convert_image_to_base64,
            check_vision_model_support, analyze_image_with_vision_llm,
        )

        from distr.core.agent.services.llm.bulk_instruction import augment_bulk_instruction

        user_message_content = augment_bulk_instruction(
            text,
            source="telegram" if is_telegram else "chat",
        )
        image_path = get_image_path_from_context(uploaded_image_path)

        if image_path and os.path.exists(image_path):
            logger.debug("📸 Image found: %s", image_path)
            chat_model_vision = check_vision_model_support(self._model_name, provider_name)

            if chat_model_vision:
                try:
                    base64_image, mime_type = convert_image_to_base64(image_path)
                    user_message_content = self._format_vision_message(user_message_content, base64_image, mime_type)
                    logger.debug("✅ Image embedded in message (native vision)")
                except Exception as e:
                    logger.error("Failed to embed image: %s", e, exc_info=True)
                    user_message_content = augment_bulk_instruction(
                        text,
                        source="telegram" if is_telegram else "chat",
                    )
            else:
                analysis = await analyze_image_with_vision_llm(image_path, text)
                if analysis:
                    user_message_content = (
                        f"[The user uploaded an image. Here's what's in the image: {analysis}]\n\n"
                        f"User's question: {user_message_content}"
                    )
                else:
                    user_message_content = augment_bulk_instruction(
                        text,
                        source="telegram" if is_telegram else "chat",
                    )
        elif image_path:
            logger.warning("Image path provided but file doesn't exist: %s", image_path)

        self._messages.append({"role": "user", "content": user_message_content})

        # Cancel any running background chain before starting new generation
        if hasattr(self, '_background_chain') and self._background_chain:
            self._background_chain.cancel()
            self._background_chain = None

        if hasattr(self, '_generation_task') and self._generation_task and not self._generation_task.done():
            self._cancelled = True
            self._generation_task.cancel()
            try:
                await self._generation_task
            except asyncio.CancelledError:
                pass
            self._cancelled = False

        self._generation_task = asyncio.create_task(self._generate_response())

    def _format_vision_message(self, text: str, base64_image: str, mime_type: str):
        """Format a vision message. Override in subclass for provider-specific format."""
        return [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}}
        ]

    # ------------------------------------------------------------------ #
    #  process_frame                                                      #
    # ------------------------------------------------------------------ #

    async def process_frame(self, frame, direction):
        """Process incoming frames — common routing for all providers."""
        from distr.core.agent.libs import (
            StartFrame, CancelFrame, InterruptionFrame, TranscriptionFrame,
            UserStartedSpeakingFrame, TextFrame,
            LLMFullResponseStartFrame, LLMFullResponseEndFrame,
        )
        from distr.core.agent.services.llm.fast_action_detector import detect_fast_action, ActionType
        from distr.core.agent.tools.base import fast_tool_matcher

        self._pipeline_direction = direction

        if isinstance(frame, StartFrame):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, CancelFrame):
            if hasattr(self, '_generation_requested_at') and self._generation_requested_at > 0:
                now = time.monotonic()
                if (now - self._generation_requested_at) < 0.5:
                    logger.debug("LLM: Ignoring stale CancelFrame (%.0fms since generation requested)",
                                 (now - self._generation_requested_at) * 1000)
                    return
            self._cancelled = True
            if hasattr(self, '_generation_task') and self._generation_task and not self._generation_task.done():
                self._generation_task.cancel()
            if hasattr(self, '_background_chain') and self._background_chain:
                self._background_chain.cancel()
                self._background_chain = None
            self._emit_interruption_cleanup()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserStartedSpeakingFrame):
            if self._is_hands_free:
                if self._is_critical_tool_run_in_progress():
                    logger.info("LLM: Ignoring hands-free interruption during critical tool execution")
                    await self.push_frame(frame, direction)
                    return
                self._cancelled = True
                if hasattr(self, '_generation_task') and self._generation_task and not self._generation_task.done():
                    self._generation_task.cancel()
                if hasattr(self, '_background_chain') and self._background_chain:
                    self._background_chain.cancel()
                    self._background_chain = None
                self._emit_interruption_cleanup()
                await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterruptionFrame):
            self._cancelled = True
            if hasattr(self, '_generation_task') and self._generation_task and not self._generation_task.done():
                self._generation_task.cancel()
            if hasattr(self, '_background_chain') and self._background_chain:
                self._background_chain.cancel()
                self._background_chain = None
            self._emit_interruption_cleanup()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TranscriptionFrame):
            self._cancelled = False
            text = frame.text.strip()
            if not text:
                logger.info("LLM: Received empty TranscriptionFrame — ignoring")
                return

            text_lower = text.lower().strip()

            # Dictation is a text-entry mode. It must win before wake phrases,
            # voice commands, fast actions, duplicate tracking, or the
            # conversational agent can see the transcript.
            if self._is_dictation_transcript():
                logger.info("Dictation: Received transcription for text entry: '%s'", text[:100])
                cid = self.chat_manager.get_current_chat() if getattr(self, "chat_manager", None) else None
                if cid:
                    self._notify_transcription_progress(
                        int(cid), "", False, False, discard_live_preview=True
                    )
                if not getattr(self, '_dictation_one_shot', False) and self._check_dictation_commands(text_lower, text):
                    return
                self._last_dictation_transcription_mono = time.monotonic()
                text_to_type = self._process_dictation_text(text)
                if text_to_type:
                    await self._type_dictation_text(text_to_type)
                self._one_shot_dictation_armed = False
                release_pending = getattr(self, '_dictation_release_pending', False)
                self._dictation_release_pending = False
                if getattr(self, '_dictation_one_shot', False) or release_pending:
                    self._stop_dictation()
                return

            logger.info("LLM: Received transcription: '%s'", text[:100])
            # Drop duplicate frames (e.g. PTT / pipeline emits the same utterance twice in a row)
            _now = time.monotonic()
            if (
                text == getattr(self, '_last_ptt_transcription_text', None)
                and (_now - getattr(self, '_last_ptt_transcription_mono', 0.0)) < 2.5
            ):
                logger.warning(
                    "LLM: Duplicate TranscriptionFrame within 2.5s — ignoring (%.50s…)",
                    text,
                )
                if self._is_dictation_transcript() and getattr(self, '_dictation_one_shot', False):
                    cid = self.chat_manager.get_current_chat() if getattr(self, "chat_manager", None) else None
                    if cid:
                        self._notify_transcription_progress(
                            int(cid), "", False, False, discard_live_preview=True
                        )
                    self._stop_dictation()
                return
            self._last_ptt_transcription_text = text
            self._last_ptt_transcription_mono = _now

            # Agent PTT / hands-free utterance — never keep stale interrupt flags.
            self._one_shot_dictation_armed = False
            self._prepare_agent_voice_response()
            if not self._is_listening:
                if self._check_start_listening_command(text_lower):
                    return
                return

            # Live preview for web chat only when the transcript is going to the agent.
            cid = self.chat_manager.get_current_chat() if getattr(self, "chat_manager", None) else None
            if cid:
                self._notify_transcription_progress(int(cid), text, False, False)

            if self._check_dictation_commands(text_lower, text):
                return

            # Voice commands
            if await self._check_and_execute_voice_command(text_lower, direction):
                return

            # Duplicate message detection
            last_user_msg = self._messages[-1] if self._messages else None
            if last_user_msg and last_user_msg.get('role') == 'user' and last_user_msg.get('content') == text:
                logger.warning("Duplicate user message detected: '%s...' — skipping", text[:50])
                return

            # Fast action detection
            from distr.core.agent.services.llm.bulk_instruction import should_bypass_fast_action_detection
            has_clipboard_context = any(
                'CLIPBOARD CONTENT:' in msg.get('content', '')
                for msg in self._messages[-5:] if msg.get('role') == 'tool'
            )
            fast_action = None
            if not should_bypass_fast_action_detection(text):
                fast_action = detect_fast_action(text, has_clipboard_context)

            can_execute_directly = (
                fast_action is not None
                and fast_action.confidence >= 0.9
                and fast_action.action_type != ActionType.CONVERSATIONAL
                and (fast_action.action_type != ActionType.UNKNOWN or fast_action.tool_name)
            )
            if can_execute_directly:
                logger.info("⚡ FAST ACTION: %s (confidence %.2f)", fast_action.action_type.value, fast_action.confidence)
                self._processed_fast_actions.clear()
                current_chat_id = self._ensure_user_message_persisted(text)
                self._messages.append({"role": "user", "content": text})
                if await self._execute_fast_action(fast_action, current_chat_id):
                    return

            # Fast tool matcher fallback
            fast_match = fast_tool_matcher(text, self._tools, self._tools_dict)
            if fast_match:
                tool, args, confidence = fast_match
                logger.debug("Fast tool match: %s (confidence=%.1f%%)", tool.name, confidence * 100)
                current_chat_id = self._ensure_user_message_persisted(text)
                self._messages.append({"role": "user", "content": text})

                # Signal the UI that the agent is working
                if self.event_queue and current_chat_id:
                    self.event_queue.put(('typing_indicator_changed', {'show': True}), block=False)
                    self.event_queue.put(('chat_stream_started', {'chat_id': current_chat_id}), block=False)

                await self.push_frame(LLMFullResponseStartFrame(), direction)

                display_result = None
                try:
                    if 'text' not in args:
                        args['text'] = text
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(
                        None, lambda t=tool, a=args: t._run(**a)
                    )

                    if hasattr(tool, '_read_task') and tool._read_task:
                        try:
                            await tool._read_task
                        except Exception as e:
                            logger.error("Error awaiting read task: %s", e)
                        finally:
                            tool._read_task = None

                    from distr.core.agent.tool_audit import record_tool_execution
                    record_tool_execution(current_chat_id, tool.name, str(result), "completed", event_queue=self.event_queue)

                    # Convert raw tool result to a natural response for chat/TTS
                    display_result = "Done"
                    if result and isinstance(result, str):
                        r = result.strip()
                        if r.startswith("Error") or "error" in r.lower() or "failed" in r.lower():
                            display_result = r[:200]
                        elif r.startswith("{") and '"silent"' in r:
                            display_result = ""  # Silent tool (legacy JSON)
                        elif (
                            getattr(tool, "name", "") in _FAST_MATCH_TOOLS_SHOW_FULL_RESULT
                            and not r.startswith("{")
                        ):
                            display_result = r
                        # Otherwise just "Done" (mouse/clipboard-style tools)

                    if self.chat_manager and current_chat_id and display_result:
                        self.chat_manager.add_assistant_message(current_chat_id, display_result)
                    self._messages.append({"role": "assistant", "content": display_result or "Done"})

                    if (
                        self._tts_service
                        and self._speaker_enabled
                        and display_result
                        and not getattr(self, '_is_telegram_request', False)
                    ):
                        cleaned = clean_text_for_tts(display_result)
                        if cleaned:
                            await self.push_frame(TextFrame(text=cleaned))
                except Exception as e:
                    logger.error("Error running fast tool %s: %s", tool.name, e, exc_info=True)
                finally:
                    if (
                        getattr(self, '_is_telegram_request', False)
                        and self.event_queue
                        and display_result
                    ):
                        self._telegram_fallback_text = display_result
                        self._emit_telegram_response("", "")
                    await self.push_frame(LLMFullResponseEndFrame(), direction)
                    # Signal the UI that the agent is done
                    if self.event_queue and current_chat_id:
                        self.event_queue.put(('typing_indicator_changed', {'show': False}), block=False)
                        self.event_queue.put(('chat_stream_finished', {'chat_id': current_chat_id, 'response_text': ''}), block=False)
                    if getattr(self, '_is_telegram_request', False):
                        self._cleanup_telegram_flags()
                return

            # Normal LLM generation
            self._ensure_user_message_persisted(text)
            # Inject clipboard content if the user is asking about it
            user_content = text
            try:
                from distr.core.agent.services.llm.tool_routing import should_inject_clipboard, get_clipboard_content_fast
                if should_inject_clipboard(text):
                    clipboard = get_clipboard_content_fast()
                    if clipboard and clipboard.strip():
                        user_content = f"{text}\n\n[Clipboard content:\n{clipboard.strip()[:4000]}]"
            except Exception:
                pass
            self._messages.append({"role": "user", "content": user_content})

            if hasattr(self, '_generation_task') and self._generation_task and not self._generation_task.done():
                self._cancelled = True
                self._generation_task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(self._generation_task), timeout=3.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                self._cancelled = False

            if hasattr(self, '_generation_requested_at'):
                self._generation_requested_at = time.monotonic()
            self._generation_task = asyncio.create_task(self._generate_response())
            return

        # All other frames — pass through
        await self.push_frame(frame, direction)

    # ------------------------------------------------------------------ #
    #  send_welcome_message                                               #
    # ------------------------------------------------------------------ #

    async def send_welcome_message(self, agent_name: str = "Heart"):
        """Send a welcome message through the pipeline."""
        from distr.core.agent.libs import LLMFullResponseStartFrame, LLMFullResponseEndFrame, TextFrame

        if self._cancelled:
            return

        if not getattr(self, '_FrameProcessor__started', False):
            setattr(self, '_FrameProcessor__started', True)

        welcome_sentences = await self._build_welcome_sentences(agent_name)

        if self._cancelled:
            return

        full_message = " ".join(welcome_sentences)
        logger.info("AGENT_WELCOME_TEXT: %s", full_message)

        await self.push_frame(LLMFullResponseStartFrame(), getattr(self, '_pipeline_direction', None))

        try:
            if self._speaker_enabled and not self._cancelled:
                await self.push_frame(TextFrame(text=full_message), getattr(self, '_pipeline_direction', None))
                wait_time = max(1.0, len(welcome_sentences) * 0.5)
                await asyncio.sleep(wait_time)
        except asyncio.CancelledError:
            logger.warning("Welcome message task cancelled")
        finally:
            try:
                await self.push_frame(LLMFullResponseEndFrame(), getattr(self, '_pipeline_direction', None))
            except Exception as e:
                logger.error("Failed to push LLMFullResponseEndFrame in welcome: %s", e)

        self._messages.append({"role": "assistant", "content": full_message})
        self._apply_context_window()

    async def _build_welcome_sentences(self, agent_name: str) -> list:
        """Build a short spoken startup greeting.

        Startup should feel like the assistant has arrived, not like a transcript
        report. Keep previous-chat summaries out of TTS so we never speak
        meta text such as "The conversation..." or "the user said...".
        """
        include_interaction = not (hasattr(self, 'event_queue') and self.event_queue is not None)

        def _add_interaction(sentences):
            if include_interaction:
                if self._is_hands_free:
                    sentences.append(f"To get my attention, just use my name {agent_name}, or say 'Agent' or any wake word you prefer.")
                else:
                    sentences.append("To talk to me, just hold down on the oracle and then speak.")
            return sentences

        try:
            import getpass
            user_name = getpass.getuser().replace("_", " ").replace(".", " ").strip().title()
        except Exception:
            user_name = ""

        greeting = f"Hey {user_name}." if user_name and user_name.lower() not in ("user", "root") else "Hey."
        return _add_interaction([
            greeting,
            "I'm here.",
        ])

    async def _generate_welcome_summary(self, conversation_text: str, agent_name: str) -> str:
        """Generate a welcome summary. Default uses Ollama. Override for other providers."""
        summary_prompt = (
            "Summarize the latest context as a neutral continuity note for the assistant.\n\n"
            f"IMPORTANT:\n"
            f"- Do not include role tags or labels.\n"
            f"- Do not output text like \"User said\" or \"Assistant said\".\n"
            f"- Keep it short and objective, in first-person where needed.\n\n"
            f"Conversation history:\n{conversation_text}\n\n"
            f"Provide a brief, natural summary (max 2 sentences). Just the summary, no explanations:"
        )
        try:
            import ollama as _ollama
            client = _ollama.AsyncClient(timeout=120.0)
            response = await client.chat(
                model=self._model_name,
                messages=[{"role": "user", "content": summary_prompt}],
                options={"keep_alive": -1, "num_predict": 200},
            )
            return response.get('message', {}).get('content', '').strip()
        except Exception as e:
            logger.error("Error generating welcome summary: %s", e, exc_info=True)
            return ""
