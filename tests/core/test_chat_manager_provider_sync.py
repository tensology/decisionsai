from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from distr.core.chat_manager import ChatManagerCore


def test_init_syncs_provider_from_live_chat_when_id_already_set():
  """agent_current_chat_id preloads the live chat; provider must still come from the chat row."""
  fake_settings = SimpleNamespace(
      last_chat_id=99,
      agent_current_chat_id=1,
      conversational_llm_provider="openai",
      conversational_llm_model="gpt-5.2",
      llm_provider=None,
      agent_provider=None,
      agent_model=None,
  )
  fake_chat = SimpleNamespace(
      id=1,
      provider="openai",
      model_name="gpt-5.2",
      voice_provider=None,
      voice_model=None,
  )
  session = MagicMock()
  session.query.return_value.first.return_value = fake_settings
  session.get.return_value = fake_chat

  with patch("distr.core.chat_manager.get_session") as mock_get_session, patch(
      "distr.core.workflow.development_threads.is_development_thread",
      return_value=False,
  ):
      mock_get_session.return_value.__enter__.return_value = session
      cm = ChatManagerCore()

  assert cm.get_current_chat() == 1
  assert cm.current_provider == "OpenAI"
  assert cm.current_model == "gpt-5.2"



def test_init_does_not_use_last_chat_id_as_live_fallback():
  """Viewed last_chat_id must not become the live agent after a restart."""
  fake_settings = SimpleNamespace(
      last_chat_id=1,
      agent_current_chat_id=None,
      conversational_llm_provider="openai",
      conversational_llm_model="gpt-5.2",
      llm_provider=None,
      agent_provider=None,
      agent_model=None,
  )
  session = MagicMock()
  session.query.return_value.first.return_value = fake_settings
  session.get.return_value = SimpleNamespace(
      id=1, provider="openai", model_name="gpt-5.2", voice_provider=None, voice_model=None,
  )

  with patch("distr.core.chat_manager.get_session") as mock_get_session:
      mock_get_session.return_value.__enter__.return_value = session
      cm = ChatManagerCore()

  assert cm.get_current_chat() is None


def test_set_current_chat_refreshes_metadata_when_chat_unchanged():
  cm = ChatManagerCore()
  cm._current_chat_id = 1
  cm.current_provider = "Ollama"

  fake_chat = SimpleNamespace(
      id=1,
      provider="openai",
      model_name="gpt-5.2",
      voice_provider=None,
      voice_model=None,
  )
  session = MagicMock()
  session.get.return_value = fake_chat
  session.query.return_value.first.return_value = None

  with patch("distr.core.chat_manager.get_session") as mock_get_session:
      mock_get_session.return_value.__enter__.return_value = session
      with patch.object(cm, "get_chat_history"):
        cm.set_current_chat(1)

  assert cm.current_provider == "OpenAI"
  assert cm.current_model == "gpt-5.2"
