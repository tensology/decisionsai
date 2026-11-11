import time

import pytest

from tests.telegram.test_telegram_delivery_stability import DummyTelegramSender


def test_result_describing_reconnect_is_not_a_connection_notice():
    sender = DummyTelegramSender()
    assert sender.send_to_telegram('Completed. I fixed the reconnect failure.')
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 1


def test_proposal_result_preserves_exact_body_instead_of_status_sanitization():
    sender = DummyTelegramSender()
    text = 'Completed. Settings saved; service is online. Evidence: https://example.com/result'
    assert sender.send_to_telegram(text, interaction_token='proposal-update:1:job:0')
    assert sender._message_queue.get_nowait()['text'] == text


def test_distinct_interactions_with_same_text_keep_both_controls():
    sender = DummyTelegramSender()
    assert sender.send_to_telegram('Approve this step?', interaction_token='first', reply_markup={'inline_keyboard': []})
    assert sender.send_to_telegram('Approve this step?', interaction_token='second', reply_markup={'inline_keyboard': []})
    assert not sender.send_to_telegram('Approve this step?', interaction_token='first', reply_markup={'inline_keyboard': []})
    assert sender._message_queue.qsize() == 2


def test_minimum_interval_applies_between_every_message_in_a_batch(monkeypatch):
    sender = DummyTelegramSender()
    sender._last_send_time = 100
    sender._min_send_interval = 1
    monkeypatch.setattr(time, 'time', lambda: 100)
    assert sender.send_to_telegram('First result.')
    assert sender.send_to_telegram('Second result.')
    monkeypatch.setattr(time, 'time', lambda: 101)
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 1
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 1
    monkeypatch.setattr(time, 'time', lambda: 102)
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 2


def test_recipient_is_part_of_outbound_dedup_identity():
    sender = DummyTelegramSender()
    assert sender.send_to_telegram('Reviewed project result.')
    sender.telegram_user_id = 67890
    assert sender.send_to_telegram('Reviewed project result.')
    assert sender._message_queue.qsize() == 2


def test_invalid_owner_cannot_send_to_group_or_coerce_boolean_identity():
    for owner in [-42, True, 1.5, 'not-an-id']:
        sender = DummyTelegramSender()
        sender.telegram_user_id = owner
        assert not sender.send_to_telegram('Private reviewed result.')
        assert sender._message_queue.empty()


def test_missing_attachment_is_rejected_and_can_be_retried(tmp_path):
    sender = DummyTelegramSender()
    path = tmp_path / 'report.txt'
    assert not sender.send_to_telegram(document_path=str(path))
    assert sender._message_queue.empty()
    path.write_text('Report')
    assert sender.send_to_telegram(document_path=str(path))
    assert sender._message_queue.get_nowait()['document']['filename'] == 'report.txt'


def test_disappearing_screenshot_keeps_text(monkeypatch, tmp_path):
    from PIL import Image

    sender = DummyTelegramSender()
    path = tmp_path / 'evidence.png'
    path.write_bytes(b'image')

    def disappear(*args, **kwargs):
        path.unlink()
        raise FileNotFoundError(path)

    monkeypatch.setattr(Image, 'open', disappear)
    assert sender.send_to_telegram('Here is the result.', screenshot_path=str(path))
    message = sender._message_queue.get_nowait()
    assert message['text'] == 'Here is the result.'
    assert 'screenshot' not in message


@pytest.mark.parametrize(('text', 'caption'), [
    ('Here is the result.', 'Here is the result.'),
    ('Screenshot captured and evidence stored in Decisions.', None),
])
def test_screenshot_encodes_as_webp_even_if_caption_is_suppressed(tmp_path, text, caption):
    import base64
    import io

    from PIL import Image

    sender = DummyTelegramSender()
    path = tmp_path / 'evidence.png'
    Image.new('RGB', (32, 24), 'white').save(path)
    assert sender.send_to_telegram(text, screenshot_path=str(path))
    message = sender._message_queue.get_nowait()
    assert message.get('caption') == caption
    assert message.get('text') == caption
    assert message['screenshot']['mime_type'] == 'image/webp'
    with Image.open(io.BytesIO(base64.b64decode(message['screenshot']['data']))) as image:
        assert image.format == 'WEBP'
        assert image.size == (32, 24)
