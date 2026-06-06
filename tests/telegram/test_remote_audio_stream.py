import base64

from distr.core.integrations.telegram.remote_audio_stream import (
    REMOTE_AUDIO_MIME,
    iter_remote_audio_stream_messages,
    remote_audio_stopped_message,
)


def test_remote_audio_stream_messages_are_ogg_and_chunked(tmp_path):
    audio = tmp_path / "reply.ogg"
    audio.write_bytes(b"abcdefghi")

    messages = list(
        iter_remote_audio_stream_messages(
            request_id="req-1",
            audio_path=audio,
            chunk_size=4,
        )
    )

    assert messages[0]["type"] == "remote_agent_audio_start"
    assert messages[0]["data"]["mime_type"] == REMOTE_AUDIO_MIME
    assert messages[0]["data"]["size_bytes"] == 9

    chunks = [msg for msg in messages if msg["type"] == "remote_agent_audio_chunk"]
    assert [chunk["data"]["seq"] for chunk in chunks] == [0, 1, 2]
    assert b"".join(base64.b64decode(chunk["data"]["data"]) for chunk in chunks) == b"abcdefghi"

    assert messages[-1]["type"] == "remote_agent_audio_end"
    assert messages[-1]["data"]["chunks"] == 3
    assert messages[-1]["data"]["size_bytes"] == 9


def test_remote_audio_stopped_message_is_correlated():
    message = remote_audio_stopped_message("req-2", reason="user_stop")
    assert message == {
        "type": "remote_agent_audio_stopped",
        "request_id": "req-2",
        "data": {"reason": "user_stop"},
    }
