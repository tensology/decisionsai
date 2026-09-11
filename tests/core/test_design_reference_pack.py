from __future__ import annotations

def test_merge_design_reference_skips_generic_work(tmp_path):
    from distr.core.design_reference_pack import merge_design_reference_pre_chain

    chain = merge_design_reference_pre_chain(
        ["decisions-harness-stack", "tdd-workflow"],
        project_folder=str(tmp_path),
    )
    assert chain[0] == "decisions-harness-stack"
    assert "decisions-design-references" not in chain
    assert "tdd-workflow" in chain


def test_merge_design_reference_ui_project(tmp_path, monkeypatch):
    from distr.core.design_reference_pack import merge_design_reference_pre_chain

    monkeypatch.setattr("distr.core.design_reference_pack.impeccable_skill_available", lambda: True)

    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    chain = merge_design_reference_pre_chain(
        ["decisions-harness-stack", "ui-ideation"],
        project_folder=str(tmp_path),
    )
    assert chain[1:5] == [
        "decisions-ui-ideation",
        "decisions-design-references",
        "impeccable",
        "frontend-design-direction",
    ]


def test_merge_design_reference_skips_missing_impeccable(tmp_path, monkeypatch):
    from distr.core.design_reference_pack import merge_design_reference_pre_chain

    monkeypatch.setattr("distr.core.design_reference_pack.impeccable_skill_available", lambda: False)
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    chain = merge_design_reference_pre_chain(
        ["decisions-harness-stack", "ui-ideation"],
        project_folder=str(tmp_path),
    )

    assert "impeccable" not in chain


def test_design_reference_bootstrap_writes_mcp_script(tmp_path, monkeypatch):
    from distr.core.design_reference_pack import ensure_design_reference_setup

    monkeypatch.setattr(
        "distr.core.design_reference_pack.detected_harnesses",
        lambda: {"codex": True, "cursor": False, "claude": False, "pi": False},
    )

    result = ensure_design_reference_setup(home=tmp_path, run_full=False)
    assert result["status"] == "configured"
    script = tmp_path / ".decisions" / "harness" / "mcp-setup-design.sh"
    assert script.is_file()
    assert "api.mobbin.com/mcp" in script.read_text(encoding="utf-8")
    assert result["mcp_setup_script"] == str(script)
    assert result["refero_skill_install"].startswith("npx skills add")
