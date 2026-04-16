"""
Dynamic tool loader — instantiates all LangChain tools from the TOOL_REGISTRY.

All tool imports are deferred to instantiation time via importlib to avoid
circular imports and keep the module lightweight.
"""

import importlib
import logging
import threading
from typing import List, Dict, Optional

from distr.core.agent.tools.base import BaseActionTool

logger = logging.getLogger(__name__)

# Module-level tool instance cache — populated by warm_tool_cache(),
# keyed by tool.name (the string name attribute on each BaseTool instance).
_tool_cache: dict[str, "BaseActionTool"] = {}
_cache_lock: threading.Lock = threading.Lock()


def get_cached_tool(name: str):
    """Return a cached tool instance by name, or None if not cached."""
    return _tool_cache.get(name)


def _get_tool_definitions(
    chat_manager=None,
    llm_service=None,
    tts_service=None,
    llm_model=None,
    event_queue=None,
    command_queue=None,
    confirmation_results_dict=None,
) -> list:
    """Return the canonical list of (tool_name, kwargs) tuples.

    Shared by both ``load_tools`` and ``warm_tool_cache`` so the tool set
    is defined in exactly one place.
    """
    defs = [
        # Smart Open - Handles URLs, applications, and files intelligently
        ("SmartOpenTool", dict(chat_manager=chat_manager)),
        # Navigation and Window Management
        ("OpenWindowTool", dict(chat_manager=chat_manager)),
        ("OpenFileMenuTool", dict(chat_manager=chat_manager)),
        ("OracleControlTool", dict(chat_manager=chat_manager, event_queue=event_queue)),
        ("ModeControlTool", dict(chat_manager=chat_manager, event_queue=event_queue)),
        ("ShortcutTool", dict(chat_manager=chat_manager)),
        # Text Editing
        ("TextEditingTool", dict(chat_manager=chat_manager)),
        # Caret Movement
        ("CaretMovementTool", dict(chat_manager=chat_manager)),
        # Mouse Control
        ("MouseMovementTool", dict(chat_manager=chat_manager)),
        ("MouseActionsTool", dict(chat_manager=chat_manager)),
        # Media Control
        ("MediaControlTool", dict(chat_manager=chat_manager)),
        # Function Keys
        ("FunctionKeyTool", dict(chat_manager=chat_manager)),
        # Special Keys
        ("SpecialKeyTool", dict(chat_manager=chat_manager)),
        # Clipboard Actions
        ("ClipboardActionTool", dict(chat_manager=chat_manager, llm_service=llm_service)),
        # Save Audio
        ("SaveAudioTool", dict(tts_service=tts_service)),
        # Exit Application
        ("ExitAppTool", dict(llm_service=llm_service, event_queue=event_queue)),
        ("RestartAppTool", dict(llm_service=llm_service, event_queue=event_queue)),
        # Rework Clipboard
        ("ReworkClipboardTool", dict(llm_model=llm_model or "qwen3:8b")),
        # Summarize Clipboard
        ("SummarizeClipboardTool", dict(llm_model=llm_model or "qwen3:8b", llm_service=llm_service)),
        # Skills tools
        ("FindSkillTool", {}),
        ("PushSkillTool", {}),
        # Create Action
        ("CreateActionTool", dict(event_queue=event_queue)),
        # Workflow Builder (AutoWorkflow) tools
        ("ListWorkflowsTool", {}),
        ("GetWorkflowTool", {}),
        ("RunWorkflowTool", {}),
        ("CancelWorkflowRunTool", {}),
        ("GetProjectStatusTool", {}),
        ("AddWorkflowStepTool", {}),
        ("UpdateWorkflowStepTool", {}),
        ("GenerateWorkflowTool", {}),
        ("ResetWorkflowTool", {}),
        ("ClearWorkflowHistoryTool", {}),
        ("ContinueWorkflowTool", {}),
        # Play Action
        ("PlayActionTool", dict(event_queue=event_queue)),
        ("ListActionsTool", {}),
        # Stop Action
        ("StopActionTool", dict(event_queue=event_queue)),
        # Start Recording
        ("StartRecordingTool", dict(event_queue=event_queue)),
        # Stop Recording
        ("StopRecordingTool", dict(event_queue=event_queue)),
        # Oracle Globe Control
        ("OracleGlobeTool", dict(chat_manager=chat_manager, event_queue=event_queue)),
        # New Chat
        ("NewChatTool", dict(chat_manager=chat_manager)),
        # Open Page (web UI navigation)
        ("OpenPageTool", dict(chat_manager=chat_manager)),
        # Clear Chat
        ("ClearChatTool", dict(chat_manager=chat_manager)),
        # Web Search
        ("WebSearchTool", dict(llm_service=llm_service)),
        # Web Fetch
        ("WebFetchTool", dict(llm_service=llm_service)),
        # Git Operations
        ("GitOperationsTool", {}),
        # Screenshot Analyzer
        ("ScreenshotAnalyzerTool", dict(llm_service=llm_service)),
        # Vision Analyzer
        ("VisionAnalyzerTool", dict(llm_service=llm_service)),
        # Open File
        ("OpenFileTool", {}),
        # Execute Code
        ("ExecuteCodeTool", dict(event_queue=event_queue, command_queue=command_queue, confirmation_results_dict=confirmation_results_dict)),
        # Create Cursor Ticket (legacy — kept for backward compat)
        ("CreateCursorTicketTool", dict(llm_service=llm_service, llm_model=llm_model or "qwen3:8b", chat_manager=chat_manager)),
        # Ticket Board Ticket (primary ticket tool)
        ("KanbanTicketTool", dict(chat_manager=chat_manager, llm_service=llm_service, event_queue=event_queue)),
        # Playwright Browser Automation
        ("PlaywrightTool", dict(event_queue=event_queue, command_queue=command_queue, confirmation_results_dict=confirmation_results_dict)),
        # Pi Agent — AI coding agent for project tasks
        ("PiAgentTool", dict(event_queue=event_queue, chat_manager=chat_manager)),
        # Terminal Overview — query project terminal state
        ("TerminalOverviewTool", dict(event_queue=event_queue, chat_manager=chat_manager)),
        # Document Extractor
        ("DocumentExtractorTool", {}),
        # System Information
        ("SystemInfoTool", dict(chat_manager=chat_manager)),
        ("ImageGeneratorTool", {}),
        # Wake Up
        ("WakeUpTool", {}),
        # Speak on Desktop (remote intercom from Telegram)
        ("SpeakOnDesktopTool", dict(event_queue=event_queue)),
        # Send File to Telegram
        ("SendFileToTelegramTool", dict(chat_manager=chat_manager, event_queue=event_queue)),
        # Send Voice Note to Telegram
        ("SendVoiceNoteToTelegramTool", dict(event_queue=event_queue)),
        # PDF Page Extractor
        ("PDFPageExtractorTool", {}),
        # Audio Transcriber
        ("AudioTranscriberTool", dict(chat_manager=chat_manager)),
        # Video Transcriber
        ("VideoTranscriberTool", {}),
        # Transcription Doctor
        ("TranscriptionDoctorTool", {}),
        # File Converter
        ("FileConverterTool", dict(chat_manager=chat_manager)),
        # Type Text
        ("TypeTextTool", dict(chat_manager=chat_manager, llm_service=llm_service)),
        # File Operations
        ("FileOperationsTool", dict(event_queue=event_queue, command_queue=command_queue, confirmation_results_dict=confirmation_results_dict)),
        # Index Folder
        ("IndexFolderTool", dict(chat_manager=chat_manager, event_queue=event_queue, command_queue=command_queue, confirmation_results_dict=confirmation_results_dict)),
        # Convert Document (MD → PDF/DOCX/Google Doc)
        ("ConvertDocumentTool", {}),
        # Google Workspace
        ("GoogleWorkspaceTool", {}),
        # Markdown to Google Doc
        ("MarkdownToGoogleDocTool", {}),
        # Upload DOC/DOCX to Google Doc
        ("UploadDocToGoogleTool", {}),
        # Project Management Tools
        ("ListProjectsTool", dict(event_queue=event_queue)),
        ("GetProjectDetailsTool", dict(event_queue=event_queue)),
        ("SwitchProjectTool", dict(event_queue=event_queue)),
        ("QueryCurrentProjectTool", dict(event_queue=event_queue)),
        ("DeactivateProjectTool", dict(event_queue=event_queue)),
        ("CreateProjectFromFolderTool", dict(event_queue=event_queue)),
        ("AddFilesToProjectTool", dict(event_queue=event_queue)),
        ("CreateProjectTicketTool", dict(event_queue=event_queue)),
        ("OpenProjectTool", dict(event_queue=event_queue)),
        ("StartProjectTool", dict(event_queue=event_queue)),
        ("OpenAndStartProjectTool", dict(event_queue=event_queue)),
        # Meta-tool: RequestToolTool (callback wired separately in core_mixin.py)
        ("RequestToolTool", {}),
    ]

    return defs


def warm_tool_cache(
    chat_manager=None,
    llm_service=None,
    tts_service=None,
    llm_model=None,
    event_queue=None,
    command_queue=None,
    confirmation_results_dict=None,
) -> None:
    """Instantiate all tools once and populate ``_tool_cache``.

    Called at startup so that retrieval-based loading can reuse cached
    instances instead of re-instantiating tools on every LLM call.

    After all tools are cached the embedding index is built in a
    background thread via ``build_index_async``.

    The ``RequestToolTool`` callback is NOT wired here — that is done
    in ``core_mixin.py`` (task 8.3).
    """
    tool_definitions = _get_tool_definitions(
        chat_manager=chat_manager,
        llm_service=llm_service,
        tts_service=tts_service,
        llm_model=llm_model,
        event_queue=event_queue,
        command_queue=command_queue,
        confirmation_results_dict=confirmation_results_dict,
    )

    with _cache_lock:
        for tool_name, kwargs in tool_definitions:
            try:
                tool_class = _get_tool_class(tool_name)
                tool = tool_class(**kwargs)
                _tool_cache[tool.name] = tool
                logger.debug("Cached %s: %s", tool_name, tool.name)
            except Exception as e:
                logger.error("FAILED to cache %s: %s", tool_name, e, exc_info=True)
                # Continue — do not raise

        # Accessibility tree tools (sidecar-powered, optional)
        _accessibility_tools = [
            ("GetWindowTreeTool",  ("input.accessibility_tree", "GetWindowTreeTool"),  {}),
            ("FindElementTool",    ("input.accessibility_tree", "FindElementTool"),    {}),
            ("MoveToElementTool",  ("input.accessibility_tree", "MoveToElementTool"),  {}),
            ("ClickElementTool",   ("input.accessibility_tree", "ClickElementTool"),   {}),
        ]
        for tool_name, (submodule, class_name), kwargs in _accessibility_tools:
            try:
                mod = importlib.import_module(f"{_BASE_PACKAGE}.{submodule}")
                cls = getattr(mod, class_name)
                tool = cls(**kwargs)
                _tool_cache[tool.name] = tool
                logger.debug("Cached accessibility tool: %s", tool_name)
            except Exception as e:
                logger.debug("Skipped %s (sidecar not available): %s", tool_name, e)

        # Extended sidecar tools (python executor, drag, scroll, wait)
        # Note: ScreenAnalyzeTool removed — screenshot_analyzer handles all screen vision tasks
        # and integrates with the mouse/click pipeline for coordinate-based actions.
        _sidecar_extended_tools = [
            ("RunPythonTool",        ("input.sidecar_tools", "RunPythonTool"),        {}),
            ("DragToTool",           ("input.sidecar_tools", "DragToTool"),           {}),
            ("ScrollTool",           ("input.sidecar_tools", "ScrollTool"),           {}),
            ("WaitForElementTool",   ("input.sidecar_tools", "WaitForElementTool"),   {}),
        ]
        for tool_name, (submodule, class_name), kwargs in _sidecar_extended_tools:
            try:
                mod = importlib.import_module(f"{_BASE_PACKAGE}.{submodule}")
                cls = getattr(mod, class_name)
                tool = cls(**kwargs)
                _tool_cache[tool.name] = tool
                logger.debug("Cached sidecar tool: %s", tool_name)
            except Exception as e:
                logger.debug("Skipped %s (sidecar not available): %s", tool_name, e)

    # Trigger background embedding index build
    from distr.core.agent.tool_retriever import build_index_async
    build_index_async(list(_tool_cache.values()))
    logger.info("warm_tool_cache complete — %d tools cached, index build started", len(_tool_cache))


# Registry: (module_path, class_name) for each tool
# module_path is relative to distr.core.agent.tools
TOOL_REGISTRY = {
    # input/
    "SmartOpenTool":           ("input.navigation", "SmartOpenTool"),
    "OpenWindowTool":          ("input.navigation", "OpenWindowTool"),
    "OpenFileMenuTool":        ("input.navigation", "OpenFileMenuTool"),
    "OracleControlTool":       ("input.navigation", "OracleControlTool"),
    "ModeControlTool":         ("input.navigation", "ModeControlTool"),
    "ShortcutTool":            ("input.navigation", "ShortcutTool"),
    "WindowManagementTool":    ("input.window_management", "WindowManagementTool"),
    "TextEditingTool":         ("input.text_editing", "TextEditingTool"),
    "CaretMovementTool":       ("input.caret_movement", "CaretMovementTool"),
    "MouseMovementTool":       ("input.mouse_movement", "MouseMovementTool"),
    "MouseActionsTool":        ("input.mouse_actions", "MouseActionsTool"),
    "FunctionKeyTool":         ("input.function_keys", "FunctionKeyTool"),
    "SpecialKeyTool":          ("input.special_keys", "SpecialKeyTool"),
    "TypeTextTool":            ("input.type_text", "TypeTextTool"),
    # clipboard/
    "ClipboardActionTool":     ("clipboard.clipboard_actions", "ClipboardActionTool"),
    "ReworkClipboardTool":     ("clipboard.rework_clipboard", "ReworkClipboardTool"),
    "SummarizeClipboardTool":  ("clipboard.summarize_clipboard", "SummarizeClipboardTool"),
    # media/
    "MediaControlTool":        ("media.media_control", "MediaControlTool"),
    "SaveAudioTool":           ("media.save_audio", "SaveAudioTool"),
    "AudioTranscriberTool":    ("media.audio_transcriber", "AudioTranscriberTool"),
    "VideoTranscriberTool":    ("media.video_transcriber", "VideoTranscriberTool"),
    "TranscriptionDoctorTool": ("media.transcription_doctor", "TranscriptionDoctorTool"),
    "FileConverterTool":       ("media.file_converter", "FileConverterTool"),
    # files/
    "FileOperationsTool":      ("files.file_operations", "FileOperationsTool"),
    "OpenFileTool":            ("files.open_file", "OpenFileTool"),
    "DocumentExtractorTool":   ("files.document_extractor", "DocumentExtractorTool"),
    "PDFPageExtractorTool":    ("files.pdf_page_extractor", "PDFPageExtractorTool"),
    "IndexFolderTool":         ("files.index_folder", "IndexFolderTool"),
    "ConvertDocumentTool":     ("files.convert_document", "ConvertDocumentTool"),
    # vision/
    "ScreenshotAnalyzerTool":  ("vision.screenshot_analyzer", "ScreenshotAnalyzerTool"),
    "VisionAnalyzerTool":      ("vision.vision_analyzer", "VisionAnalyzerTool"),
    "ImageGeneratorTool":      ("vision.image_generator", "ImageGeneratorTool"),
    # web/
    "WebSearchTool":           ("web.web_search", "WebSearchTool"),
    "WebFetchTool":            ("web.web_fetch", "WebFetchTool"),
    # actions/
    "CreateActionTool":        ("actions.create_action", "CreateActionTool"),
    # workflow builder (AutoWorkflow) tools
    "ListWorkflowsTool":          ("step_runner.workflow_tools", "ListWorkflowsTool"),
    "GetWorkflowTool":            ("step_runner.workflow_tools", "GetWorkflowTool"),
    "RunWorkflowTool":            ("step_runner.workflow_tools", "RunWorkflowTool"),
    "CancelWorkflowRunTool":      ("step_runner.workflow_tools", "CancelWorkflowRunTool"),
    "GetProjectStatusTool":       ("step_runner.workflow_tools", "GetProjectStatusTool"),
    "AddWorkflowStepTool":        ("step_runner.workflow_tools", "AddWorkflowStepTool"),
    "UpdateWorkflowStepTool":     ("step_runner.workflow_tools", "UpdateWorkflowStepTool"),
    "GenerateWorkflowTool":       ("step_runner.workflow_tools", "GenerateWorkflowTool"),
    "ResetWorkflowTool":          ("step_runner.workflow_tools", "ResetWorkflowTool"),
    "ClearWorkflowHistoryTool":   ("step_runner.workflow_tools", "ClearWorkflowHistoryTool"),
    "ContinueWorkflowTool":       ("step_runner.workflow_tools", "ContinueWorkflowTool"),
    "StartRecordingTool":      ("actions.start_recording", "StartRecordingTool"),
    "StopRecordingTool":       ("actions.stop_recording", "StopRecordingTool"),
    "PlayActionTool":          ("actions.play_action", "PlayActionTool"),
    "StopActionTool":          ("actions.stop_action", "StopActionTool"),
    "ListActionsTool":         ("actions.list_actions", "ListActionsTool"),
    "FindSkillTool":          ("skills.find_skill", "FindSkillTool"),
    "PushSkillTool":          ("skills.push_skill", "PushSkillTool"),
    # chat/
    "NewChatTool":             ("chat.new_chat", "NewChatTool"),
    "ClearChatTool":           ("chat.clear_chat", "ClearChatTool"),
    "OracleGlobeTool":         ("chat.oracle_globe", "OracleGlobeTool"),
    "OpenPageTool":            ("chat.open_page", "OpenPageTool"),
    # system/
    "SystemInfoTool":          ("system.system_info", "SystemInfoTool"),
    "ExitAppTool":             ("system.exit_app", "ExitAppTool"),
    "RestartAppTool":          ("system.restart_app", "RestartAppTool"),
    "WakeUpTool":              ("system.wake_up", "WakeUpTool"),
    "SpeakOnDesktopTool":      ("system.speak_on_desktop", "SpeakOnDesktopTool"),
    "ExecuteCodeTool":         ("system.execute_code", "ExecuteCodeTool"),
    "ListProjectsTool":        ("system.project_tools", "ListProjectsTool"),
    "GetProjectDetailsTool":   ("system.project_tools", "GetProjectDetailsTool"),
    "SwitchProjectTool":       ("system.project_tools", "SwitchProjectTool"),
    "QueryCurrentProjectTool": ("system.project_tools", "QueryCurrentProjectTool"),
    "DeactivateProjectTool":   ("system.project_tools", "DeactivateProjectTool"),
    "CreateProjectFromFolderTool": ("system.project_tools", "CreateProjectFromFolderTool"),
    "AddFilesToProjectTool":   ("system.project_tools", "AddFilesToProjectTool"),
    "CreateProjectTicketTool": ("system.project_tools", "CreateProjectTicketTool"),
    "OpenProjectTool":         ("system.project_tools", "OpenProjectTool"),
    "StartProjectTool":        ("system.project_tools", "StartProjectTool"),
    "OpenAndStartProjectTool": ("system.project_tools", "OpenAndStartProjectTool"),
    # integrations/
    "GoogleWorkspaceTool":     ("integrations.google_workspace_tool", "GoogleWorkspaceTool"),
    "MarkdownToGoogleDocTool": ("integrations.markdown_to_google_doc", "MarkdownToGoogleDocTool"),
    "UploadDocToGoogleTool":   ("integrations.upload_doc_to_google", "UploadDocToGoogleTool"),
    "SendFileToTelegramTool":  ("integrations.send_file_to_telegram", "SendFileToTelegramTool"),
    "SendVoiceNoteToTelegramTool": ("integrations.send_voice_note_to_telegram", "SendVoiceNoteToTelegramTool"),
    "GitOperationsTool":       ("integrations.git_operations", "GitOperationsTool"),
    "CreateCursorTicketTool":  ("integrations.create_cursor_ticket", "CreateCursorTicketTool"),
    "KanbanTicketTool":        ("integrations.kanban_ticket", "KanbanTicketTool"),
    "PlaywrightTool":          ("integrations.playwright_tool", "PlaywrightTool"),
    "PiAgentTool":             ("integrations.pi_agent", "PiAgentTool"),
    # meta/
    "RequestToolTool":         ("request_tool", "RequestToolTool"),
    "TerminalOverviewTool":     ("integrations.terminal_overview", "TerminalOverviewTool"),
}

_BASE_PACKAGE = "distr.core.agent.tools"

# Use-case descriptions for semantic retrieval — one sentence per tool.
# Keys match TOOL_REGISTRY class names; values are verb-phrase sentences
# describing what the tool does and when to use it.
TOOL_DESCRIPTIONS: dict[str, str] = {
    # input/
    "SmartOpenTool": "Open a URL in the browser, launch a desktop application by name, or open a file with its default app. Use for: open Chrome, open Spotify, open a website, launch an app.",
    "OpenWindowTool": "Switch focus to an already-running application window. Use for: switch to Chrome, bring up Finder, focus on VS Code.",
    "OpenFileMenuTool": "Open the File menu in the currently active application window.",
    "OracleControlTool": "Show, hide, minimize, or restore the Oracle assistant overlay on the desktop.",
    "ModeControlTool": "Switch the assistant between different interaction modes such as voice, chat, or silent.",
    "ShortcutTool": "Execute a keyboard shortcut like Cmd+S, Ctrl+Z, or any multi-key combination in the active app.",
    "TextEditingTool": "Perform text editing operations such as copy, paste, cut, select all, undo, redo, or delete.",
    "CaretMovementTool": "Move the text cursor using arrow keys, page up/down, home, or end in the active editor.",
    "MouseMovementTool": "Move the mouse pointer to a specific position, screen corner, or relative direction on the desktop.",
    "MouseActionsTool": "Perform mouse clicks (left, right, double) and scroll up or down at the current cursor position.",
    "FunctionKeyTool": "Press a function key from F1 through F12 in the active application.",
    "SpecialKeyTool": "Press any keyboard key: Enter, Space, Tab, Escape, arrows, F1-F24, letters, numbers, modifiers, and more.",
    "TypeTextTool": "Type out text character by character into the currently focused input field or editor.",
    # input/accessibility_tree
    "GetWindowTreeTool": "Get the accessibility tree of the frontmost window to inspect UI elements, buttons, text fields, and their hierarchy.",
    "FindElementTool": "Find a specific UI element in the accessibility tree by role, title, or description.",
    "MoveToElementTool": "Move the mouse cursor to a specific UI element identified by its accessibility tree ID.",
    "ClickElementTool": "Click a specific UI element identified by its accessibility tree ID.",
    # clipboard/
    "ClipboardActionTool": "Explain, elaborate on, or read aloud the currently selected text or clipboard contents using the LLM.",
    "ReworkClipboardTool": "Rewrite and improve the selected text or clipboard contents using an LLM and paste the result back.",
    "SummarizeClipboardTool": "Summarize the selected text or clipboard contents using an LLM and optionally paste or read the summary.",
    # media/
    "MediaControlTool": "Control media playback: play, pause, stop, next track, previous track, skip song, adjust volume up or down, mute, unmute, or refresh the browser page.",
    "SaveAudioTool": "Convert selected text or clipboard contents to speech and save the audio as a WAV file on the desktop.",
    "AudioTranscriberTool": "Transcribe audio files like MP3, M4A, or WAV to text using AssemblyAI or Whisper.",
    "VideoTranscriberTool": "Extract audio from a video file and transcribe it to text with speaker diarization support.",
    "TranscriptionDoctorTool": "Run a health check on transcription backends to see which engines and models are available.",
    "FileConverterTool": "Convert files between formats including audio, video-to-audio, image format conversion, and audio-to-text transcription.",
    # files/
    "FileOperationsTool": "Perform file system operations: list, create, write, read, delete, copy, move, or rename files and directories on the desktop, documents, downloads, or any folder path.",
    "OpenFileTool": "Find a file by name across common folders using fuzzy matching and open it with the default application.",
    "DocumentExtractorTool": "Extract and read text content from PDFs, Word documents, Excel spreadsheets, ZIP/RAR archives, and plain text files.",
    "PDFPageExtractorTool": "Find a PDF by name and extract text from a specific page number.",
    "IndexFolderTool": "Index a dropped folder into the RAG system for semantic search and querying of its contents.",
    "ConvertDocumentTool": "Convert a Markdown file to PDF, DOCX, or Google Doc with proper formatting, tables, and Mermaid diagrams.",
    # vision/
    "ScreenshotAnalyzerTool": "Capture a screenshot of the desktop or a specific screen. THREE MODES: 1) Analysis — capture and analyze with vision LLM. 2) Direct send — capture and send to Telegram. 3) Capture-only — capture and return file path for chaining to other tools (save to folder, attach to ticket, send to pi agent, send to Telegram). Use for: take a screenshot, what do you see, screenshot and save, screenshot and attach to ticket, screenshot and send to pi, push to CLI.",
    "VisionAnalyzerTool": "Analyze a dropped or specified image file using a vision-enabled LLM to describe its contents.",
    "ImageGeneratorTool": "Generate an image from a text description using an image generation LLM and save it to disk.",
    # web/
    "WebSearchTool": "Search the web, Google, or the internet for current information, news, facts, or answers.",
    "WebFetchTool": "Fetch, read, or extract the text content of a web page given its URL.",
    # actions/
    "CreateActionTool": "Record a new reusable macro action that can be replayed later to automate repetitive tasks.",
    # workflow builder (AutoWorkflow) tools
    "ListWorkflowsTool": "List all saved workflows with optional filtering by status or search term.",
    "GetWorkflowTool": "Retrieve the full details, steps, and run history of a specific workflow by its ID.",
    "RunWorkflowTool": "Start a new run of a workflow, executing its steps in order.",
    "CancelWorkflowRunTool": "Cancel an in-progress workflow run by its run ID.",
    "GetProjectStatusTool": "Get the current status, recent activity, and health summary of a project.",
    "AddWorkflowStepTool": "Add a new step to an existing workflow at a specified position.",
    "UpdateWorkflowStepTool": "Update the configuration of an existing workflow step.",
    "GenerateWorkflowTool": "Auto-generate a complete workflow from a natural language description of the desired process.",
    "ResetWorkflowTool": "Reset a workflow to its initial state, clearing all step statuses and run data.",
    "ClearWorkflowHistoryTool": "Clear the run history of a workflow while keeping its step definitions intact.",
    "ContinueWorkflowTool": "Resume a paused or waiting workflow run, optionally providing user input.",
    "StartRecordingTool": "Start recording user interactions to create a replayable macro action.",
    "StopRecordingTool": "Stop the current macro recording session and save the recorded action.",
    "PlayActionTool": "Play back a previously recorded macro action to repeat a sequence of interactions.",
    "StopActionTool": "Stop a currently playing macro action mid-execution.",
    "ListActionsTool": "List all saved macro actions available for playback.",
    "FindSkillTool": "Search and find skills by capability, or list all skills. Use when: find a skill for X, suggest a skill, what skills do we have, is there a skill for Docker/testing/security. No query = list all; with query = relevance-scored search.",
    "PushSkillTool": "Push a skill to a project's CLI (pi, Claude, Cursor, Gemini, Codex). For pi, copies SKILL.md into .pi/skills/ so it's auto-loaded by the agent. Use when the user asks: push skill, add skill to my project, install skill for pi, make skill available.",
    # chat/
    "NewChatTool": "Start a new conversation, new chat, or fresh chat session.",
    "ClearChatTool": "Clear, wipe, or delete all messages from the current chat conversation.",
    "OracleGlobeTool": "Control the Oracle globe overlay appearance, animations, and visual state on the desktop.",
    "OpenPageTool": "Open a specific page in the DecisionsAI app: chat, ticket boards, board, settings, preferences, actions, skills, projects, workflows, step runner, docs, activity log, audio, models, skins, or about. Use for: open ticket boards, go to settings, show the board, open chat page, open skills.",
    # system/
    "SystemInfoTool": "Retrieve system information such as OS version, CPU, memory, disk usage, and running processes.",
    "ExitAppTool": "Quit and close the DecisionsAI desktop application.",
    "RestartAppTool": "Restart the DecisionsAI desktop application to apply updates or recover from errors.",
    "WakeUpTool": "Wake the computer from sleep or activate the display when the screen is off.",
    "SpeakOnDesktopTool": "Speak a text message aloud on the desktop using text-to-speech, used as a remote intercom from Telegram.",
    "ExecuteCodeTool": "Write and run Python or shell code, scripts, or commands to perform tasks, calculations, or automation.",
    "ListProjectsTool": "List all registered projects with their names, paths, and active status.",
    "GetProjectDetailsTool": "Get detailed information about a specific project including its files, settings, and configuration.",
    "SwitchProjectTool": "Switch the active project context to a different registered project.",
    "QueryCurrentProjectTool": "Query information about the currently active project and its configuration.",
    "DeactivateProjectTool": "Deactivate the currently active project, returning to the default context.",
    "CreateProjectFromFolderTool": "Register a new project from an existing folder on disk.",
    "AddFilesToProjectTool": "Add files or directories to an existing project's tracked file set.",
    "CreateProjectTicketTool": "Create a new task ticket within the current project's Ticket Board.",
    "OpenProjectTool": "Open a project's folder in the system file manager or IDE.",
    "StartProjectTool": "Start a project by running its configured start command or script.",
    "OpenAndStartProjectTool": "Open a project folder and immediately run its start command in one step.",
    # integrations/
    "GoogleWorkspaceTool": "Interact with Google Workspace services to manage Gmail, Google Calendar, Google Drive, and Google Docs.",
    "MarkdownToGoogleDocTool": "Convert Markdown content from the clipboard into a formatted Google Doc and open it in the browser.",
    "UploadDocToGoogleTool": "Upload a local DOC or DOCX file to Google Drive as a Google Doc.",
    "SendFileToTelegramTool": "Send a file or image to a Telegram user or group chat.",
    "SendVoiceNoteToTelegramTool": "Record or convert text to a voice note and send it via Telegram.",
    "GitOperationsTool": "Perform Git operations like clone, pull, push, commit, diff, log, and browse GitHub repositories.",
    "CreateCursorTicketTool": "Create a development task ticket from clipboard content or conversation context for the Cursor IDE.",
    "KanbanTicketTool": "Create, update, list, move, or manage tickets, tasks, or cards on the project ticket board.",
    "PlaywrightTool": "Run browser automation scripts using Playwright to interact with web pages, fill forms, and scrape data.",
    "PiAgentTool": "Delegate coding and query tasks to the pi AI coding agent. Sends the instruction to pi, waits for the result, and returns it. Use for any project-level code, query, or terminal task. Can also send screenshot file paths for pi to read and analyze — include the full file path in the instruction. Use when the user says: send screenshot to pi, push to CLI, screenshot and send to pi, analyze this screenshot in context of my project.",
    "TerminalOverviewTool": "Get the current state of a project's terminal session — last command and output. Use when the user asks about terminal activity.",
    # meta/
    "RequestToolTool": "Request a tool that is not currently available in your active tool set when you need a capability you don't have access to.",
    # sidecar (screen intelligence, Python execution, physical interaction)

    "RunPythonTool": "Execute arbitrary Python code on the user's machine for complex tasks without dedicated tools: batch file operations, image processing, data transformation, web scraping, GUI automation. Optional pip install of packages before execution.",
    "DragToTool": "Drag from one position to another using element IDs from get_window_tree or raw screen coordinates. Supports element-to-element, element-to-coordinate, and coordinate-to-coordinate dragging.",
    "ScrollTool": "Scroll at the current mouse position or at specified coordinates. Supports up, down, left, right directions with configurable scroll amount.",
    "WaitForElementTool": "Wait until a UI element appears in the accessibility tree, polling repeatedly until found or timeout. Find by name, control_type, or app_name."
}


def _get_tool_class(name: str):
    """Dynamically import and return a tool class by registry name."""
    submodule, class_name = TOOL_REGISTRY[name]
    module = importlib.import_module(f"{_BASE_PACKAGE}.{submodule}")
    return getattr(module, class_name)


def load_tools(chat_manager=None, filter_methods: Optional[List[str]] = None, use_navigation_tools: bool = True, llm_service=None, tts_service=None, llm_model=None, event_queue=None, command_queue=None, confirmation_results_dict=None, user_message: str | None = None, model_name: str | None = None) -> List:
    """
    Load all tools from actions.config.json and specialized navigation tools.

    When *user_message* is provided and the tool cache is populated, the
    function delegates to the semantic Tool_Retriever to select only the
    most relevant tools.  Otherwise it falls back to instantiating all
    tools (backward-compatible behaviour).

    Args:
        chat_manager: Optional chat manager instance to pass to tools
        filter_methods: Optional list of method prefixes to filter (e.g., ['windows.', 'shortcuts.'])
        use_navigation_tools: If True, include specialized navigation tools for better natural language handling
        user_message: Optional user message for semantic retrieval-based tool selection
        model_name: Optional model name for tier classification

    Returns:
        List of tool instances (mix of BaseActionTool and specialized tools)
    """
    # --- Retrieval path: when user_message is provided and cache is warm ---
    if user_message and _tool_cache:
        from distr.core.agent.tool_retriever import get_tool_retriever

        names = get_tool_retriever().retrieve(user_message, model_name or "")
        if names is None:
            # Kill switch active or index not ready — fall back to all cached tools
            return list(_tool_cache.values())
        # Resolve retrieved names to cached instances
        resolved: List = []
        for name in names:
            tool = get_cached_tool(name)
            if tool is not None:
                resolved.append(tool)
            else:
                logger.error("Retriever returned tool name %r but it is not in the cache — skipping", name)
        return resolved

    tools = []
    navigation_tools_count = 0

    # Add specialized tools first (these handle natural language better)
    logger.info(f"load_tools called with use_navigation_tools={use_navigation_tools}")
    if use_navigation_tools:
        logger.info("Loading specialized tools...")
        specialized_tools = []
        tool_definitions = _get_tool_definitions(
            chat_manager=chat_manager,
            llm_service=llm_service,
            tts_service=tts_service,
            llm_model=llm_model,
            event_queue=event_queue,
            command_queue=command_queue,
            confirmation_results_dict=confirmation_results_dict,
        )

        # Load each tool individually to catch which one fails
        for tool_name, kwargs in tool_definitions:
            try:
                tool_class = _get_tool_class(tool_name)
                tool = tool_class(**kwargs)
                specialized_tools.append(tool)
                logger.debug(f"Loaded {tool_name}: {tool.name}")
            except Exception as e:
                logger.error(f"FAILED to load {tool_name}: {e}", exc_info=True)
                import traceback
                traceback.print_exc()
                # Continue loading other tools even if one fails

        if specialized_tools:
            tools.extend(specialized_tools)
            navigation_tools_count = len(specialized_tools)
            logger.info(f"Loaded {navigation_tools_count} specialized tools (out of {len(tool_definitions)} attempted)")
            tool_names = [t.name for t in specialized_tools]
            logger.info(f"Specialized tool names: {tool_names}")
            if "screenshot_analyzer" in tool_names:
                logger.info("screenshot_analyzer successfully loaded in specialized tools")
            else:
                logger.error(f"screenshot_analyzer NOT in specialized tools! Available: {tool_names}")
        else:
            logger.error("NO specialized tools loaded! All tool loading failed!")
    else:
        logger.warning(f"use_navigation_tools is False - skipping specialized tools!")

    # Accessibility tree tools (sidecar-powered, optional — only if sidecar is running)
    accessibility_tools = [
        ("GetWindowTreeTool",  ("input.accessibility_tree", "GetWindowTreeTool"),  {}),
        ("FindElementTool",    ("input.accessibility_tree", "FindElementTool"),    {}),
        ("MoveToElementTool",  ("input.accessibility_tree", "MoveToElementTool"),  {}),
        ("ClickElementTool",   ("input.accessibility_tree", "ClickElementTool"),   {}),
    ]
    for tool_name, (submodule, class_name), kwargs in accessibility_tools:
        try:
            import importlib as _il
            mod = _il.import_module(f"{_BASE_PACKAGE}.{submodule}")
            cls = getattr(mod, class_name)
            tools.append(cls(**kwargs))
            logger.debug("Loaded accessibility tool: %s", tool_name)
        except Exception as e:
            logger.debug("Skipped %s (sidecar not available): %s", tool_name, e)

    # Extended sidecar tools (screen intelligence, python executor, drag, scroll, wait)
    sidecar_extended_tools = [

        ("RunPythonTool",        ("input.sidecar_tools", "RunPythonTool"),        {}),
        ("DragToTool",           ("input.sidecar_tools", "DragToTool"),           {}),
        ("ScrollTool",           ("input.sidecar_tools", "ScrollTool"),           {}),
        ("WaitForElementTool",   ("input.sidecar_tools", "WaitForElementTool"),   {}),
    ]
    for tool_name, (submodule, class_name), kwargs in sidecar_extended_tools:
        try:
            import importlib as _il
            mod = _il.import_module(f"{_BASE_PACKAGE}.{submodule}")
            cls = getattr(mod, class_name)
            tools.append(cls(**kwargs))
            logger.debug("Loaded sidecar tool: %s", tool_name)
        except Exception as e:
            logger.debug("Skipped %s (sidecar not available): %s", tool_name, e)

    logger.info(f"Loaded {len(tools)} total tools")
    return tools
