from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging

logger = logging.getLogger(__name__)

class NewChatTool(BaseTool):
    """Tool for starting a new chat conversation."""
    
    name: str = "new_chat"
    description: str = (
        "Start a new, empty Oracle chat conversation. Use this ONLY when the user says 'New Chat', "
        "'Start over', 'New conversation', or similar. NEVER use it for Development threads or project work "
        "('put this in the Decisions thread', 'create a new thread in project X'): use development_control "
        "send_instruction (existing thread) or create_thread (new thread) instead."
    )
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
    
    def _run(self, **kwargs) -> str:
        """Execute new chat creation."""
        return self._create_new_chat(kwargs.get("last_user_message") or "")
    
    async def _arun(self, **kwargs) -> str:
        """Async execution."""
        return self._create_new_chat(kwargs.get("last_user_message") or "")
    
    def _create_new_chat(self, last_user_message: str = "") -> str:
        import json
        # Hard guard (routee2e 2026-10-22): an instruction never spawns a chat. Only the
        # user's own New chat button (POST /api/chats from the UI) creates one.
        from distr.core.agent.services.llm.tool_execution_policy import NEW_CHAT_BLOCK_REASON

        logger.warning("NewChatTool: refused (new chats only come from the user's New chat button)")
        return json.dumps({"status": "refused", "reason": NEW_CHAT_BLOCK_REASON})
        # Bug 2026-10-22: "put it in the decisions thread ... create a new thread in the
        # project" was routed here, spawning an empty Oracle chat and orphaning the turn.
        from distr.core.workflow.oracle_thread_handoff import mentions_development_thread

        if mentions_development_thread(last_user_message):
            return json.dumps({
                "status": "refused",
                "reason": (
                    "The user asked for a Development thread, not a new chat. Call development_control "
                    "with action=send_instruction (thread_id of the current/named Development thread) or "
                    "action=create_thread (project_id, instruction) instead."
                ),
            })
        if not self._chat_manager:
            logger.error("NewChatTool: Chat manager not available")
            return "Error: Chat manager not available"
            
        try:
            # Create a new chat with a temporary title and is_new=True
            # The next user message will trigger title generation in ChatManager
            # Don't pass input_text - we want to wait for the first transcription
            origin_chat_id = None
            try:
                origin_chat_id = self._chat_manager.get_current_chat()
            except Exception:
                origin_chat_id = None
            new_chat_id = self._chat_manager.create_chat("New Chat", input_text="", is_new=True)
            # The reply now goes to the new chat, so close the originating turn instead of
            # leaving its "Preparing the answer" card spinning forever.
            if origin_chat_id and origin_chat_id != new_chat_id:
                try:
                    from distr.core.chat_turns import complete_turn

                    complete_turn(int(origin_chat_id), display_text="Started a new chat.")
                except Exception:
                    logger.debug("NewChatTool: could not close originating turn", exc_info=True)
            
            # Verify the chat was created and set as current
            current_chat_id = self._chat_manager.get_current_chat()
            logger.info(f"NewChatTool: Created chat {new_chat_id}, current_chat is now {current_chat_id}")
            
            if current_chat_id != new_chat_id:
                logger.warning(f"NewChatTool: Created chat {new_chat_id} but current_chat is {current_chat_id} - fixing...")
                self._chat_manager.set_current_chat(new_chat_id)
                logger.info(f"NewChatTool: Set current_chat to {new_chat_id}")
            
            # Return JSON string with chat info (will be parsed by ollama service)
            return json.dumps({
                "status": "success",
                "chat_id": new_chat_id,
                "current_chat_id": self._chat_manager.get_current_chat(),
                "silent": True
            })
        except Exception as e:
            logger.error(f"NewChatTool: Error creating new chat: {e}", exc_info=True)
            return f"Error: Failed to create new chat: {str(e)}"
