import json

import distr.core.agent.tools.media.file_converter as file_converter_module
from distr.core.agent.tools.media.file_converter import FileConverterTool


class StubChatManager:
    def __init__(self, chat_id):
        self.chat_id = chat_id

    def get_current_chat(self):
        return self.chat_id


def test_recent_files_prefers_active_chat_latest_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    storage_dir = tmp_path / ".decisions" / "dropped_files"
    storage_dir.mkdir(parents=True)

    old_file = tmp_path / "old-project" / "old.png"
    latest_folder = tmp_path / "latest-drop"
    latest_file = latest_folder / "menu.png"
    old_file.parent.mkdir()
    latest_folder.mkdir()
    old_file.write_bytes(b"old")
    latest_file.write_bytes(b"latest")

    (storage_dir / "current_files.json").write_text(
        json.dumps(
            {
                "files": [str(old_file), str(latest_file)],
                "other_files": [str(old_file), str(latest_file)],
                "dropped_folders": [str(latest_folder)],
                "file_timestamps": {str(old_file): 1, str(latest_file): 2},
                "folder_timestamps": {str(latest_folder): 2},
                "chat_files_index": {
                    "7": {
                        "files": [str(old_file), str(latest_file)],
                        "other_files": [str(old_file), str(latest_file)],
                        "dropped_folders": [str(latest_folder)],
                    }
                },
            }
        )
    )

    tool = FileConverterTool(chat_manager=StubChatManager(7))

    assert tool._find_recent_files(multiple=True) == [str(latest_file)]


def test_bulk_threading_uses_progress_callback_to_avoid_per_file_notifications(tmp_path, monkeypatch):
    files = []
    for name in ("one.png", "two.png"):
        path = tmp_path / name
        path.write_bytes(b"image")
        files.append(str(path))

    callback_flags = []

    def fake_worker(file_path, output_path, target_format, is_video, is_image, progress_callback, chat_manager, chat_id):
        callback_flags.append(progress_callback is not None)
        return True, ""

    monkeypatch.setattr(file_converter_module, "_convert_worker_thread", fake_worker)
    tool = FileConverterTool()

    result = tool._convert_multiple_files_threading(files, "webp", chat_id=None)

    assert callback_flags == [True, True]
    assert result == "Converted 2 of 2 file(s) to webp."
