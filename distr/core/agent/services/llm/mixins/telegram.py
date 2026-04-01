"""
Telegram Mixin

Handles Telegram-specific flag propagation, response emission, file sending,
and cleanup.  Used by all LLM providers.

Extracted from LLMSharedMixin to keep shared.py focused on core LLM logic.
"""

import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)


class TelegramMixin:
    """Mixin providing Telegram helpers for LLM services.

    Expects on self:
    - event_queue
    - _messages (list)
    - _tools_dict (dict)
    - _tts_service
    - _is_telegram_request
    - _uploaded_image_path
    """

    def _propagate_telegram_flags(self):
        """Propagate telegram flags from instance vars to the current asyncio thread.

        Returns is_telegram.  Response-format preference is now determined by
        ``determine_response_format()`` in the event handler using
        Settings_Store-backed settings, so thread-local text-response flags
        are no longer propagated here.
        """
        import threading

        is_telegram = getattr(self, '_is_telegram_request', False)

        if is_telegram:
            threading.current_thread().telegram_request = True
            for t in threading.enumerate():
                if not getattr(t, 'telegram_request', False):
                    t.telegram_request = True

        # Clear stale file-sent flags
        if hasattr(threading.current_thread(), 'telegram_file_sent'):
            threading.current_thread().telegram_file_sent = False
        if hasattr(self, '_tts_service') and self._tts_service and hasattr(self._tts_service, '_telegram_file_sent'):
            self._tts_service._telegram_file_sent = False

        # Uploaded image
        if not is_telegram:
            if hasattr(self, '_uploaded_image_path') and self._uploaded_image_path and os.path.exists(self._uploaded_image_path):
                threading.current_thread().telegram_uploaded_image = self._uploaded_image_path

        return is_telegram

    def _emit_telegram_response(self, full_content: str = "", follow_up_content: str = ""):
        """Emit send_to_telegram event from the LLM service finally-block.

        For Telegram requests, TextFrames are NOT pushed into the TTS pipeline,
        so the Kokoro send_to_telegram path never fires.  Instead we send the
        full response text here; app.py will generate a separate TTS audio file
        and deliver it via Telegram.

        Call this in the finally-block of _generate_response().
        """
        import threading

        if not getattr(self, '_is_telegram_request', False) or not self.event_queue:
            return

        response_text = (
            follow_up_content
            or full_content
            or getattr(self, '_telegram_fallback_text', '')
            or ""
        ).strip()
        self._telegram_fallback_text = None  # Clear after use

        if not response_text:
            return

        # Sanitize: strip leaked tool call formatting from the response.
        # Some models hallucinate raw tool call syntax (to=functions.X, json{...})
        # in their text output after executing tool calls.
        import re
        response_text = re.sub(r'to=functions\.\S+', '', response_text)
        response_text = re.sub(r'[^\x00-\x7F\u00C0-\u024F\u0400-\u04FF]{3,}', '', response_text)  # strip long non-latin runs first
        response_text = re.sub(r'json\s*[\n\r]*\s*\{[^}]*\}', '', response_text)  # json{...} blocks
        response_text = re.sub(r'\bjson\b', '', response_text)  # stray "json" keyword
        response_text = re.sub(r'\s{2,}', ' ', response_text).strip()

        if not response_text:
            return

        is_done = response_text.lower() in (
            'done', 'done.', 'complete', 'completed', 'finished', 'finished.',
        )

        analyzed_image_path = None
        skip_screenshot = False
        for t in threading.enumerate():
            img = (
                getattr(t, 'telegram_analyzed_image', None)
                or getattr(t, 'telegram_send_raw_screenshot', None)
            )
            if img:
                analyzed_image_path = img
            if getattr(t, 'skip_telegram_screenshot', False):
                skip_screenshot = True
                t.skip_telegram_screenshot = False

        self.event_queue.put(('send_to_telegram', {
            'text': response_text,
            'is_done': is_done,
            'skip_screenshot': skip_screenshot,
            'provider': 'kokoro',
            'analyzed_image_path': analyzed_image_path,
        }), block=False)
        logger.info("%s: Emitted send_to_telegram from LLM (bypassed TTS pipeline)",
                    getattr(self, 'SERVICE_NAME', self.__class__.__name__))

    def _cleanup_telegram_flags(self):
        """Clean up telegram thread-local and instance flags.

        Call this in the finally-block of _generate_response() after
        _emit_telegram_response().
        """
        import threading

        # Clear telegram_request on ALL threads — _propagate_telegram_flags
        # sets it on every thread, so cleanup must mirror that.
        for t in threading.enumerate():
            if getattr(t, 'telegram_request', False):
                t.telegram_request = False
            if getattr(t, 'telegram_analyzed_image', None):
                t.telegram_analyzed_image = None
            if getattr(t, 'telegram_send_raw_screenshot', None):
                t.telegram_send_raw_screenshot = None

        cur = threading.current_thread()
        if hasattr(cur, 'telegram_uploaded_image'):
            cur.telegram_uploaded_image = None
        self._is_telegram_request = False
        self._uploaded_image_path = None

    def _extract_action_required_path(self):
        """Scan recent tool messages for [ACTION REQUIRED] and extract the file path."""
        import re as _re
        for msg in reversed(self._messages):
            if msg.get("role") != "tool" or "[ACTION REQUIRED" not in msg.get("content", ""):
                continue
            content = msg["content"]
            m = _re.search(r'file_path="([^"]+)"', content)
            if m:
                return m.group(1)
            m = _re.search(r'Result:\s*([^\n]+)', content)
            if m:
                p = os.path.expanduser(m.group(1).strip())
                if os.path.isfile(p):
                    return p
        return None

    def _mark_telegram_file_sent(self):
        """Set telegram_file_sent on thread + TTS service."""
        import threading
        threading.current_thread().telegram_file_sent = True
        if hasattr(self, '_tts_service') and self._tts_service:
            self._tts_service._telegram_file_sent = True
        logger.info("Marked telegram_file_sent=True")

    async def _auto_send_file_to_telegram(self):
        """If ACTION REQUIRED flag is set, extract file path and auto-call send_file_to_telegram.

        Returns True if file was sent (caller should skip follow-up API call).
        """
        import threading

        if not getattr(threading.current_thread(), 'suppress_tts_for_tool_chain', False):
            return False

        file_path = self._extract_action_required_path()
        if not file_path:
            logger.warning("ACTION REQUIRED set but could not extract file path — falling back to follow-up")
            threading.current_thread().suppress_tts_for_tool_chain = False
            return False

        if "send_file_to_telegram" not in self._tools_dict:
            logger.error("send_file_to_telegram tool not found")
            threading.current_thread().suppress_tts_for_tool_chain = False
            return False

        tool = self._tools_dict["send_file_to_telegram"]
        try:
            logger.info(f"Auto-calling send_file_to_telegram: {file_path}")
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, lambda t=tool, fp=file_path: t._run(file_path=fp)
            )

            self._messages.append({
                "role": "tool", "name": "send_file_to_telegram",
                "content": str(result),
                "tool_call_id": f"auto_call_{int(time.time() * 1000000)}",
            })

            self._mark_telegram_file_sent()
            threading.current_thread().suppress_tts_for_tool_chain = False
            return True
        except Exception as e:
            logger.error(f"Auto-call send_file_to_telegram failed: {e}", exc_info=True)
            threading.current_thread().suppress_tts_for_tool_chain = False
            return False
