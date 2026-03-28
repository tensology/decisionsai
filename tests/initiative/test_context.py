import json
import pytest
from unittest.mock import patch, MagicMock
from distr.core.initiative.context import ContextAssembler, ContextBundle


class TestContextAssemblerBuild:
    def test_returns_context_bundle(self):
        assembler = ContextAssembler()
        with patch.object(assembler, "_fetch_chat_history", return_value=[]), \
             patch.object(assembler, "_fetch_scheduled_sessions", return_value=[]), \
             patch.object(assembler, "_fetch_kanban_summary", return_value=[]), \
             patch.object(assembler, "_fetch_stuck_tasks", return_value=[]), \
             patch.object(assembler, "_fetch_unfinished_workflows", return_value=[]):
            bundle = assembler.build({})
            assert isinstance(bundle, ContextBundle)

    def test_chat_history_exception_uses_empty_fallback(self):
        assembler = ContextAssembler()
        with patch.object(assembler, "_fetch_chat_history", side_effect=Exception("DB error")), \
             patch.object(assembler, "_fetch_scheduled_sessions", return_value=[]), \
             patch.object(assembler, "_fetch_kanban_summary", return_value=[]), \
             patch.object(assembler, "_fetch_stuck_tasks", return_value=[]), \
             patch.object(assembler, "_fetch_unfinished_workflows", return_value=[]):
            bundle = assembler.build({})
            assert bundle.chat_history == []

    def test_kanban_exception_uses_empty_fallback(self):
        assembler = ContextAssembler()
        with patch.object(assembler, "_fetch_chat_history", return_value=[]), \
             patch.object(assembler, "_fetch_scheduled_sessions", return_value=[]), \
             patch.object(assembler, "_fetch_kanban_summary", side_effect=Exception("DB error")), \
             patch.object(assembler, "_fetch_stuck_tasks", return_value=[]), \
             patch.object(assembler, "_fetch_unfinished_workflows", return_value=[]):
            bundle = assembler.build({})
            assert bundle.kanban_summary == []

    def test_chat_history_truncation_to_4000_chars(self):
        assembler = ContextAssembler()
        # Create messages that total > 4000 chars
        long_messages = [{"role": "user", "content": "x" * 300} for _ in range(20)]
        settings = {"agent_current_chat_id": 1}
        with patch("distr.core.chat.ChatService.get_chat_history", return_value=long_messages):
            result = assembler._fetch_chat_history(settings)
            total = sum(len(json.dumps(m)) for m in result)
            assert total <= 4000

    def test_initiative_settings_included(self):
        assembler = ContextAssembler()
        settings = {"initiative_level": "operate", "initiative_allow_telegram": True}
        with patch.object(assembler, "_fetch_chat_history", return_value=[]), \
             patch.object(assembler, "_fetch_scheduled_sessions", return_value=[]), \
             patch.object(assembler, "_fetch_kanban_summary", return_value=[]), \
             patch.object(assembler, "_fetch_stuck_tasks", return_value=[]), \
             patch.object(assembler, "_fetch_unfinished_workflows", return_value=[]):
            bundle = assembler.build(settings)
            assert bundle.initiative_settings == settings
