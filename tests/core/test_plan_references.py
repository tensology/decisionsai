import pytest

from distr.core.planning.validation import validate_plan_references


def artifact(kind, content, fmt="markdown"):
    return {"item_type": kind, "content": content, "content_format": fmt, "title": kind}


def test_image_references_must_belong_to_project_manifest():
    wire = artifact('wireframe', 'screen Brand\n  image Logo asset=7', 'wire')
    validate_plan_references([wire], asset_ids={'7'})
    with pytest.raises(ValueError, match='asset=7'):
        validate_plan_references([wire], asset_ids={'8'})


@pytest.mark.parametrize('source,require_erd', [('not a diagram', False), ('erDiagram\n Customer { broken }', True), ('flowchart TD\n A --> B', True)])
def test_invalid_mermaid_and_wrong_erd_type_are_rejected(source, require_erd):
    from distr.core.planning.validation import validate_mermaid, PlanValidationError
    with pytest.raises(PlanValidationError):
        validate_mermaid(source, require_erd=require_erd)


def test_inherited_template_bindings_are_checked():
    wire = artifact("wireframe", 'template Shell\n  input "Name" binding=Customer.name\nscreen "Customers" uses=Shell', "wire")
    with pytest.raises(ValueError, match="binding=Customer.name"):
        validate_plan_references([wire])
    validate_plan_references([wire, artifact("erd", 'erDiagram\n Customer["Customer record"] {\n string name "Display name"\n }', "mermaid")])


@pytest.mark.parametrize("kind,content", [
    ("skills", "## FR-001\nAn example instruction"),
    ("frac", "```markdown\n## FR-001\nAn example only\n```"),
    ("prd", "This mentions FR-001 without defining it."),
])
def test_mentions_and_example_code_do_not_define_requirements(kind, content):
    wire = artifact("wireframe", 'screen "Save"\n  button "Save" requirement=FR-001', "wire")
    with pytest.raises(ValueError, match="requirement=FR-001"):
        validate_plan_references([wire, artifact(kind, content)])


def test_commented_schema_does_not_define_binding():
    wire = artifact("wireframe", 'screen "Name"\n  input "Name" bind=Customer.name', "wire")
    with pytest.raises(ValueError, match="bind=Customer.name"):
        validate_plan_references([wire, artifact("erd", "erDiagram\n%% Customer {\n%% string name\n%% }", "mermaid")])


def test_chat_exposes_reference_diagnostics_but_not_arbitrary_provider_errors():
    from distr.core.planning.conversation import _safe_error
    from distr.core.planning.validation import PlanReferenceError
    message = "Unresolved plan references. No changes were saved: Customers, line 2: unresolved bind=Customer.name"
    assert _safe_error(PlanReferenceError(message)) == message
    assert "private-provider-token" not in _safe_error(ValueError("private-provider-token"))
