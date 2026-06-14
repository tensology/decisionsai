from distr.core.agent.tools.base import BaseActionTool
import logging

logger = logging.getLogger(__name__)

class ClearChatTool(BaseActionTool):
    """Tool for clearing the current chat history."""
    
    def __init__(self, chat_manager=None):
        super().__init__(
            name="clear_chat",  # Explicitly set name to clear_chat (not "clear chat")
            description="Clears the current chat history and starts fresh. Use this tool when the user says 'clear chat', 'clear history', 'start over', 'reset conversation', or 'clear this chat'. This removes all messages from the current chat but keeps the chat session itself. Call this tool directly - do not output JSON text.",
            action_config={
                "trigger": "clear chat",
                "description": "Clears the current chat history and starts fresh. Use this tool when the user says 'clear chat', 'clear history', 'start over', 'reset conversation', or 'clear this chat'. This removes all messages from the current chat but keeps the chat session itself. Call this tool directly - do not output JSON text.",
                "method": "chat.clear_chat",
                "name": "clear_chat",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "confirm": {
                            "type": "boolean",
                            "description": "Explicit confirmation to clear the chat (default: true)"
                        }
                    }
                }
            }, 
            chat_manager=chat_manager
        )

    async def _arun(self, confirm: bool = True, **kwargs) -> str:
        """Execute the clear chat action."""
        logger.debug(f"Executing ClearChatTool (confirm={confirm})")
        
        if not self._chat_manager:
            logger.error("Chat manager not available for ClearChatTool")
            return "Error: Chat manager not available."
            
        try:
            current_chat_id = self._chat_manager.get_current_chat()
            if not current_chat_id:
                return "Error: No active chat to clear."
            
            success = self._chat_manager.clear_chat_messages(current_chat_id)
            
            if success:
                return "Chat history cleared. Starting fresh."
            else:
                return "Error: Failed to clear chat history."
                
        except Exception as e:
            logger.error(f"Error executing ClearChatTool: {e}", exc_info=True)
            return f"Error clearing chat: {str(e)}"

