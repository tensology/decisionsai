"""Bounded validation through the canonical browser wireframe implementation."""
import json
import re
from pathlib import Path
import shutil
import subprocess


class PlanValidationError(ValueError):
    """Workspace-owned validation diagnostics, safe to show in the plan chat."""


class PlanReferenceError(PlanValidationError):
    """Workspace-owned reference diagnostics, safe to show in the plan chat."""


def _inspect_wireframe(source, *, page_id=None):
    if not isinstance(source, str) or len(source) > 100_000:
        raise ValueError("Wireframe source exceeds the supported limit.")
    node = shutil.which("node")
    if not node:
        raise ValueError("Wireframe validation requires the configured Node runtime. No changes were saved.")
    try:
        result = subprocess.run(
            [node, "--max-old-space-size=128", str(Path(__file__).with_name("validate_wireframe.mjs"))],
            input=json.dumps({"source": source, **({"page_id": page_id} if page_id is not None else {})}),
            text=True, capture_output=True, timeout=10, check=True,
        )
        inspection = json.loads(result.stdout)
        diagnostics = inspection["diagnostics"]
    except (subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
        raise ValueError("Wireframe validation could not finish. No changes were saved.") from exc
    errors = [item for item in diagnostics if item["severity"] == "error"]
    if errors:
        details = "; ".join(f"line {item['line']}: {item['message']}" for item in errors[:5])
        raise PlanValidationError(f"Invalid wireframe: {details}")
    return inspection


def validate_wireframe(source, *, page_id=None):
    return _inspect_wireframe(source, page_id=page_id)["diagnostics"]


def validate_mermaid(source, *, require_erd=False):
    if not isinstance(source, str) or len(source) > 100_000:
        raise PlanValidationError("Diagram source exceeds the supported limit.")
    node = shutil.which("node")
    if not node:
        raise ValueError("Diagram validation requires the configured Node runtime. No changes were saved.")
    try:
        result = subprocess.run(
            [node, "--max-old-space-size=128", str(Path(__file__).with_name("validate_mermaid.mjs"))],
            input=json.dumps({"source": source}), text=True, capture_output=True, timeout=10, check=True,
        )
        parsed = json.loads(result.stdout)
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        raise ValueError("Diagram validation could not finish. No changes were saved.") from exc
    if parsed.get("valid") is not True:
        raise PlanValidationError("Invalid Mermaid diagram. No changes were saved: " + str(parsed.get("error", "Invalid syntax.")))
    if require_erd and parsed.get("diagram_type") != "er":
        raise PlanValidationError("An ERD artifact must contain a Mermaid erDiagram. No changes were saved.")
    return parsed["diagram_type"]


def project_image_ids(db, project_id):
    from distr.core.db.projects import ProjectFile
    import mimetypes
    return {str(row.id) for row in db.query(ProjectFile).filter_by(project_id=project_id).all()
            if project_id and mimetypes.guess_type(row.filename)[0] in {'image/png', 'image/jpeg', 'image/webp', 'image/svg+xml'}}


def validate_plan_references(items, *, asset_ids=()):
    """Resolve explicit DSL annotations against the entire prospective workspace.

    Unannotated sketches remain valid. This proves reference integrity, not that
    the specification captures every behavior or that the ERD is sufficient.
    Requirement declarations use a heading or list/paragraph beginning FR-xxx.
    Data bindings use the Mermaid entity identifier (not its display alias).
    """
    requirements = set()
    fields = set()
    for item in items:
        content = item["content"]
        if item["content_format"] == "mermaid":
            validate_mermaid(content, require_erd=item["item_type"] == "erd")
        if item["item_type"] in {"prd", "frac"} and item["content_format"] == "markdown":
            content = re.sub(r"(?ms)^\s*(`{3,}|~{3,})[^\n]*\n.*?^\s*\1\s*$", "", content)
            requirements.update(re.findall(
                r"(?m)^\s*(?:#{1,6}\s+|[-*+]\s+)?(?:\*\*)?(FR-[A-Za-z0-9][A-Za-z0-9_.-]*)(?=\s|:|\*|$)", content))
        if item["item_type"] in {"erd", "architecture"} and item["content_format"] == "mermaid":
            # Comments cannot satisfy a binding. Attribute types may include
            # arrays/generics; the second token is the canonical field name.
            content = re.sub(r"(?m)%%.*$", "", content)
            for entity, body in re.findall(r'\b([A-Za-z_][\w-]*)(?:\s*\[[^\]\n]*\])?\s*\{([^{}]*)\}', content):
                for field in re.findall(r"(?m)^\s*[^\s]+\s+([A-Za-z_][\w-]*)(?=\s|$)", body):
                    fields.add(f"{entity}.{field}")
    errors = []
    for item in items:
        if item["content_format"] != "wire":
            continue
        for ref in _inspect_wireframe(item["content"])["references"]:
            available = set(asset_ids) if ref["attribute"] == "asset" else requirements if ref["attribute"] == "requirement" else fields
            if ref["value"] not in available:
                errors.append(f"{item.get('title', 'Wireframe')}, line {ref['line']}: unresolved {ref['attribute']}={ref['value']}")
    if errors:
        raise PlanReferenceError("Unresolved plan references. No changes were saved: " + "; ".join(errors[:5]))
