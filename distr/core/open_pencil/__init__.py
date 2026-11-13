"""OpenPencil read-side bridge (reference dependency, pinned; no editor embed)."""

from distr.core.open_pencil.gaps import find_gaps
from distr.core.open_pencil.pin import (
    PIN_CLI_VERSION,
    PIN_SHA,
    PIN_TAG,
    OpenPencilBridgeError,
    OpenPencilCliMissing,
    OpenPencilInvalidDocument,
    OpenPencilPinMismatch,
    check_pin,
)
from distr.core.open_pencil.reader import inspect_document, validate_document_path
from distr.core.open_pencil.tickets import create_tickets_from_gaps, draft_ticket_specs

__all__ = [
    "PIN_CLI_VERSION", "PIN_SHA", "PIN_TAG",
    "OpenPencilBridgeError", "OpenPencilCliMissing", "OpenPencilInvalidDocument", "OpenPencilPinMismatch",
    "check_pin", "inspect_document", "validate_document_path", "find_gaps",
    "draft_ticket_specs", "create_tickets_from_gaps",
]
