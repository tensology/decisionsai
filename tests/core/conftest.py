"""Isolate the additive durable proposal store between Incoming regressions."""
import pytest


@pytest.fixture(autouse=True)
def isolated_incoming_snapshots(request):
    if 'incoming' not in request.node.path.name:
        yield
        return
    from distr.core.db import IncomingProposalSnapshot, get_session
    with get_session() as db:
        db.query(IncomingProposalSnapshot).delete()
        db.commit()
    yield
