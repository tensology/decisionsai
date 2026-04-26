"""Action mode resolution helpers for computer-use flows."""


def resolve_execute_action(explicit_execute_action, vision_intent, locate_intents) -> bool:
    """Resolve whether physical mouse/keyboard action should execute.

    Priority:
    1) Explicit execute_action arg from tool call.
    2) Safe default for locate intents -> False.
    3) Otherwise default True.
    """
    if explicit_execute_action is not None:
        return bool(explicit_execute_action)
    if vision_intent in locate_intents:
        return False
    return True
