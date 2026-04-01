"""
Fast Action Mixin

Handles fast action execution (bypassing the LLM for high-confidence tool
invocations), read-this-directly, conversation summary, and clipboard helpers.

Extracted from LLMSharedMixin to keep shared.py focused on core LLM logic.
"""

import asyncio
import logging
import os
import platform
import re
import subprocess
import time
from typing import Optional

from distr.core.signals import signal_manager
from distr.core.agent.services.llm.text_utils import clean_text_for_tts

logger = logging.getLogger(__name__)


class FastActionMixin:
    """Mixin providing fast action execution for LLM services.

    Expects on self:
    - _tools, _tools_dict
    - _messages (list)
    - _cancelled (bool)
    - _processed_fast_actions (set)
    - _speaker_enabled (bool)
    - chat_manager
    - event_queue
    - push_frame(frame, direction?)
    - _generate_response() (for LLM follow-up)
    - _fa_save_to_history (defined here)
    """

    async def _execute_fast_action(self, fast_action, current_chat_id: str) -> bool:
        """Execute a fast-detected action directly, bypassing the LLM.

        Returns True if action was handled, False to fall back to LLM.
        """
        from distr.core.agent.services.llm.fast_action_detector import ActionType

        logger.debug("LLM: _execute_fast_action for %s, tool=%s", fast_action.action_type.value, fast_action.tool_name)

        try:
            # --- locate tool ---
            tool = None
            if hasattr(self, '_tools_dict') and self._tools_dict:
                tool = self._tools_dict.get(fast_action.tool_name)
            if not tool and self._tools:
                tool = next((t for t in self._tools if t.name == fast_action.tool_name), None)
            if not tool:
                available = [t.name for t in self._tools] if self._tools else []
                logger.error("Fast action tool '%s' NOT FOUND. Available: %s", fast_action.tool_name, available[:20])
                return False

            # --- execute ---
            logger.info("Fast exec: %s(%s)", tool.name, fast_action.tool_args)
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, lambda t=tool, a=fast_action.tool_args: t._run(**a)
            )
            logger.debug("LLM: Fast action result: %s", str(result)[:100])

            # --- file_operations → trigger LLM follow-up ---
            if fast_action.tool_name == "file_operations":
                self._messages.append({"role": "tool", "content": result, "name": "file_operations"})
                if not self._cancelled:
                    await self._generate_response()
                return True

            # --- wait for async read task if needed ---
            if fast_action.response_type == "tts":
                if result and isinstance(result, str) and ("Reading" in result or "read" in result.lower()):
                    for _ in range(10):
                        if hasattr(tool, '_read_task') and tool._read_task:
                            break
                        await asyncio.sleep(0.01)

            # --- rework_clipboard / summarize_clipboard ---
            if fast_action.tool_name in ('rework_clipboard', 'summarize_clipboard') and result:
                return await self._fa_handle_clipboard_rework(fast_action, current_chat_id, str(result))

            # --- dispatch by response_type ---
            handler = {
                "action_playback": self._fa_handle_action_playback,
                "done":            self._fa_handle_done,
                "tts":             self._fa_handle_tts,
                "tts_clipboard":   self._fa_handle_tts_clipboard,
                "llm_response":    self._fa_handle_llm_response,
            }.get(fast_action.response_type)

            if handler:
                return await handler(fast_action, current_chat_id, result, tool)

            logger.warning("LLM: Unknown response_type '%s' for fast action, falling back to LLM", fast_action.response_type)
            return False

        except Exception as e:
            logger.error("LLM: Error executing fast action: %s", e, exc_info=True)
            return False

    # --- fast-action sub-handlers (private) ---

    async def _fa_push_tts(self, text: str):
        """Helper: push text through TTS pipeline frames."""
        from distr.core.agent.libs import LLMFullResponseStartFrame, LLMFullResponseEndFrame, TextFrame
        await self.push_frame(LLMFullResponseStartFrame())
        cleaned = clean_text_for_tts(text)
        if cleaned:
            await self.push_frame(TextFrame(text=cleaned))
        await self.push_frame(LLMFullResponseEndFrame())

    def _fa_save_to_history(self, chat_id, text: str, emit_signals: bool = False):
        """Helper: save assistant message and optionally emit UI signals."""
        if self.chat_manager and chat_id:
            try:
                self.chat_manager.add_assistant_message(chat_id, text)
                if emit_signals:
                    try:
                        signal_manager.chat_stream_finished.emit(chat_id)
                        signal_manager.typing_indicator_changed.emit(False)
                    except RuntimeError:
                        pass
            except Exception as e:
                logger.warning("LLM: Could not save fast action response: %s", e)

    async def _fa_handle_clipboard_rework(self, fast_action, chat_id, result_str: str) -> bool:
        if result_str.startswith("Error"):
            response_text = result_str.replace("Error: ", "").strip()
        else:
            response_text = result_str

        if fast_action.response_type == "tts":
            if not self._cancelled:
                await self._fa_push_tts(response_text)
            self._fa_save_to_history(chat_id, response_text, emit_signals=True)
        else:
            await self._fa_push_tts("Done")
            self._fa_save_to_history(chat_id, response_text)

        self._messages.append({"role": "assistant", "content": response_text})
        return True

    async def _fa_handle_action_playback(self, fast_action, chat_id, result, tool) -> bool:
        is_error = result and isinstance(result, str) and (
            result.startswith("Error") or "error" in result.lower() or "failed" in result.lower()
        )
        if is_error:
            response_text = result.replace("Error: ", "").replace("Error executing task: ", "").strip()
            if len(response_text) > 200:
                response_text = response_text[:200] + "..."
            await self._fa_push_tts(response_text)
            self._fa_save_to_history(chat_id, response_text)
        else:
            if self.chat_manager and chat_id and result:
                self.chat_manager.add_assistant_message(
                    chat_id, result if isinstance(result, str) else "Action playback started"
                )
        return True

    async def _fa_handle_done(self, fast_action, chat_id, result, tool) -> bool:
        import json as _json
        from distr.core.agent.services.llm.fast_action_detector import ActionType

        # Check if the tool result explicitly requests silence (e.g. open_page returns {"silent": True})
        is_silent = False
        if result and isinstance(result, str):
            try:
                parsed = _json.loads(result)
                if isinstance(parsed, dict) and parsed.get("silent"):
                    is_silent = True
            except (ValueError, TypeError):
                pass

        is_error = result and isinstance(result, str) and (
            result.startswith("Error") or "error" in result.lower()
            or "failed" in result.lower() or "timeout" in result.lower()
        )

        if is_error:
            response_text = result.replace("Error: ", "").replace("Error executing task: ", "").strip()
            if len(response_text) > 200:
                response_text = response_text[:200] + "..."
        elif fast_action.action_type == ActionType.NEW_CHAT:
            response_text = "A new conversation has been created"
        elif fast_action.action_type == ActionType.CURSOR_TICKET:
            if result and isinstance(result, str) and not is_error:
                if "Successfully created" in result and "under .tickets)" in result:
                    response_text = "Ticket saved in your active project's tickets folder"
                elif "Successfully created" in result:
                    response_text = "Ticket created successfully"
                else:
                    response_text = result[:100] if len(result) <= 100 else "Ticket created"
            else:
                response_text = "Ticket created"
        else:
            response_text = "Done"
            if result and isinstance(result, str) and not is_error:
                if len(result) < 100 and "pasted" not in result.lower() and "Playing" in result:
                    response_text = result

        if is_silent and not is_error:
            # Tool requested silence — push end frame without speaking so the
            # pipeline stays clean but no audio (and no player) is triggered.
            from distr.core.agent.libs import LLMFullResponseStartFrame, LLMFullResponseEndFrame
            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(LLMFullResponseEndFrame())
        else:
            await self._fa_push_tts(response_text)
        self._fa_save_to_history(chat_id, response_text)
        return True

    async def _fa_handle_tts(self, fast_action, chat_id, result, tool) -> bool:
        # Check for async read task
        if hasattr(tool, '_read_task') and tool._read_task:
            logger.debug("LLM: Awaiting tool's async read task for TTS")
            try:
                await tool._read_task
            except Exception as e:
                logger.error("LLM: Error awaiting read task: %s", e)
                await self._fa_push_tts("Error reading content.")
            finally:
                tool._read_task = None
            self._fa_save_to_history(chat_id, "[Read aloud]")

        elif result and isinstance(result, str) and result.startswith("READ_ACTION:"):
            text_to_read = result[len("READ_ACTION:"):]
            await self._fa_push_tts(text_to_read)
            preview = text_to_read[:50] + "..." if len(text_to_read) > 50 else text_to_read
            self._fa_save_to_history(chat_id, f"[Read aloud: {preview}]")
        else:
            logger.warning("LLM: TTS path fallback, no _read_task and no READ_ACTION, result=%s", result)
            await self._fa_push_tts("Done")

        return True

    async def _fa_handle_tts_clipboard(self, fast_action, chat_id, result, tool) -> bool:
        if result and isinstance(result, str):
            text_to_read = result
            if result.startswith("CLIPBOARD CONTENT:"):
                text_to_read = result.replace("CLIPBOARD CONTENT:", "").strip()
                if "\n\nThis is the current clipboard content." in text_to_read:
                    text_to_read = text_to_read.split("\n\nThis is the current clipboard content.")[0].strip()

            if text_to_read and text_to_read.strip():
                await self._fa_push_tts(text_to_read)
                preview = text_to_read[:100] + "..." if len(text_to_read) > 100 else text_to_read
                self._fa_save_to_history(chat_id, f"[Read from clipboard: {preview}]")
            else:
                await self._fa_push_tts("The clipboard is empty.")
                self._fa_save_to_history(chat_id, "The clipboard is empty.")
        else:
            error_msg = str(result) if result else "Could not read the clipboard."
            await self._fa_push_tts(error_msg)
        return True

    async def _fa_handle_llm_response(self, fast_action, chat_id, result, tool) -> bool:
        # Mark as processed to prevent re-detection
        query_text = fast_action.original_text
        self._processed_fast_actions.add(query_text)

        # clipboard explain/elaborate already triggered LLM internally
        if fast_action.tool_name == 'clipboard_action' and result and "Processing" in str(result):
            return True

        # screenshot_analyzer error → speak directly
        if fast_action.tool_name == 'screenshot_analyzer' and result and isinstance(result, str) and str(result).startswith("Error:"):
            error_msg = str(result).replace("Error: ", "").strip()
            if not self._cancelled:
                await self._fa_push_tts(error_msg)
            self._fa_save_to_history(chat_id, error_msg, emit_signals=True)
            self._messages.append({"role": "assistant", "content": error_msg})
            return True

        # web_search → use result directly
        if fast_action.tool_name == 'web_search' and result:
            response_text = str(result)
            if not self._cancelled:
                await self._fa_push_tts(response_text)
            self._fa_save_to_history(chat_id, response_text, emit_signals=True)
            self._messages.append({"role": "assistant", "content": response_text})
            return True

        # Default: add tool result to messages and trigger LLM follow-up
        self._messages.append({
            "role": "tool",
            "content": str(result) if result else "Tool executed successfully.",
            "name": fast_action.tool_name,
        })
        if not self._cancelled:
            await self._generate_response()
        return True

    # ------------------------------------------------------------------ #
    #  Read-this / conversation summary                                   #
    # ------------------------------------------------------------------ #

    async def _handle_read_this_directly(self, direction, use_clipboard_directly=False):
        """Handle 'read this' command by sending clipboard content directly to TTS, bypassing LLM."""
        from distr.core.agent.libs import LLMFullResponseStartFrame, LLMFullResponseEndFrame, TextFrame

        try:
            pipeline_dir = getattr(self, '_pipeline_direction', None) or direction
            if not pipeline_dir:
                logger.error("Read this: No pipeline direction available")
                return

            if not getattr(self, '_FrameProcessor__started', False):
                setattr(self, '_FrameProcessor__started', True)

            # --- get clipboard ---
            if not use_clipboard_directly:
                try:
                    from distr.core.agent.tools.base import get_platform_modifier_key
                    from distr.core.agent.libs import pyautogui
                    cmd_key = get_platform_modifier_key()
                    pyautogui.keyDown(cmd_key)
                    pyautogui.press('c')
                    pyautogui.keyUp(cmd_key)
                    time.sleep(0.15)
                except Exception as e:
                    logger.error("Read this: Could not press copy shortcut: %s", e)

            clipboard_text = self._get_clipboard_text()
            if not clipboard_text or not clipboard_text.strip():
                logger.warning("Read this: Clipboard is empty")
                return

            logger.debug("Read this: Got clipboard content (%d chars)", len(clipboard_text))

            # --- stream to TTS ---
            self._cancelled = False
            await self.push_frame(LLMFullResponseStartFrame(), pipeline_dir)
            await asyncio.sleep(0.05)

            chunks = self._split_text_into_chunks(clipboard_text)
            for i, chunk in enumerate(chunks):
                if self._cancelled:
                    break
                cleaned = clean_text_for_tts(chunk, strip_whitespace=False)
                if cleaned:
                    await self.push_frame(TextFrame(text=cleaned), pipeline_dir)
                await asyncio.sleep(0.05)

            await self.push_frame(LLMFullResponseEndFrame(), pipeline_dir)
            logger.debug("Read this: Sent %d chars in %d chunks to TTS", len(clipboard_text), len(chunks))

        except Exception as e:
            logger.error("Error handling read this directly: %s", e, exc_info=True)

    async def _handle_conversation_summary(self, direction):
        """Handle 'what did we talk about' by generating a summary from conversation history."""
        from distr.core.agent.libs import LLMFullResponseStartFrame, LLMFullResponseEndFrame, TextFrame

        try:
            current_chat_id = None
            if self.chat_manager:
                current_chat_id = self.chat_manager.get_current_chat()
                if current_chat_id:
                    db_history = self.chat_manager.get_chat_history(current_chat_id)
                    system_prompt = self._messages[0] if self._messages else {"role": "system", "content": self._get_full_system_prompt()}
                    self._messages = [system_prompt]
                    for msg in db_history:
                        if msg.get('role') in ('user', 'assistant'):
                            self._messages.append(msg)
                    try:
                        signal_manager.chat_history_loaded.emit(current_chat_id)
                    except (RuntimeError, AttributeError):
                        pass

            conversation_messages = [m for m in self._messages if m.get('role') != 'system']

            # Remove the summary question itself
            if conversation_messages and conversation_messages[-1].get('role') == 'user':
                last_msg = conversation_messages[-1].get('content', '').lower()
                summary_triggers = [
                    "what did we talk", "what do we talk", "what were we talking",
                    "what did we discuss", "what do we discuss", "what were we discussing",
                    "what did we chat", "what do we chat", "what were we chatting",
                    "what was the story", "what story were we", "what story did we",
                ]
                if any(t in last_msg for t in summary_triggers):
                    conversation_messages = conversation_messages[:-1]

            summary_text = await self._generate_conversation_summary(conversation_messages)

            self._cancelled = False
            await self.push_frame(LLMFullResponseStartFrame(), direction)
            await asyncio.sleep(0.05)
            cleaned = clean_text_for_tts(summary_text)
            if cleaned:
                await self.push_frame(TextFrame(text=cleaned), direction)
            await self.push_frame(LLMFullResponseEndFrame(), direction)

            self._messages.append({"role": "assistant", "content": summary_text})
            self._fa_save_to_history(current_chat_id, summary_text)
            if current_chat_id:
                try:
                    signal_manager.chat_message_added.emit(current_chat_id, "assistant", summary_text)
                    signal_manager.chat_stream_finished.emit(current_chat_id)
                    signal_manager.chat_updated.emit(current_chat_id)
                except RuntimeError:
                    pass

        except Exception as e:
            logger.error("Error handling conversation summary: %s", e, exc_info=True)
            try:
                from distr.core.agent.libs import LLMFullResponseStartFrame, LLMFullResponseEndFrame, TextFrame
                await self.push_frame(LLMFullResponseStartFrame(), direction)
                await self.push_frame(TextFrame(text="I'm sorry, I couldn't generate a summary right now."), direction)
                await self.push_frame(LLMFullResponseEndFrame(), direction)
            except Exception:
                pass

    # --- helpers ---

    @staticmethod
    def _get_clipboard_text() -> Optional[str]:
        """Get clipboard content using platform-specific methods."""
        try:
            system = platform.system()
            if system == "Darwin":
                r = subprocess.run(['pbpaste'], capture_output=True, text=True, timeout=1)
                return r.stdout if r.returncode == 0 else None
            elif system == "Windows":
                r = subprocess.run(['powershell', '-command', 'Get-Clipboard'], capture_output=True, text=True, timeout=1)
                return r.stdout.strip() if r.returncode == 0 else None
            else:
                for cmd in (['xclip', '-selection', 'clipboard', '-o'], ['xsel', '--clipboard', '--output']):
                    try:
                        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
                        if r.returncode == 0:
                            return r.stdout
                    except Exception:
                        continue
                return None
        except Exception as e:
            logger.error("Error getting clipboard content: %s", e)
            return None

    @staticmethod
    def _split_text_into_chunks(text: str, words_per_chunk: int = 10) -> list:
        """Split text into sentence-based chunks for streaming TTS."""
        sentence_pattern = r'([.!?]+[\s\n]+|[\n]{2,})'
        sentences = re.split(sentence_pattern, text)
        chunks = []
        for i in range(0, len(sentences), 2):
            chunk = sentences[i]
            if i + 1 < len(sentences):
                chunk += sentences[i + 1]
            if chunk.strip():
                chunks.append(chunk)
        if len(chunks) == 1 and len(chunks[0].split()) > words_per_chunk:
            words = chunks[0].split()
            chunks = []
            for i in range(0, len(words), words_per_chunk):
                c = ' '.join(words[i:i + words_per_chunk])
                if i + words_per_chunk < len(words):
                    c += ' '
                chunks.append(c)
        return chunks
