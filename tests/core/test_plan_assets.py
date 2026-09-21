"""Real attachment storage and scope checks, isolated from project data."""
import pytest
import io
import shutil

from distr.core.db.projects import Project, ProjectFile
from distr.core.planning import assets, service
from tests.core.test_planning_workspace import plan_workspace_db


@pytest.fixture
def asset_store(plan_workspace_db, monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "get_session", service.get_session)
    monkeypatch.setattr(assets, "UPLOAD_ROOT", tmp_path / "uploads")
    workspace = service.ensure_workspace(board_key="decisions:9", board_provider="decisions",
                                         board_name="Plan", project_id=plan_workspace_db["project_id"])
    return workspace["id"], plan_workspace_db


def test_uploaded_source_is_stored_and_read_in_project_scope(asset_store):
    wid, project = asset_store
    item = assets.add_asset(wid, name="../../requirements.md", data=b"Support multiple saved addresses.")
    assert item["name"] == "requirements.md"
    path, metadata = assets.asset_path(wid, item["id"])
    assert assets.UPLOAD_ROOT in path.parents
    assert metadata == item
    assert assets.list_assets(wid) == [item]
    assert assets.context_assets(wid, [item["id"]])[-1]["text"] == "Support multiple saved addresses."


def test_foreign_project_attachment_is_not_readable(asset_store):
    wid, project = asset_store
    item = assets.add_asset(wid, name="private.md", data=b"Private project source")
    with service.get_session() as db:
        other = Project(name="Other")
        db.add(other)
        db.commit()
        other_id = other.id
    other_workspace = service.ensure_workspace(board_key="decisions:10", board_provider="decisions",
                                               board_name="Other", project_id=other_id)
    with pytest.raises(LookupError, match="does not belong"):
        assets.context_assets(other_workspace["id"], [item["id"]])


def test_source_symlink_cannot_escape_project(asset_store, tmp_path):
    wid, project = asset_store
    outside = tmp_path / "private.md"
    outside.write_text("Not project source")
    link = project["folder"] / "escape.md"
    link.symlink_to(outside)
    with service.get_session() as db:
        row = ProjectFile(project_id=project["project_id"], filename="escape.md", file_path=str(link))
        db.add(row)
        db.commit()
        ident = row.id
    with pytest.raises(ValueError, match="unavailable"):
        assets.asset_path(wid, ident)


@pytest.mark.parametrize("name,data", [("script.exe", b"binary"), ("empty.md", b""), ("fake.pdf", b"not pdf")])
def test_invalid_upload_leaves_no_files_or_rows(asset_store, name, data):
    wid, _ = asset_store
    with pytest.raises(ValueError):
        assets.add_asset(wid, name=name, data=data)
    assert assets.list_assets(wid) == []
    assert not assets.UPLOAD_ROOT.exists()


def test_eight_retained_sources_fit_reader_contract(asset_store):
    wid, _ = asset_store
    ids = [assets.add_asset(wid, name=f"source-{i}.md", data=f"Source {i}".encode())["id"] for i in range(8)]
    assert len(assets.context_assets(wid, ids)) == 16
    with pytest.raises(ValueError, match="eight"):
        assets.context_assets(wid, ids + [ids[0]])


@pytest.mark.skipif(not shutil.which("pdftoppm"), reason="Poppler is required for the real rendering test")
def test_pdf_preview_renders_requested_page_and_rejects_invalid_page(asset_store):
    from pypdf import PdfWriter
    from PIL import Image
    wid, _ = asset_store
    pdf = PdfWriter()
    pdf.add_blank_page(width=300, height=400)
    pdf.add_blank_page(width=400, height=300)
    data = io.BytesIO()
    pdf.write(data)
    asset = assets.add_asset(wid, name="two-pages.pdf", data=data.getvalue())
    assert assets.pdf_preview_info(wid, asset["id"]) == {"page_count": 2}
    portrait = Image.open(io.BytesIO(assets.render_pdf_page(wid, asset["id"], 1)))
    landscape = Image.open(io.BytesIO(assets.render_pdf_page(wid, asset["id"], 2)))
    assert portrait.height > portrait.width
    assert landscape.width > landscape.height
    assert max(portrait.size) <= 1600
    with pytest.raises(ValueError, match="outside"):
        assets.render_pdf_page(wid, asset["id"], 3)


def test_pdf_preview_rejects_non_pdf(asset_store):
    wid, _ = asset_store
    asset = assets.add_asset(wid, name="notes.md", data=b"Text")
    with pytest.raises(ValueError, match="not a PDF"):
        assets.pdf_preview_info(wid, asset["id"])
