from pathlib import Path


CHAT_MANAGER = (
    Path(__file__).resolve().parents[2]
    / "distr"
    / "core"
    / "chat_manager.py"
)


def _chat_manager_source() -> str:
    return CHAT_MANAGER.read_text(encoding="utf-8")


def test_set_current_chat_persists_agent_current_chat_id_for_context_selectors():
    src = _chat_manager_source()
    set_current_block = src.split("def set_current_chat(", 1)[1].split(
        "    def create_chat(",
        1,
    )[0]

    assert "settings.last_chat_id = chat_id" in set_current_block
    assert "settings.agent_current_chat_id = chat_id" in set_current_block
