"""Retry local Telegram receipt writes across database outages and restarts."""

from collections import deque
from functools import partial
import json
import logging
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from time import time_ns
from typing import Literal
from uuid import uuid4

from sqlalchemy.exc import OperationalError

logger = logging.getLogger(__name__)
_lock = Lock()
_pending = deque()
CHECKPOINT_RATE_LIMIT_MESSAGE = "Telegram relay rate limit blocked this delivery attempt."
_ReceiptEntry = (
    tuple[str, int, int]
    | tuple[str, int, int, int | None]
    | tuple[str, int, int, int | None, bool]
    | tuple[Literal["proposal-rate-limit"], str, float]
    | tuple[Literal["proposal-socket-refused"], str, str, str]
    | tuple[Literal["proposal-unconfirmed"], str]
    | tuple[Literal["checkpoint-error"], str, int | None, bool]
    | tuple[Literal["closing-uncertain"], str]
    | tuple[Literal["closing-sent"], str, int, int]
)


def checkpoint_delivery_error_message(*, rate_limited: bool) -> str:
    if rate_limited:
        return CHECKPOINT_RATE_LIMIT_MESSAGE
    return "Telegram delivery could not be confirmed."


def _journal_path() -> Path:
    from distr.core.db import db_path

    database = Path(db_path)
    return database.with_name(f"{database.name}.telegram-receipts")


def _journal_receipt(entry: _ReceiptEntry) -> Path | None:
    folder = _journal_path()
    target = folder / f"{time_ns():020d}-{uuid4().hex}.json"
    temporary = None
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=folder, suffix=".tmp", delete=False, mode="w", encoding="utf-8") as stream:
            temporary = Path(stream.name)
            json.dump(entry, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
        return target
    except OSError:
        logger.warning("Could not journal the Telegram receipt locally.", exc_info=True)
        return None
    finally:
        if temporary is not None:
            _discard_journal_entry(temporary)


def _discard_journal_entry(path: Path | None) -> None:
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove a processed Telegram receipt journal entry.", exc_info=True)


def _read_journaled_receipt(path: Path):
    from distr.core.incoming.telegram_updates import (
        _recipient_id, defer_rate_limited_delivery, mark_delivery_not_sent, record_delivery,
    )
    from distr.core.workflow.interactions import (
        parse_telegram_checkpoint_receipt_id, record_telegram_delivery, record_telegram_delivery_error,
    )

    entry = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entry, list) or len(entry) not in (2, 3, 4, 5):
        raise ValueError("Invalid Telegram receipt")
    if len(entry) == 2 and entry[0] == "closing-uncertain":
        from distr.core.workflow.telegram_reply_receipts import confirm

        return partial(confirm, entry[1], uncertain=True)
    if len(entry) == 4 and entry[0] == "closing-sent":
        from distr.core.workflow.telegram_reply_receipts import confirm

        return partial(confirm, entry[1], telegram_chat_id=entry[2], telegram_message_id=entry[3])
    if len(entry) == 2:
        kind, token = entry
        if kind == "proposal-unconfirmed" and isinstance(token, str) and token.startswith("proposal-update:"):
            return partial(record_delivery, token, flush=False)
        raise ValueError("Invalid Telegram unconfirmed receipt")
    if len(entry) == 3 and entry[0] == "proposal-rate-limit":
        _, token, received_at = entry
        if (isinstance(token, str) and token.startswith("proposal-update:")
                and type(received_at) in (int, float) and 0 <= received_at < 2**53):
            return partial(defer_rate_limited_delivery, token, received_at=received_at)
        raise ValueError("Invalid Telegram rate-limit receipt")
    if len(entry) == 4 and entry[0] == "proposal-socket-refused":
        _, token, queue_id, attempt_id = entry
        if (all(isinstance(value, str) and value for value in (token, queue_id, attempt_id))
                and token.startswith("proposal-update:")):
            return partial(mark_delivery_not_sent, token, queue_id=queue_id, attempt_id=attempt_id)
        raise ValueError("Invalid Telegram socket refusal receipt")
    if len(entry) == 4 and entry[0] == "checkpoint-error":
        _, token, attempt, rate_limited = entry
        if (isinstance(token, str) and parse_telegram_checkpoint_receipt_id(token) == (token, None)
                and (attempt is None or (type(attempt) is int and _recipient_id(attempt) is not None))
                and type(rate_limited) is bool):
            return partial(record_telegram_delivery_error, token=token, attempt=attempt,
                           error=checkpoint_delivery_error_message(rate_limited=rate_limited))
        raise ValueError("Invalid Telegram checkpoint error receipt")
    token, message_id, chat_id = entry[:3]
    if (not isinstance(token, str) or type(message_id) is not int or type(chat_id) is not int
            or _recipient_id(message_id) is None or _recipient_id(chat_id) is None):
        raise ValueError("Invalid Telegram acknowledgment")
    if len(entry) == 3 and token.startswith("proposal-update:"):
        return partial(record_delivery, token, telegram_message_id=message_id, telegram_chat_id=chat_id, flush=False)
    if len(entry) >= 4 and parse_telegram_checkpoint_receipt_id(token) == (token, None):
        attempt = entry[3]
        controls_sent = True if len(entry) == 4 else entry[4]
        if type(controls_sent) is bool and (attempt is None or (type(attempt) is int and _recipient_id(attempt) is not None)):
            return partial(record_telegram_delivery, token=token, telegram_message_id=message_id,
                           telegram_chat_id=chat_id, reply_markup_sent=controls_sent, attempt=attempt)
    raise ValueError("Invalid Telegram acknowledgment")


def _load_journaled_receipts() -> None:
    already_pending = {path for _, path in _pending}
    try:
        paths = sorted(_journal_path().glob("*.json"))
    except OSError:
        logger.warning("Could not list the local Telegram receipt journal.", exc_info=True)
        return
    for path in paths:
        if path in already_pending:
            continue
        try:
            write = _read_journaled_receipt(path)
        except FileNotFoundError:
            continue
        except (OSError, TypeError, ValueError):
            logger.warning("Could not read a Telegram receipt from the local journal.")
            continue
        _pending.append((write, path))


def persist_or_defer_receipt_write(
    write, *, journal_entry: _ReceiptEntry | None = None,
):
    """Return the write result, or None when a later sync must retry it."""
    with _lock:
        _load_journaled_receipts()
        path = _journal_receipt(journal_entry) if journal_entry is not None else None
        if not _pending:
            try:
                result = write()
            except OperationalError:
                pass
            else:
                _discard_journal_entry(path)
                return result
        # A newer reply must not overtake an earlier one during an outage.
        _pending.append((write, path))
        return None


def flush_pending_receipt_writes() -> bool:
    with _lock:
        _load_journaled_receipts()
        while _pending:
            write, path = _pending[0]
            try:
                write()
            except OperationalError:
                return False
            _pending.popleft()
            _discard_journal_entry(path)
        return True
