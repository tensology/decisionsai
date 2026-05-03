"""
List Actions Tool for LangChain.

This tool lists all available actions with their descriptions.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
import logging
import json
from distr.core.agent.tool_voice_format import voice_then_reference
from distr.core.db import get_session, Action

logger = logging.getLogger(__name__)


class ListActionsInput(BaseModel):
    """Input schema for list_actions tool."""
    text: Optional[str] = Field(default="", description="Optional search text to filter actions")


class ListActionsTool(BaseTool):
    """Tool for listing all available actions with their descriptions."""
    
    name: str = "list_actions"
    description: str = """List all available actions with their descriptions.
    
    Use this tool when the user wants to see what actions are available, or when you need to find an action by its description.
    Returns a formatted list of all actions (both recorded actions from the database and config-based actions) with their titles and descriptions.
    
    Usage:
    - "list actions" -> Lists all actions
    - "what actions are available" -> Lists all actions
    - "show me the actions" -> Lists all actions
    """
    args_schema: type[BaseModel] = ListActionsInput

    def _run(self, text: str = "", **kwargs) -> str:
        """List all available actions."""
        try:
            actions_list = []
            
            # Get actions from database (recorded actions)
            with get_session() as session:
                db_actions = session.query(Action).all()
                for action in db_actions:
                    if action.title:
                        action_info = {
                            "title": action.title,
                            "description": action.description or "No description",
                            "type": "instruction" if action.is_instruction else "recorded",
                            "trigger_words": [action.title.lower()]
                        }
                        
                        # Add additional trigger words
                        if action.additional_trigger_words:
                            try:
                                trigger_words = json.loads(action.additional_trigger_words)
                                if isinstance(trigger_words, list):
                                    action_info["trigger_words"].extend([str(t).lower() for t in trigger_words if t])
                            except (json.JSONDecodeError, TypeError):
                                pass
                        
                        actions_list.append(action_info)
            
            if not actions_list:
                return "No actions found."
            
            # Format the output
            result_lines = [f"Available Actions ({len(actions_list)} total):\n"]
            
            for i, action in enumerate(actions_list, 1):
                result_lines.append(f"{i}. {action['title']}")
                result_lines.append(f"   Description: {action['description']}")
                result_lines.append(f"   Type: {action['type']}")
                if len(action['trigger_words']) > 1:
                    result_lines.append(f"   Trigger words: {', '.join(action['trigger_words'][:5])}")  # Show first 5
                result_lines.append("")
            
            result = "\n".join(result_lines)
            logger.info(f"ListActionsTool: Listed {len(actions_list)} actions")
            n = len(actions_list)
            spoken = (
                f"I found {n} saved actions you can trigger by name or phrase. "
                "If you want detail on one, say its title; the full list is below for the screen."
                if n != 1
                else "You have one saved action; details are below for the screen."
            )
            return voice_then_reference(spoken, result)
            
        except Exception as e:
            logger.error(f"Error listing actions: {e}", exc_info=True)
            return f"Error listing actions: {str(e)}"
    
    async def _arun(self, text: str = "", **kwargs) -> str:
        return self._run(text=text)







