from distr.core.workspace_memory import paths
from distr.core.workspace_memory.lifecycle import hook_remove_workspace


def test_deleted_entity_memory_cannot_leak_into_reused_id(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "WORKSPACES_ROOT", tmp_path / "workspaces")
    stale = paths.companion_root("tickets", 41)
    stale.mkdir(parents=True)
    (stale / "handoff.md").write_text("private context from deleted ticket")

    assert hook_remove_workspace("tickets", 41) is True
    assert not stale.exists()

    recreated = paths.companion_root("tickets", 41)
    recreated.mkdir(parents=True)
    assert not (recreated / "handoff.md").exists()


def test_workspace_removal_is_scoped_to_exact_entity(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "WORKSPACES_ROOT", tmp_path / "workspaces")
    target = paths.companion_root("workflows", 7)
    sibling = paths.companion_root("workflows", 8)
    target.mkdir(parents=True)
    sibling.mkdir(parents=True)
    (target / "memory.txt").write_text("remove")
    (sibling / "memory.txt").write_text("preserve")

    assert hook_remove_workspace("workflows", 7) is True
    assert not target.exists()
    assert (sibling / "memory.txt").read_text() == "preserve"
