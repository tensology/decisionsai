"""Local backup and reversible reporting corrections for non-ledger projections.

Canonical rows and provider JSONL files are not rewritten. A correction changes
only the report built from a backed-up image. Unknown project and unknown
duration stay unknown. Ticket titles and prompts are not outcomes.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Boolean, Column, DateTime, Integer, String, UniqueConstraint, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base

SCHEMA = "decisions-projection-store-backup/v1"
SOURCES = frozenset({"execution", "automation", "workflow", "pi_local", "codex_local"})
GAP_SECONDS = 20 * 60
PAD_SECONDS = 60
MAX_BACKUP_AGE = timedelta(hours=24)
MAX_PROVIDER_BYTES = 32 * 1024 * 1024

CorrectionBase = declarative_base()


class ProjectionCorrection(CorrectionBase):
    __tablename__ = "projection_corrections"

    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False)
    ref = Column(String, nullable=False)
    original_start = Column(String, nullable=False)
    original_end = Column(String, nullable=False)
    corrected_end = Column(String, nullable=True)
    duration_unknown = Column(Boolean, nullable=False, default=False)
    fingerprint = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    provenance = Column(String, nullable=False)
    backup_id = Column(String, nullable=False)
    provider_path = Column(String, nullable=True)
    provider_root = Column(String, nullable=True)
    source_sha256 = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("source", "ref", "original_start", "original_end", "reason", name="uq_projection_correction"),
    )


class ProjectionStoreError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def body(self) -> dict:
        return {"detail": {"code": self.code, "message": self.message}}


def _sha(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _stamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")


def _parse(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f")


def ensure_correction_schema(get_session) -> None:
    with get_session() as db:
        ProjectionCorrection.__table__.create(bind=db.get_bind(), checkfirst=True)


def _image_row(row, table: str, source: str) -> dict:
    fields = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        fields[column.name] = _stamp(value) if isinstance(value, datetime) else value
    identity = {"table": table, "source": source, "id": fields.get("id"), "fields": fields}
    identity["fingerprint"] = _sha(identity)
    return identity


def _raw_intervals(stamps: list[datetime]) -> list[tuple[datetime, datetime]]:
    intervals = []
    start = prev = None
    for stamp in stamps:
        if start is None:
            start = prev = stamp
            continue
        if (stamp - prev).total_seconds() > GAP_SECONDS:
            intervals.append((start, prev))
            start = stamp
        prev = stamp
    if start is not None and prev is not None:
        intervals.append((start, prev))
    return intervals


def _padded(start: datetime, end: datetime) -> tuple[datetime, datetime, bool]:
    if (end - start).total_seconds() >= PAD_SECONDS:
        return start, end, False
    return start, start + timedelta(seconds=PAD_SECONDS), True


def collect_database_images(get_session, lo: datetime, hi: datetime, sources: set[str]) -> list[dict]:
    images = []
    with get_session() as db:
        if "execution" in sources:
            from distr.core.db.kanban import ProjectExecutionSession
            rows = db.query(ProjectExecutionSession).filter(
                ProjectExecutionSession.started_at < hi,
                ProjectExecutionSession.started_at >= lo - timedelta(hours=24),
            ).all()
            images.extend(_image_row(row, "project_execution_sessions", "execution") for row in rows)
        if "automation" in sources:
            from distr.core.db.automation import AutomationRun
            rows = db.query(AutomationRun).filter(
                AutomationRun.started_at < hi,
                AutomationRun.started_at >= lo - timedelta(hours=24),
            ).all()
            images.extend(_image_row(row, "automation_runs", "automation") for row in rows)
        if "workflow" in sources:
            from distr.core.db.workflow import AutoWorkflowRun
            rows = db.query(AutoWorkflowRun).filter(
                AutoWorkflowRun.started_at < hi,
                AutoWorkflowRun.started_at >= lo - timedelta(hours=24),
            ).all()
            images.extend(_image_row(row, "auto_workflow_runs", "workflow") for row in rows)
    return images


def _provider_bytes(path: Path, root: Path) -> bytes:
    """Read one provider file under its configured root. Rejects symlinks and paths outside that root."""
    if path.is_symlink() or root.is_symlink():
        raise ProjectionStoreError(409, "path_rejected", "provider path is a symlink")
    try:
        resolved = path.resolve(strict=True)
        root_resolved = root.resolve(strict=True)
    except OSError as exc:
        raise ProjectionStoreError(409, "path_rejected", "provider path is not a readable file") from exc
    if not resolved.is_relative_to(root_resolved) or not resolved.is_file():
        raise ProjectionStoreError(409, "path_rejected", "provider path escapes its root")
    data = resolved.read_bytes()
    if len(data) > MAX_PROVIDER_BYTES:
        raise ProjectionStoreError(409, "source_too_large", "provider file exceeds 32MiB")
    return data


def _stamps_from_bytes(data: bytes) -> list[datetime]:
    from distr.core.reports.month import _TS_RE, _parse_ts
    stamps = []
    for line in data.splitlines():
        match = _TS_RE.search(line[:400].decode("utf-8", "replace"))
        if not match:
            continue
        stamp = _parse_ts(match.group(1))
        if stamp is not None:
            stamps.append(stamp)
    return sorted(stamps)


def _live_pad(image: dict, interval: dict) -> dict:
    """Re-read the provider bytes. A display-bound fingerprint is not proof of the current span."""
    data = _provider_bytes(Path(image["provider_path"]), Path(image["provider_root"]))
    digest = hashlib.sha256(data).hexdigest()
    same = digest == image.get("content_sha256")
    live = None
    for raw_start, raw_end in _raw_intervals(_stamps_from_bytes(data)):
        shown_start, shown_end, padded = _padded(raw_start, raw_end)
        if _stamp(shown_start) == interval["displayed_start"] and _stamp(shown_end) == interval["displayed_end"] and padded:
            live = (raw_start, raw_end)
            break
    backup_end = _parse(interval["raw_end"])
    if not same or live is None or live[1] != backup_end:
        return {"ok": False, "digest": digest}
    return {"ok": True, "digest": digest, "raw_start": live[0], "raw_end": live[1]}


def collect_jsonl_images(lo: datetime, hi: datetime, sources: set[str], *, pi_root: Path | None, codex_root: Path | None) -> list[dict]:
    from distr.core.reports.month import _session_activity, _session_files
    roots = {}
    if "pi_local" in sources and pi_root is not None:
        roots["pi_local"] = pi_root
    if "codex_local" in sources and codex_root is not None:
        roots["codex_local"] = codex_root
    images = []
    for source, root in roots.items():
        kind = "pi" if source == "pi_local" else "codex"
        for path in _session_files(root, lo, kind=kind):
            data = _provider_bytes(path, root)
            meta, stamps = _session_activity(path, kind=kind)
            intervals = []
            for raw_start, raw_end in _raw_intervals(stamps):
                shown_start, shown_end, padded = _padded(raw_start, raw_end)
                if shown_end <= lo or shown_start >= hi:
                    continue
                intervals.append({
                    "raw_start": _stamp(raw_start),
                    "raw_end": _stamp(raw_end),
                    "displayed_start": _stamp(shown_start),
                    "displayed_end": _stamp(shown_end),
                    "synthetic_pad": padded,
                    "project_provenance": "unknown",
                })
            if not intervals:
                continue
            resolved = path.resolve()
            record = {
                "table": "jsonl_timestamps",
                "source": source,
                "ref": f"{source}:{meta['id']}",
                "session_id": meta["id"],
                "provider_path": str(resolved),
                "provider_root": str(root.resolve()),
                "content_sha256": hashlib.sha256(data).hexdigest(),
                "byte_length": len(data),
                "content_base64": base64.b64encode(data).decode("ascii"),
                "stamp_count": len(stamps),
                "intervals": intervals,
            }
            record["fingerprint"] = _sha({key: value for key, value in record.items() if key != "fingerprint"})
            images.append(record)
    return images


def collect_correction_images(get_session) -> list[dict]:
    """Full rows of the correction table. This is the store an apply mutates."""
    with get_session() as db:
        if not inspect(db.get_bind()).has_table(ProjectionCorrection.__tablename__):
            return []
        rows = db.query(ProjectionCorrection).all()
    images = []
    for row in rows:
        image = _image_row(row, "projection_corrections", "projection_corrections")
        image["ref"] = f"projection_corrections:{int(row.id)}"
        images.append(image)
    return images


def _backup_dir(root: Path) -> Path:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    return root


def _write_backup(directory: Path, manifest: dict) -> dict:
    body = dict(manifest)
    body.pop("checksum", None)
    body["checksum"] = _sha(body)
    backup_id = body["backup_id"]
    path = _backup_dir(directory) / f"{backup_id}.json"
    if path.is_symlink():
        raise ProjectionStoreError(409, "backup_tampered", "backup path is a symlink")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o600)
    fd = os.open(tmp, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dirfd)
    finally:
        os.close(dirfd)
    return body


def _read_backup(directory: Path, backup_id: str) -> dict:
    if not backup_id or "/" in backup_id or backup_id.startswith("."):
        raise ProjectionStoreError(400, "path_rejected", "backup_id is not a server id")
    path = directory / f"{backup_id}.json"
    if path.is_symlink() or not path.is_file():
        raise ProjectionStoreError(409, "backup_missing", "backup file is missing")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    checksum = loaded.get("checksum")
    check = dict(loaded)
    check.pop("checksum", None)
    if checksum != _sha(check) or loaded.get("schema") != SCHEMA or loaded.get("backup_id") != backup_id:
        raise ProjectionStoreError(409, "backup_tampered", "backup checksum or id does not match")
    return loaded


def public_manifest(manifest: dict) -> dict:
    """Counts, ids, and checksums only. No row images, prompts, or tokens."""
    counts: dict[str, int] = {}
    refs = []
    for row in manifest.get("images") or []:
        counts[row["source"]] = counts.get(row["source"], 0) + 1
        refs.append({
            "source": row["source"],
            "ref": row.get("ref") or f"{row['source']}:{row.get('id')}",
            "fingerprint": row["fingerprint"],
            "content_sha256": row.get("content_sha256"),
            "byte_length": row.get("byte_length"),
        })
    return {
        "schema": manifest["schema"],
        "backup_id": manifest["backup_id"],
        "created_at": manifest["created_at"],
        "checksum": manifest["checksum"],
        "counts": counts,
        "images": len(refs),
        "refs": refs,
    }


def create_backup(get_session, directory: Path, sources: list[str], lo: datetime, hi: datetime, *, pi_root: Path | None = None, codex_root: Path | None = None, now: datetime | None = None) -> dict:
    chosen = set(sources)
    if not chosen or not chosen <= SOURCES:
        raise ProjectionStoreError(400, "invalid_source", "sources must be execution, automation, workflow, pi_local, or codex_local")
    home = Path.home()
    if "pi_local" in chosen and pi_root is None:
        pi_root = home / ".pi" / "agent" / "sessions"
    if "codex_local" in chosen and codex_root is None:
        codex_root = home / ".codex" / "sessions"
    images = collect_database_images(get_session, lo, hi, chosen)
    images.extend(collect_jsonl_images(lo, hi, chosen, pi_root=pi_root, codex_root=codex_root))
    images.extend(collect_correction_images(get_session))
    created = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    backup_id = hashlib.sha256(os.urandom(32)).hexdigest()[:32]
    manifest = {"schema": SCHEMA, "backup_id": backup_id, "created_at": created, "images": images}
    written = _write_backup(directory, manifest)
    return public_manifest(written)


def evidence(get_session, directory: Path, backup_id: str) -> dict:
    manifest = _read_backup(directory, backup_id)
    candidates = []
    for row in manifest["images"]:
        if row["source"] not in {"pi_local", "codex_local"}:
            candidates.append({
                "source": row["source"],
                "ref": row.get("ref") or f"{row['source']}:{row.get('id')}",
                "fingerprint": row["fingerprint"],
                "eligible": False,
                "reason": None,
                "project_id": (row.get("fields") or {}).get("project_id"),
                "project_provenance": "stored_on_row" if (row.get("fields") or {}).get("project_id") else "unknown",
                "outcome": None,
                "timestamp_bounds": {
                    "started_at": (row.get("fields") or {}).get("started_at"),
                    "ended_at": (row.get("fields") or {}).get("completed_at") or (row.get("fields") or {}).get("updated_at"),
                },
            })
            continue
        for interval in row["intervals"]:
            candidates.append({
                "source": row["source"],
                "ref": row["ref"],
                "fingerprint": _sha({"ref": row["ref"], "displayed_start": interval["displayed_start"], "displayed_end": interval["displayed_end"]}),
                "displayed_start": interval["displayed_start"],
                "displayed_end": interval["displayed_end"],
                "raw_start": interval["raw_start"],
                "raw_end": interval["raw_end"],
                "eligible": bool(interval["synthetic_pad"]),
                "reason": "remove_synthetic_pad" if interval["synthetic_pad"] else None,
                "provenance": "collator_min_block_seconds" if interval["synthetic_pad"] else None,
                "project_id": None,
                "project_provenance": "unknown",
                "outcome": None,
            })
    return {"backup_id": backup_id, "checksum": manifest["checksum"], "candidates": candidates, "not_outcome_evidence": True}


def _begin(db) -> None:
    db.execute(text("BEGIN IMMEDIATE"))


def apply_correction(get_session, directory: Path, body: dict, *, now: datetime | None = None) -> dict:
    if body.get("confirm") != "apply-projection-correction":
        raise ProjectionStoreError(409, "confirmation", "confirm must be apply-projection-correction")
    if body.get("reason") != "remove_synthetic_pad" or body.get("provenance") != "collator_min_block_seconds":
        raise ProjectionStoreError(409, "reason", "only remove_synthetic_pad from the collator pad is eligible")
    manifest = _read_backup(directory, str(body.get("backup_id") or ""))
    created = datetime.strptime(manifest["created_at"], "%Y-%m-%dT%H:%M:%S.%f+00:00").replace(tzinfo=UTC)
    if datetime.now(UTC) - created > MAX_BACKUP_AGE:
        raise ProjectionStoreError(409, "backup_stale", "backup is outside 24 hours")
    match = None
    matched_image = None
    for row in manifest["images"]:
        if row.get("ref") != body.get("ref") or row["source"] not in {"pi_local", "codex_local"}:
            continue
        for interval in row.get("intervals") or []:
            fingerprint = _sha({"ref": row["ref"], "displayed_start": interval["displayed_start"], "displayed_end": interval["displayed_end"]})
            if fingerprint == body.get("fingerprint") and interval["synthetic_pad"]:
                match = interval
                matched_image = row
                break
    if match is None or matched_image is None:
        raise ProjectionStoreError(409, "fingerprint_mismatch", "backup has no padded interval with that fingerprint")
    try:
        live = _live_pad(matched_image, match)
    except ProjectionStoreError:
        live = {"ok": False}
    if not live.get("ok"):
        return {"applied": False, "idempotent": False, "reason": "source_changed", "correction_id": None}
    raw_span = (live["raw_end"] - live["raw_start"]).total_seconds()
    unknown = raw_span < 1
    ensure_correction_schema(get_session)
    with get_session() as db:
        _begin(db)
        existing = (
            db.query(ProjectionCorrection)
            .filter(
                ProjectionCorrection.source == body["source"],
                ProjectionCorrection.ref == body["ref"],
                ProjectionCorrection.original_start == match["displayed_start"],
                ProjectionCorrection.original_end == match["displayed_end"],
                ProjectionCorrection.reason == "remove_synthetic_pad",
            )
            .one_or_none()
        )
        if existing is not None:
            if existing.fingerprint != body["fingerprint"] or existing.source_sha256 != live["digest"]:
                return {"applied": False, "idempotent": False, "reason": "source_changed", "correction_id": int(existing.id)}
            return {"applied": True, "idempotent": True, "correction_id": int(existing.id), "duration_unknown": bool(existing.duration_unknown)}
        row = ProjectionCorrection(
            source=body["source"], ref=body["ref"],
            original_start=match["displayed_start"], original_end=match["displayed_end"],
            corrected_end=None if unknown else _stamp(live["raw_end"]),
            duration_unknown=unknown, fingerprint=body["fingerprint"],
            reason="remove_synthetic_pad", provenance="collator_min_block_seconds",
            backup_id=manifest["backup_id"],
            provider_path=matched_image.get("provider_path"),
            provider_root=matched_image.get("provider_root"),
            source_sha256=live["digest"],
            created_at=(now or datetime.now(UTC)).replace(tzinfo=None),
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            existing = (
                db.query(ProjectionCorrection)
                .filter(
                    ProjectionCorrection.source == body["source"],
                    ProjectionCorrection.ref == body["ref"],
                    ProjectionCorrection.original_start == match["displayed_start"],
                    ProjectionCorrection.original_end == match["displayed_end"],
                    ProjectionCorrection.reason == "remove_synthetic_pad",
                )
                .one()
            )
            return {"applied": True, "idempotent": True, "correction_id": int(existing.id), "duration_unknown": bool(existing.duration_unknown)}
        return {"applied": True, "idempotent": False, "correction_id": int(row.id), "duration_unknown": unknown}


def revoke_correction(get_session, body: dict) -> dict:
    if body.get("confirm") != "revoke-projection-correction":
        raise ProjectionStoreError(409, "confirmation", "confirm must be revoke-projection-correction")
    ensure_correction_schema(get_session)
    with get_session() as db:
        _begin(db)
        row = db.get(ProjectionCorrection, int(body.get("correction_id") or 0))
        if row is None:
            raise ProjectionStoreError(404, "not_found", "correction is not stored")
        if row.fingerprint != body.get("fingerprint") or row.backup_id != body.get("backup_id"):
            raise ProjectionStoreError(409, "fingerprint_mismatch", "fingerprint or backup id does not match")
        db.delete(row)
        db.flush()
    return {"revoked": True, "correction_id": int(body["correction_id"])}


def restore_backup(get_session, directory: Path, body: dict) -> dict:
    """Insert missing canonical rows from the local image. Never writes provider JSONL files."""
    if body.get("confirm") != "restore-projection-backup":
        raise ProjectionStoreError(409, "confirmation", "confirm must be restore-projection-backup")
    wanted = body.get("refs")
    if not isinstance(wanted, list) or not wanted:
        raise ProjectionStoreError(400, "invalid_id", "refs must list explicit backup refs")
    manifest = _read_backup(directory, str(body.get("backup_id") or ""))
    by_ref = {}
    for row in manifest["images"]:
        ref = row.get("ref") or f"{row['source']}:{row.get('id')}"
        by_ref[ref] = row
    if any(ref not in by_ref for ref in wanted):
        raise ProjectionStoreError(409, "backup_row_missing", "a requested ref is not in the backup")
    restored, already, absent = [], [], []
    ensure_correction_schema(get_session)
    with get_session() as db:
        _begin(db)
        for ref in wanted:
            image = by_ref[ref]
            if image["table"] == "jsonl_timestamps":
                verdict = _provider_file_state(image)
                if verdict == "absent":
                    absent.append(ref)
                elif verdict == "present":
                    already.append(ref)
                else:
                    raise ProjectionStoreError(409, "collision", f"{ref} bytes differ and the provider file was not written")
                continue
            model = _model_for(image["table"])
            current = db.get(model, int(image["id"]))
            if current is not None:
                current_image = _image_row(current, image["table"], image["source"])
                if current_image["fingerprint"] != image["fingerprint"]:
                    raise ProjectionStoreError(409, "collision", f"{ref} exists with different contents")
                already.append(ref)
                continue
            payload = {}
            for column in model.__table__.columns:
                if column.name not in image["fields"]:
                    continue
                value = image["fields"][column.name]
                if isinstance(value, str) and isinstance(column.type, DateTime):
                    value = _parse(value)
                payload[column.name] = value
            db.add(model(**payload))
            restored.append(ref)
        try:
            db.flush()
        except IntegrityError as exc:
            raise ProjectionStoreError(409, "collision", "restore did not write; the transaction rolls back") from exc
    return {
        "restored": restored,
        "already_present": already,
        "absent": absent,
        "provider_files_written": [],
        "backup_id": manifest["backup_id"],
        "checksum": manifest["checksum"],
    }


def _provider_file_state(image: dict) -> str:
    """present, absent, or different. Never creates or rewrites the provider file."""
    raw_path = image.get("provider_path")
    raw_root = image.get("provider_root")
    if not raw_path or not raw_root or not image.get("content_sha256"):
        return "absent"
    path, root = Path(raw_path), Path(raw_root)
    if path.is_symlink() or not path.is_file():
        return "absent"
    try:
        data = _provider_bytes(path, root)
    except ProjectionStoreError:
        return "absent"
    if hashlib.sha256(data).hexdigest() != image["content_sha256"]:
        return "different"
    return "present"


def _model_for(table: str):
    if table == "project_execution_sessions":
        from distr.core.db.kanban import ProjectExecutionSession
        return ProjectExecutionSession
    if table == "automation_runs":
        from distr.core.db.automation import AutomationRun
        return AutomationRun
    if table == "auto_workflow_runs":
        from distr.core.db.workflow import AutoWorkflowRun
        return AutoWorkflowRun
    if table == "projection_corrections":
        return ProjectionCorrection
    raise ProjectionStoreError(409, "invalid_source", "that table is not a projection canonical store")


def _current_source_still_matches(row: ProjectionCorrection) -> bool:
    if not row.provider_path or not row.provider_root or not row.source_sha256:
        return False
    path, root = Path(row.provider_path), Path(row.provider_root)
    if path.is_symlink() or not path.is_file():
        return False
    try:
        data = _provider_bytes(path, root)
    except ProjectionStoreError:
        return False
    return hashlib.sha256(data).hexdigest() == row.source_sha256


def apply_active_to_local_blocks(blocks: list[dict], get_session) -> list[dict]:
    """Shorten a padded block only while the provider bytes still match the correction. No schema writes."""
    with get_session() as db:
        try:
            if not inspect(db.get_bind()).has_table(ProjectionCorrection.__tablename__):
                return blocks
            rows = db.query(ProjectionCorrection).all()
        except Exception:
            return blocks
    indexed = {(row.source, row.ref, row.original_start, row.original_end): row for row in rows}
    kept = []
    for block in blocks:
        start = block["start"].strftime("%Y-%m-%dT%H:%M:%S.%f") if isinstance(block["start"], datetime) else block["start"]
        end = block["end"].strftime("%Y-%m-%dT%H:%M:%S.%f") if isinstance(block["end"], datetime) else block["end"]
        row = indexed.get((block["source"], block["ref"], start, end))
        if row is None or row.reason != "remove_synthetic_pad" or not _current_source_still_matches(row):
            kept.append(block)
            continue
        if row.duration_unknown:
            continue
        kept.append({**block, "end": _parse(row.corrected_end)})
    return kept


_attached = False


def attach_reporting_corrections(get_session=None, month_module=None) -> None:
    """Apply stored reporting corrections inside the live month collator. month.py is not edited."""
    global _attached
    if month_module is None:
        import distr.core.reports.month as month
    else:
        month = month_module
    if getattr(month.collect_local_sessions, "_projection_wrapped", False):
        return
    original = month.collect_local_sessions

    def wrapped(*args, **kwargs):
        blocks, counts, status = original(*args, **kwargs)
        factory = get_session
        if factory is None:
            from distr.core.reports.service import get_session as factory
        return apply_active_to_local_blocks(blocks, factory), counts, status

    wrapped._projection_wrapped = True
    wrapped._projection_original = original
    month.collect_local_sessions = wrapped
    _attached = True
