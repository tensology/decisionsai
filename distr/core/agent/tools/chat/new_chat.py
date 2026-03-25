from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging

logger = logging.getLogger(__name__)

class NewChatTool(BaseTool):
    """Tool for starting a new chat conversation."""
    
    name: str = "new_chat"
    description: str = "Start a new chat conversation. Use this when the user says 'New Chat', 'Start over', 'New conversation', or similar."
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
    
    def _run(self, **kwargs) -> str:
        """Execute new chat creation."""
        return self._create_new_chat()
    
    async def _arun(self, **kwargs) -> str:
        """Async execution."""
        return self._create_new_chat()
    
    def _create_new_chat(self) -> str:
        if not self._chat_manager:
            logger.error("NewChatTool: Chat manager not available")
            return "Error: Chat manager not available"
            
        try:
            # Create a new chat with a temporary title and is_new=True
            # The next user message will trigger title generation in ChatManager
            # Don't pass input_text - we want to wait for the first transcription
            new_chat_id = self._chat_manager.create_chat("New Chat", input_text="", is_new=True)
            
            # Verify the chat was created and set as current
            current_chat_id = self._chat_manager.get_current_chat()
            logger.info(f"NewChatTool: Created chat {new_chat_id}, current_chat is now {current_chat_id}")
            
            if current_chat_id != new_chat_id:
                logger.warning(f"NewChatTool: Created chat {new_chat_id} but current_chat is {current_chat_id} - fixing...")
                self._chat_manager.set_current_chat(new_chat_id)
                logger.info(f"NewChatTool: Set current_chat to {new_chat_id}")
            
            # Return JSON string with chat info (will be parsed by ollama service)
            import json
            return json.dumps({
                "status": "success",
                "chat_id": new_chat_id,
                "current_chat_id": self._chat_manager.get_current_chat(),
                "silent": True
            })
        except Exception as e:
            logger.error(f"NewChatTool: Error creating new chat: {e}", exc_info=True)
            return f"Error: Failed to create new chat: {str(e)}"
