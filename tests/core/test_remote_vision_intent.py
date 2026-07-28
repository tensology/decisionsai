from distr.core.agent.services.vision.intent_classifier import VisionIntent, classify_vision_intent
from distr.core.human_engagement import remote_user_reply_text


def test_remote_screen_read_is_not_misclassified_as_workflow():
    wrapped = (
        "[Remote reply context]\n"
        "Last update sent to user: Follow this workflow in order.\n"
        "Treat the reply as steering or feedback for this ticket/workflow.\n\n"
        "User reply:\nOkay, read what's on the screen."
    )

    assert classify_vision_intent(remote_user_reply_text(wrapped)) == VisionIntent.READ_TEXT
