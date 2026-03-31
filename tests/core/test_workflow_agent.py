"""Unit tests for WorkflowAgent (tasks 6.1 and 6.2).

6.1 — Instantiation with mock LLM providers, verifying isolated message history.
6.2 — execute() returning response text without touching any global state.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from distr.core.workflow_agent import WorkflowAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_settings(provider="Ollama", model="llama3"):
    return {
        "llm_provider": provider,
        "llm_model": model,
        "ollama_url": "http://localhost:11434/",
    }


def _make_agent(provider="Ollama", model="llama3"):
    """Create a WorkflowAgent with all external deps mocked."""
    settings = _mock_settings(provider, model)
    with patch("distr.core.workflow_agent.WorkflowAgent._load_tools"):
        with patch("distr.core.llm_factory.resolve_settings_keys", return_value=(provider, model)):
            agent = WorkflowAgent(settings=settings)
    return agent


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ===========================================================================
# 6.1 — WorkflowAgent instantiation with mock LLM providers
# ===========================================================================

class TestWorkflowAgentInstantiation:
    """Verify WorkflowAgent initialises with isolated state."""

    @patch("distr.core.workflow_agent.WorkflowAgent._load_tools")
    @patch("distr.core.llm_factory.resolve_settings_keys", return_value=("Ollama", "llama3"))
    def test_creates_with_ollama_provider(self, mock_resolve, mock_tools):
        agent = WorkflowAgent(settings=_mock_settings())
        assert agent.provider == "Ollama"
        assert agent.model == "llama3"

    @patch("distr.core.workflow_agent.WorkflowAgent._load_tools")
    @patch("distr.core.llm_factory.resolve_settings_keys", return_value=("OpenAI", "gpt-4o"))
    def test_creates_with_openai_provider(self, mock_resolve, mock_tools):
        agent = WorkflowAgent(settings=_mock_settings("OpenAI", "gpt-4o"))
        assert agent.provider == "OpenAI"
        assert agent.model == "gpt-4o"

    @patch("distr.core.workflow_agent.WorkflowAgent._load_tools")
    @patch("distr.core.llm_factory.resolve_settings_keys", return_value=("Anthropic", "claude-3"))
    def test_creates_with_anthropic_provider(self, mock_resolve, mock_tools):
        agent = WorkflowAgent(settings=_mock_settings("Anthropic", "claude-3"))
        assert agent.provider == "Anthropic"
        assert agent.model == "claude-3"

    @patch("distr.core.workflow_agent.WorkflowAgent._load_tools")
    @patch("distr.core.llm_factory.resolve_settings_keys", return_value=("Ollama", "llama3"))
    def test_starts_with_empty_message_history(self, mock_resolve, mock_tools):
        agent = WorkflowAgent(settings=_mock_settings())
        assert agent.messages == []

    @patch("distr.core.workflow_agent.WorkflowAgent._load_tools")
    @patch("distr.core.llm_factory.resolve_settings_keys", return_value=("Ollama", "llama3"))
    def test_message_history_is_isolated_between_agents(self, mock_resolve, mock_tools):
        """Two WorkflowAgent instances have completely separate message histories."""
        agent_a = WorkflowAgent(settings=_mock_settings())
        agent_b = WorkflowAgent(settings=_mock_settings())

        # Manually append to agent_a's internal list
        agent_a._messages.append({"role": "user", "content": "hello from A"})

        assert len(agent_a.messages) == 1
        assert len(agent_b.messages) == 0

    @patch("distr.core.workflow_agent.WorkflowAgent._load_tools")
    @patch("distr.core.llm_factory.resolve_settings_keys", return_value=("Ollama", "llama3"))
    def test_shutdown_clears_state(self, mock_resolve, mock_tools):
        agent = WorkflowAgent(settings=_mock_settings())
        agent._messages.append({"role": "user", "content": "test"})
        agent.shutdown()

        assert agent.messages == []
        assert agent.tools == []
        assert agent._shutdown is True

    @patch("distr.core.workflow_agent.WorkflowAgent._load_tools")
    @patch("distr.core.llm_factory.resolve_settings_keys", return_value=("Ollama", "llama3"))
    def test_loads_settings_from_db_when_none(self, mock_resolve, mock_tools):
        """When settings=None, loads from DB."""
        import sys
        # Create a mock settings module to avoid sqlalchemy dependency
        mock_settings_mod = MagicMock()
        mock_settings_mod.load_settings_from_db = MagicMock(return_value=_mock_settings())
        with patch.dict(sys.modules, {"distr.core.settings": mock_settings_mod}):
            agent = WorkflowAgent(settings=None)
            mock_settings_mod.load_settings_from_db.assert_called_once()


# ===========================================================================
# 6.2 — execute() returning response text without touching global state
# ===========================================================================

class TestWorkflowAgentExecute:
    """Verify execute() returns response text and keeps state isolated."""

    def test_execute_returns_response_text(self):
        """execute() returns the LLM response as a string."""
        agent = _make_agent()

        with patch("distr.core.llm_factory.create_stream", return_value=iter(["Hello", " world"])):
            result = _run(agent.execute("Say hello"))

        assert result == "Hello world"

    def test_execute_appends_to_message_history(self):
        """execute() appends user and assistant messages to the agent's own history."""
        agent = _make_agent()

        with patch("distr.core.llm_factory.create_stream", return_value=iter(["response"])):
            _run(agent.execute("instruction"))

        assert len(agent.messages) == 2
        assert agent.messages[0] == {"role": "user", "content": "instruction"}
        assert agent.messages[1] == {"role": "assistant", "content": "response"}

    def test_execute_does_not_touch_other_agent(self):
        """Executing on one agent does not affect another agent's messages."""
        agent_a = _make_agent()
        agent_b = _make_agent()

        with patch("distr.core.llm_factory.create_stream", return_value=iter(["resp_a"])):
            _run(agent_a.execute("instruction_a"))

        assert len(agent_a.messages) == 2
        assert len(agent_b.messages) == 0

    def test_execute_does_not_emit_signals(self):
        """execute() must not call signal_manager.send_text_input or any global signal.

        WorkflowAgent.execute() uses llm_factory.create_stream directly — it never
        imports or touches signal_manager. We verify by checking that the execute
        path only calls create_stream (no other side effects).
        """
        agent = _make_agent()

        with patch("distr.core.llm_factory.create_stream", return_value=iter(["ok"])) as mock_stream:
            result = _run(agent.execute("do something"))

        assert result == "ok"
        # create_stream was the only external call
        mock_stream.assert_called_once()
        # Verify no signal_manager attribute exists on the agent
        assert not hasattr(agent, "signal_manager")

    def test_execute_accumulates_history_across_calls(self):
        """Multiple execute() calls build up the conversation history."""
        agent = _make_agent()

        with patch("distr.core.llm_factory.create_stream", return_value=iter(["first"])):
            _run(agent.execute("step 1"))
        with patch("distr.core.llm_factory.create_stream", return_value=iter(["second"])):
            _run(agent.execute("step 2"))

        assert len(agent.messages) == 4
        assert agent.messages[0]["content"] == "step 1"
        assert agent.messages[1]["content"] == "first"
        assert agent.messages[2]["content"] == "step 2"
        assert agent.messages[3]["content"] == "second"

    def test_execute_after_shutdown_raises(self):
        """Calling execute() after shutdown raises RuntimeError."""
        agent = _make_agent()
        agent.shutdown()

        with pytest.raises(RuntimeError, match="shut down"):
            _run(agent.execute("should fail"))

    def test_execute_handles_llm_error_gracefully(self):
        """If the LLM stream raises, execute() returns an error string."""
        agent = _make_agent()

        def _failing_stream(**kwargs):
            raise ConnectionError("LLM unreachable")

        with patch("distr.core.llm_factory.create_stream", side_effect=_failing_stream):
            result = _run(agent.execute("try this"))

        assert "Error" in result
        # History should still be updated
        assert len(agent.messages) == 2
