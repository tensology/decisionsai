"""Development image attachments must reach Pi as real image input (``@file``)."""
from pathlib import Path


def _task(tmp_path, **kw):
    from distr.core.project_cli_backends.base import ProjectTask
    kw.setdefault("folder", str(tmp_path))
    return ProjectTask(project_id=1, project_name="P", instruction="explain the screenshot", **kw)


def test_inline_image_paths_keeps_only_existing_supported_images(tmp_path):
    from distr.core.turn_runtime.cli_harness import inline_image_paths
    jpg = tmp_path / "shot.jpg"; jpg.write_bytes(b"\xff\xd8\xff")
    pdf = tmp_path / "doc.pdf"; pdf.write_bytes(b"%PDF")
    rows = [{"path": str(jpg), "mime_type": "image/jpeg"}, {"path": str(pdf), "mime_type": "application/pdf"},
            {"path": str(tmp_path / "missing.png"), "mime_type": "image/png"}, {"path": "relative.jpg", "mime_type": "image/jpeg"},
            {"path": str(jpg), "mime_type": "image/jpeg"}]
    assert inline_image_paths(rows) == [str(jpg)]


def test_development_pi_command_attaches_images_before_instruction(tmp_path):
    from distr.core.project_cli_backends.registry import _pi_print_command
    jpg = tmp_path / "shot.jpg"; jpg.write_bytes(b"\xff\xd8\xff")
    cmd = _pi_print_command("pi", _task(tmp_path, origin="development", model="deepseek-v4.1-flash:cloud",
                                        adapter_options={"model_provider": "ollama", "development_image_paths": [str(jpg), "relative.jpg"]}))
    assert cmd[-1] == "explain the screenshot"
    assert cmd[-2] == "@" + str(jpg)
    assert "@relative.jpg" not in cmd


def test_non_development_pi_command_never_gets_image_args(tmp_path):
    from distr.core.project_cli_backends.registry import _pi_print_command
    jpg = tmp_path / "shot.jpg"; jpg.write_bytes(b"\xff\xd8\xff")
    cmd = _pi_print_command("pi", _task(tmp_path, origin="workflow", adapter_options={"development_image_paths": [str(jpg)]}))
    assert not any(str(part).startswith("@") for part in cmd)
