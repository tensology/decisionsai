"""
Dynamic tool loader — instantiates all LangChain tools from the TOOL_REGISTRY.

All tool imports are deferred to instantiation time via importlib to avoid
circular imports and keep the module lightweight.
"""

import importlib
import logging
from typing import List, Dict, Optional

from distr.core.agent.tools.base import BaseActionTool
from distr.core.utils import load_settings_from_db

logger = logging.getLogger(__name__)

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
    "CreateStepRunnerTool":    ("step_runner.create_step_runner", "CreateStepRunnerTool"),
    "ListStepRunnerSessionsTool": ("step_runner.step_runner_tools", "ListStepRunnerSessionsTool"),
    "GetStepRunnerSessionTool":   ("step_runner.step_runner_tools", "GetStepRunnerSessionTool"),
    "DeleteStepRunnerSessionTool": ("step_runner.step_runner_tools", "DeleteStepRunnerSessionTool"),
    "UpdateStepRunnerStepTool":   ("step_runner.step_runner_tools", "UpdateStepRunnerStepTool"),
    "AddStepRunnerStepTool":      ("step_runner.step_runner_tools", "AddStepRunnerStepTool"),
    "RemoveStepRunnerStepTool":   ("step_runner.step_runner_tools", "RemoveStepRunnerStepTool"),
    "RunStepRunnerAllTool":       ("step_runner.step_runner_tools", "RunStepRunnerAllTool"),
    "UpdateScheduleTool":         ("step_runner.step_runner_tools", "UpdateScheduleTool"),
    "StartRecordingTool":      ("actions.start_recording", "StartRecordingTool"),
    "StopRecordingTool":       ("actions.stop_recording", "StopRecordingTool"),
    "PlayActionTool":          ("actions.play_action", "PlayActionTool"),
    "StopActionTool":          ("actions.stop_action", "StopActionTool"),
    "ListActionsTool":         ("actions.list_actions", "ListActionsTool"),
    "CreateSnippetTool":       ("actions.create_snippet", "CreateSnippetTool"),
    "UseSnippetTool":          ("actions.use_snippet", "UseSnippetTool"),
    # chat/
    "NewChatTool":             ("chat.new_chat", "NewChatTool"),
    "ClearChatTool":           ("chat.clear_chat", "ClearChatTool"),
    "OracleGlobeTool":         ("chat.oracle_globe", "OracleGlobeTool"),
    "OpenPageTool":            ("chat.open_page", "OpenPageTool"),
    # system/
    "SystemInfoTool":          ("system.system_info", "SystemInfoTool"),
    "ExitAppTool":             ("system.exit_app", "ExitAppTool"),
    "WakeUpTool":              ("system.wake_up", "WakeUpTool"),
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
    "RubeTool":                ("integrations.rube_tool", "RubeTool"),
    "CreateCursorTicketTool":  ("integrations.create_cursor_ticket", "CreateCursorTicketTool"),
    "KanbanTicketTool":        ("integrations.kanban_ticket", "KanbanTicketTool"),
    "PlaywrightTool":          ("integrations.playwright_tool", "PlaywrightTool"),
}

_BASE_PACKAGE = "distr.core.agent.tools"


def _get_tool_class(name: str):
    """Dynamically import and return a tool class by registry name."""
    submodule, class_name = TOOL_REGISTRY[name]
    module = importlib.import_module(f"{_BASE_PACKAGE}.{submodule}")
    return getattr(module, class_name)


def load_tools(chat_manager=None, filter_methods: Optional[List[str]] = None, use_navigation_tools: bool = True, llm_service=None, tts_service=None, llm_model=None, event_queue=None, command_queue=None, confirmation_results_dict=None) -> List:
    """
    Load all tools from actions.config.json and specialized navigation tools.

    Args:
        chat_manager: Optional chat manager instance to pass to tools
        filter_methods: Optional list of method prefixes to filter (e.g., ['windows.', 'shortcuts.'])
        use_navigation_tools: If True, include specialized navigation tools for better natural language handling

    Returns:
        List of tool instances (mix of BaseActionTool and specialized tools)
    """
    tools = []
    navigation_tools_count = 0

    # Add specialized tools first (these handle natural language better)
    logger.info(f"load_tools called with use_navigation_tools={use_navigation_tools}")
    if use_navigation_tools:
        logger.info("Loading specialized tools...")
        specialized_tools = []
        tool_definitions = [
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
            ("ExitAppTool", dict(llm_service=llm_service)),
            # Rework Clipboard
            ("ReworkClipboardTool", dict(llm_model=llm_model or "qwen3:8b")),
            # Summarize Clipboard
            ("SummarizeClipboardTool", dict(llm_model=llm_model or "qwen3:8b", llm_service=llm_service)),
            # Create Snippet
            ("CreateSnippetTool", dict(event_queue=event_queue)),
            # Use Snippet
            ("UseSnippetTool", {}),
            # Create Action
            ("CreateActionTool", dict(event_queue=event_queue)),
            # Create Step Runner
            ("CreateStepRunnerTool", dict(chat_manager=chat_manager)),
            # Step Runner CRUD and execution
            ("ListStepRunnerSessionsTool", {}),
            ("GetStepRunnerSessionTool", {}),
            ("DeleteStepRunnerSessionTool", dict(event_queue=event_queue)),
            ("UpdateStepRunnerStepTool", dict(event_queue=event_queue)),
            ("AddStepRunnerStepTool", dict(event_queue=event_queue)),
            ("RemoveStepRunnerStepTool", dict(event_queue=event_queue)),
            ("RunStepRunnerAllTool", dict(event_queue=event_queue)),
            ("UpdateScheduleTool", dict(event_queue=event_queue)),
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
            # Kanban Board Ticket (primary ticket tool)
            ("KanbanTicketTool", dict(chat_manager=chat_manager, llm_service=llm_service, event_queue=event_queue)),
            # Playwright Browser Automation
            ("PlaywrightTool", dict(event_queue=event_queue, command_queue=command_queue, confirmation_results_dict=confirmation_results_dict)),
            # Document Extractor
            ("DocumentExtractorTool", {}),
            # System Information
            ("SystemInfoTool", dict(chat_manager=chat_manager)),
            ("ImageGeneratorTool", {}),
            # Wake Up
            ("WakeUpTool", {}),
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
        ]

        # Check if Rube is enabled before loading tools
        settings = load_settings_from_db()
        rube_enabled = settings.get('rube_enabled', False)
        if rube_enabled:
            tool_definitions.append(("RubeTool", {}))
            logger.info("Rube is enabled - RubeTool will be loaded")
        else:
            logger.info("Rube is disabled - RubeTool will not be loaded")

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

    logger.info(f"Loaded {len(tools)} total tools")
    return tools
