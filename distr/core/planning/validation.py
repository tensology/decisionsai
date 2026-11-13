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
        def where(item):
            # Show the offending source (e.g. which page) so the error is actionable, not just a line number.
            evidence = " ".join(str(item.get("evidence") or "").split())[:120]
            return f"line {item['line']}: {item['message']}" + (f" ({evidence})" if evidence else "")
        details = "; ".join(where(item) for item in errors[:5])
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


# ---- Interface node metadata sidecar (N6): one trailing line per item --------------------------------
# wire:    # decisions-flow: {"v":1,"nodes":{"<screen id>":{"shape":..,"image":..,"note":..,"links":{..}}}}
# mermaid: %% decisions-flow: {"v":1,"nodes":{"<node id>":{"image":..,"note":..,"links":{..}}}}
# Both are comment lines, so the wire parser / renderer / validate_wireframe and Mermaid ignore them.
FLOW_SIDECAR_PREFIX = {"wire": "# decisions-flow:", "mermaid": "%% decisions-flow:"}
FLOW_SHAPES = ("page", "rectangle", "square", "circle", "oval", "diamond")
FLOW_SIDECAR_MAX_BYTES = 16 * 1024
FLOW_NODE_LIMIT = 500
FLOW_NOTE_LIMIT = 500
FLOW_LINK_LABEL_LIMIT = 120
_FLOW_ID = re.compile(r"^[A-Za-z][\w-]{0,63}$")
_SCREEN_ID = re.compile(r"^\s*screen\b.*?\bid=([A-Za-z][\w-]{0,63})\b")
_MERMAID_TOKEN = re.compile(r"[A-Za-z][\w-]{0,63}")


def _flow_node_ids(lines, content_format):
    ids = set()
    for line in lines:
        stripped = line.strip()
        if content_format == "wire":
            match = _SCREEN_ID.match(line)
            if match:
                ids.add(match.group(1))
        elif stripped and not stripped.startswith("%%"):
            ids.update(_MERMAID_TOKEN.findall(stripped.split('"', 1)[0]))
    return ids


def validate_flow_sidecar(content, content_format, *, asset_ids=None):
    """Validate the optional trailing ``decisions-flow`` line. Raise PlanValidationError (HTTP 422) for bad
    metadata; return warnings (orphan keys: the server only warns, the editor prunes them)."""
    prefix = FLOW_SIDECAR_PREFIX.get(content_format)
    if prefix is None or not isinstance(content, str) or prefix not in content:
        return []
    lines = content.split("\n")
    hits = [index for index, line in enumerate(lines) if line.strip().startswith(prefix)]
    if not hits:
        return []
    if len(hits) > 1:
        raise PlanValidationError("Only one flow metadata line is allowed.")
    last = max((index for index, line in enumerate(lines) if line.strip()), default=-1)
    if hits[0] != last:
        raise PlanValidationError("The flow metadata line must be the last line of the item.")
    raw = lines[hits[0]].strip()[len(prefix):].strip()
    if len(raw.encode("utf-8")) > FLOW_SIDECAR_MAX_BYTES:
        raise PlanValidationError("Flow metadata is larger than 16 KB.")
    try:
        data = json.loads(raw)
    except ValueError:
        raise PlanValidationError("Flow metadata is not valid JSON.") from None
    if not isinstance(data, dict):
        raise PlanValidationError("Flow metadata is not valid JSON.")
    for key in data:
        if key not in ("v", "nodes"):
            raise PlanValidationError(f'Unknown flow metadata field "{key}".')
    if data.get("v") != 1:
        raise PlanValidationError("Flow metadata needs \"v\": 1.")
    nodes = data.get("nodes", {})
    if not isinstance(nodes, dict):
        raise PlanValidationError('Flow metadata "nodes" must be an object.')
    if len(nodes) > FLOW_NODE_LIMIT:
        raise PlanValidationError(f"Flow metadata can describe at most {FLOW_NODE_LIMIT} nodes.")
    known_assets = None if asset_ids is None else {str(a) for a in asset_ids}
    for node_id, meta in nodes.items():
        if not _FLOW_ID.match(str(node_id)):
            raise PlanValidationError(f'Flow metadata node id "{str(node_id)[:64]}" must start with a letter and use at most 64 letters, digits, _ or -.')
        if not isinstance(meta, dict):
            raise PlanValidationError(f'Flow metadata for "{node_id}" must be an object.')
        for field in meta:
            if field not in ("shape", "image", "note", "links"):
                raise PlanValidationError(f'Unknown flow metadata field "{field}".')
        if "shape" in meta:
            if content_format == "mermaid":
                raise PlanValidationError("Node shapes for Mermaid flows are set in the diagram itself, not in flow metadata.")
            if meta["shape"] not in FLOW_SHAPES:
                raise PlanValidationError(f'Unknown node shape "{str(meta["shape"])[:40]}". Use page, rectangle, square, circle, oval or diamond.')
        if "image" in meta:
            image = meta["image"]
            if not isinstance(image, (str, int)) or isinstance(image, bool) or (known_assets is not None and str(image) not in known_assets):
                raise PlanValidationError(f"Image {str(image)[:40]} is not an attachment of this project.")
        if "note" in meta and (not isinstance(meta["note"], str) or len(meta["note"]) > FLOW_NOTE_LIMIT):
            raise PlanValidationError(f'Node note for "{node_id}" is longer than {FLOW_NOTE_LIMIT} characters.')
        if "links" in meta:
            links = meta["links"]
            if not isinstance(links, dict) or len(links) > 50 or not all(
                    _FLOW_ID.match(str(target)) and isinstance(label, str) and len(label) <= FLOW_LINK_LABEL_LIMIT
                    for target, label in links.items()):
                raise PlanValidationError(f'Links for "{node_id}" must map node ids to labels of at most {FLOW_LINK_LABEL_LIMIT} characters.')
    present = _flow_node_ids(lines[:hits[0]], content_format)
    return [f'Flow metadata for "{node_id}" has no matching node; it will be removed on the next edit.'
            for node_id in nodes if node_id not in present]
