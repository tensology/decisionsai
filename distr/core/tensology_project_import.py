"""Import a Tensology starter into the user's local DecisionsAI projects."""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from sqlalchemy import func

from distr.core.mempalace.paths import bundled_seed_corpus_pack

MAX_MANIFEST_BYTES = 8192
_IMPORT_LOCK = threading.Lock()
_PREVIEW_PATH = "/dashboard/trial-store"
_PREVIEW_TITLE = "Tensology preview"
_RECEIPT_NAME = ".tensology-project"
_MANIFEST_FIELDS = {"version", "starter", "project_id", "name", "preview_url"}


class StarterImportError(ValueError):
    pass


@dataclass(frozen=True)
class StarterManifest:
    project_id: UUID
    name: str
    preview_url: str


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields = dict(pairs)
    if len(pairs) != len(fields):
        raise StarterImportError("The project file contains repeated fields.")
    return fields


def parse_manifest(payload: bytes) -> StarterManifest:
    if len(payload) > MAX_MANIFEST_BYTES:
        raise StarterImportError("Choose a Tensology project file smaller than 8 KB.")
    try:
        fields = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_fields)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise StarterImportError("Choose the project file downloaded from Tensology.") from exc
    if (
        not isinstance(fields, dict) or fields.keys() != _MANIFEST_FIELDS
        or type(fields["version"]) is not int or fields["version"] != 1
        or fields["starter"] != "ecommerce"
    ):
        raise StarterImportError("This version of DecisionsAI supports the Tensology ecommerce project file.")
    try:
        project_id = UUID(fields["project_id"])
    except (AttributeError, TypeError, ValueError) as exc:
        raise StarterImportError("The project file has an invalid project ID.") from exc
    if fields["project_id"] != str(project_id):
        raise StarterImportError("The project file has an invalid project ID.")
    name = fields["name"]
    if not isinstance(name, str) or not name.isprintable() or not 1 <= len(name.strip()) <= 120:
        raise StarterImportError("The project file needs a name between 1 and 120 characters.")
    url = fields["preview_url"]
    if not isinstance(url, str) or any(char.isspace() for char in url):
        raise StarterImportError("The project file has an invalid preview URL.")
    try:
        preview = urlsplit(url)
        query = parse_qs(preview.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise StarterImportError("The project file has an invalid preview URL.") from exc
    if (
        preview.scheme != "https" or preview.netloc not in {"tensology.com", "www.tensology.com"}
        or preview.path != _PREVIEW_PATH or "#" in url
        or query != {"preview": ["1"], "project": [str(project_id)]}
    ):
        raise StarterImportError("The preview URL must match this Tensology project.")
    canonical_preview = f"https://www.tensology.com{_PREVIEW_PATH}?preview=1&project={project_id}"
    return StarterManifest(project_id, name.strip(), canonical_preview)


def _projects_dir() -> Path:
    return Path.home() / "DecisionsAI Projects"


def _ignore_local_files(folder: str, names: list[str]) -> set[str]:
    transient = {".git", ".venv", "venv", "__pycache__", "node_modules", ".DS_Store", "db.sqlite3", ".pytest_cache"}
    return {
        name for name in names
        if name in transient
        or (name.startswith(".env") and name != ".env.example")
        or (Path(folder) / name).is_symlink()
    }


def _copy_starter(target: Path, project_id: UUID) -> None:
    if target.exists() or target.is_symlink():
        receipt = target / _RECEIPT_NAME
        if (
            not target.is_symlink()
            and not receipt.is_symlink()
            and receipt.is_file()
            and receipt.read_text(encoding="utf-8") == str(project_id)
            and (target / "README.md").is_file()
            and (target / "backend" / "manage.py").is_file()
            and (target / "frontend" / "package.json").is_file()
        ):
            return
        raise StarterImportError("That local folder already exists. Move it aside or choose a different project.")
    pack = bundled_seed_corpus_pack()
    source = pack / "domains" / "ecommerce" / "white-label-starter" if pack else None
    if source is None or not (source / "README.md").is_file():
        raise StarterImportError("The ecommerce starter is missing. Update DecisionsAI and try again.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".starter-", dir=target.parent) as staging:
        copy = Path(staging) / "project"
        shutil.copytree(source, copy, ignore=_ignore_local_files)
        (copy / _RECEIPT_NAME).write_text(str(project_id), encoding="utf-8")
        # A retry may find a complete copy if registration was interrupted.
        copy.rename(target)


def import_starter(manifest: StarterManifest) -> tuple[int, bool]:
    from distr.core.db import get_session
    from distr.core.db.projects import Project, ProjectContextItem
    from distr.core.kanban.lifecycle import ensure_project_boards

    folder = _projects_dir() / f"tensology-{manifest.project_id}"
    with _IMPORT_LOCK, get_session() as session:
        existing = session.query(Project).outerjoin(Project.context_items).filter(
            Project.deleted_at.is_(None),
            (Project.folder_location == str(folder)) | (
                (ProjectContextItem.title == _PREVIEW_TITLE) & (ProjectContextItem.content == manifest.preview_url)
            ),
        ).first()
        if existing is not None:
            return existing.id, False
        _copy_starter(folder, manifest.project_id)
        last_position = session.query(func.max(Project.position)).scalar()
        project = Project(
            name=manifest.name,
            description=f"Tensology ecommerce starter\n\nPrivate preview: {manifest.preview_url}",
            notes="",
            folder_location=str(folder),
            additional_trigger_words="[]",
            coding_backend="codex",
            position=0 if last_position is None else last_position + 1,
        )
        project.context_items.append(ProjectContextItem(title=_PREVIEW_TITLE, content=manifest.preview_url))
        session.add(project)
        session.flush()
        ensure_project_boards(session)
        project_id = project.id
        session.commit()
    return project_id, True
