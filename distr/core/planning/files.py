"""Recoverable file projections. The database revision and pending write commit together."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import tempfile
from distr.core.db.workflow import PlanFileWrite


def content_hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def managed_target(workspace, item):
    root = Path(workspace.root_path).expanduser().resolve()
    target = Path(item.file_path).expanduser().resolve()
    if root not in target.parents:
        raise ValueError("The plan item path is outside the managed planning folder. Resolve the project link before saving.")
    return target


def disk_hash(target):
    return content_hash(target.read_text(encoding="utf-8")) if target.exists() else None


def stage_write(db, workspace, item, previous_hash):
    target = managed_target(workspace, item)
    if db.get(PlanFileWrite, item.id):
        raise ValueError("A committed plan revision is waiting for file synchronization. Reopen the workspace to retry.")
    actual = disk_hash(target)
    if actual != previous_hash:
        raise ValueError("This planning file changed outside Decisions AI. Use Review file changes to reconcile it before saving.")
    item.content_hash = content_hash(item.content or "")
    db.add(PlanFileWrite(item_id=item.id, target_path=str(target), previous_hash=actual, desired_hash=item.content_hash))


def finish_write(db, workspace, item):
    pending = db.get(PlanFileWrite, item.id)
    if not pending:
        return ""
    target = managed_target(workspace, item)
    if str(target) != pending.target_path or content_hash(item.content or "") != pending.desired_hash:
        return "Pending file projection no longer matches this item. Review file changes before saving."
    actual = disk_hash(target)
    if actual == pending.desired_hash:
        db.delete(pending)
        return ""
    if actual != pending.previous_hash:
        return "The file changed after this revision committed. Both versions are retained. Review file changes."
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, prefix=".decisions-plan-", delete=False) as stream:
            staged = Path(stream.name)
            stream.write(item.content or "")
            stream.flush()
            os.fsync(stream.fileno())
        # Recheck after staging so an intervening external edit is not silently replaced.
        if disk_hash(target) != actual:
            return "The file changed during synchronization. Review file changes."
        os.replace(staged, target)
        db.delete(pending)
        return ""
    finally:
        if staged and staged.exists():
            staged.unlink()
