import json
from pathlib import Path

from distr.core.agent.tools.files import convert_document as convert_document_module
from distr.core.agent.tools.files.convert_document import ConvertDocumentTool


def _write_dropped_files_context(home_dir: Path, path: Path, include_doc_bucket: bool = True) -> None:
    storage_dir = home_dir / ".decisions" / "dropped_files"
    storage_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "document_files": [str(path)] if include_doc_bucket else [],
        "files": [str(path)],
        "file_timestamps": {str(path): 1_700_000_000},
        "chat_files_index": {},
    }
    (storage_dir / "current_files.json").write_text(json.dumps(payload))


def test_convert_document_uses_recent_dropped_file_if_missing_input(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    md_file = tmp_path / "notes.md"
    md_file.write_text("# Notes\n\n- one\n- two\n")
    _write_dropped_files_context(home, md_file)

    monkeypatch.setattr(
        convert_document_module,
        "_md_to_html",
        lambda _text: "<html>ok</html>",
    )
    monkeypatch.setattr(
        convert_document_module,
        "_html_to_pdf",
        lambda html, output_path, wait_for_mermaid=False: Path(output_path).write_text("pdf"),
    )

    tool = ConvertDocumentTool()
    result = tool._run(output_format="pdf")
    assert "Converted to PDF" in result


def test_convert_document_errors_when_no_input_path_and_no_recent_document(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    tool = ConvertDocumentTool()
    result = tool._run(output_format="pdf")
    assert "Error: input_path is required for convert_document." in result
