from distr.core.agent.services.llm.fast_action_detector import (
    ActionType,
    detect_fast_action,
)
from distr.core.agent.services.llm.mixins.fast_actions import FastActionMixin
from distr.core.agent.tools.system.exit_app import ExitAppTool
from distr.core.db import Base, Chat, ChatTurnEvent
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import contextlib


def test_screenshot_request_with_see_is_descriptive():
    text = "If you take a screenshot, you can see that that's not there."

    action = detect_fast_action(text)

    assert action.tool_name == "screenshot_analyzer"
    assert FastActionMixin._fa_is_screenshot_describe_request(text) is True


def test_long_screenshot_summary_is_not_replaced_by_generic_completion():
    summary = (
        "The Market Watch panel contains USDCHF, GBPUSD, EURUSD, USDJPY, and AUDUSD, "
        "but it does not contain XAUUSD or GOLD, so the requested symbol is not currently visible."
    )

    response = FastActionMixin._fa_screenshot_response_text(summary)

    assert response == summary
    assert response != "I've finished that step."


def test_ambiguous_farewell_does_not_route_to_exit():
    for text in ("I'm gone.", "I'm done.", "goodbye", "bye"):
        assert detect_fast_action(text).action_type is not ActionType.EXIT_APP


def test_explicit_exit_routes_original_text_to_tool():
    action = detect_fast_action("exit the app")

    assert action.action_type is ActionType.EXIT_APP
    assert action.tool_args == {"text": "exit the app"}


def test_exit_tool_rejects_non_explicit_request():
    class EventQueue:
        def __init__(self):
            self.events = []

        def put(self, event, block=False):
            self.events.append(event)

    queue = EventQueue()
    result = ExitAppTool(event_queue=queue)._run(text="I'm gone")

    assert "didn't close" in result
    assert queue.events == []


@contextlib.contextmanager
def _session_ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def test_new_user_message_cancels_superseded_running_turn(monkeypatch):
    from distr.core.chat_manager import ChatManagerCore

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    monkeypatch.setattr("distr.core.chat_manager.get_session", lambda: factory())
    monkeypatch.setattr("distr.core.chat_turns.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.orchestrator.get_session", lambda: _session_ctx(factory))

    with _session_ctx(factory) as session:
        root = Chat(parent_id=None, title="Test chat", provider="OpenAI", model_name="m")
        session.add(root)
        session.flush()
        chat_id = root.id

    manager = ChatManagerCore()
    manager.add_user_message(chat_id, "Take a screenshot and tell me what you see.")
    first_turn_id = chat_id

    manager.add_user_message(chat_id, "No, tell me what to do.")

    with _session_ctx(factory) as session:
        terminal = (
            session.query(ChatTurnEvent)
            .filter(
                ChatTurnEvent.chat_id == chat_id,
                ChatTurnEvent.turn_id == first_turn_id,
                ChatTurnEvent.event_type == "turn_cancelled",
            )
            .one()
        )

    assert terminal.status == "cancelled"
    assert terminal.summary == "Superseded by a new request."
