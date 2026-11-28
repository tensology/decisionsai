"""A Telegram button tap is acted on once, including after a relay replay or an app restart."""
import json
import subprocess
import sys
import time

from distr.core.integrations.telegram import seen_callbacks
from core.test_telegram_checkpoint_scope import checkpoint, intake  # noqa: F401  (fixtures)
from .test_telegram_delivery_stability import DummyTelegramMessages


def _tap(manager, callback_id, data="legacy:stop"):
    manager._handle_telegram_message({"data": {
        "message_id": callback_id, "chat_id": 123, "from": {"id": 123}, "callback_data": data}})


def test_claim_is_true_once_and_false_for_a_repeat():
    assert seen_callbacks.claim_callback("cb-1") is True
    assert seen_callbacks.claim_callback("cb-1") is False
    assert seen_callbacks.claim_callback("cb-2") is True
    assert seen_callbacks.claim_callback("") is True  # no id: nothing to dedupe on


def test_memory_survives_a_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    assert seen_callbacks.claim_callback("cb-restart") is True
    # A fresh interpreter has an empty in-memory state; the file is all it has.
    code = ("from distr.core.integrations.telegram import seen_callbacks as s;"
            "print(s.claim_callback('cb-restart'), s.claim_callback('cb-new'))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**__import__("os").environ, "DECISIONS_DB_DIR": str(tmp_path)},
                         cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]), timeout=60)
    assert out.stdout.split()[-2:] == ["False", "True"], out.stderr[-500:]


def test_entries_expire_and_the_file_is_capped(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    path = tmp_path / "telegram_seen_callbacks.json"
    path.write_text(json.dumps({"old": time.time() - seen_callbacks.SEEN_TTL_S - 5}))
    assert seen_callbacks.claim_callback("old") is True
    for i in range(seen_callbacks.SEEN_CAP + 20):
        seen_callbacks.claim_callback(f"n{i}")
    assert len(json.loads(path.read_text())) == seen_callbacks.SEEN_CAP


def test_repeated_tap_is_handled_once_through_the_manager(checkpoint):  # noqa: F811
    manager = DummyTelegramMessages()
    _tap(manager, "cb-same")
    first = list(manager.sent)
    assert first  # the first tap was processed
    _tap(manager, "cb-same")
    assert manager.sent == first  # the repeat did nothing
    _tap(manager, "cb-other")
    assert len(manager.sent) == 2 * len(first)  # a different tap is still handled


def test_callback_survives_clearing_the_in_memory_caches(checkpoint):  # noqa: F811
    manager = DummyTelegramMessages()
    _tap(manager, "cb-mem")
    sent = list(manager.sent)
    manager._processed_message_ids.clear()
    manager._processed_message_hashes.clear()
    _tap(manager, "cb-mem")
    assert manager.sent == sent
