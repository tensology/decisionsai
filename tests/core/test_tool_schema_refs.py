"""Tool schemas must be self-contained.

Pydantic emits nested models as `{"$ref": "#/$defs/..."}` plus a top-level `$defs`
block. The OpenAI-format converter copied only the field snippet, dropping the
definitions, so providers with strict schema validation rejected the whole
request:

    reference to undefined schema at properties.tickets.items

Gemini/OpenRouter rejected every turn because of one nested model.
"""

from __future__ import annotations

import json

from distr.core.agent.services.llm.tool_format import inline_schema_refs


def test_a_nested_model_ref_is_inlined():
    schema = {
        "$defs": {
            "TicketDraftInput": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
            }
        },
        "type": "object",
        "properties": {
            "tickets": {"type": "array", "items": {"$ref": "#/$defs/TicketDraftInput"}}
        },
    }

    resolved = inline_schema_refs(schema)
    rendered = json.dumps(resolved)

    assert "$ref" not in rendered
    assert "$defs" not in rendered
    items = resolved["properties"]["tickets"]["items"]
    assert items["properties"]["title"]["type"] == "string"


def test_an_unresolvable_ref_is_dropped_rather_than_shipped():
    resolved = inline_schema_refs(
        {"type": "object", "properties": {"x": {"$ref": "#/$defs/Missing"}}}
    )

    assert json.dumps(resolved) == '{"type": "object", "properties": {"x": {}}}'


def test_the_real_kanban_tool_schema_has_no_dangling_refs():
    from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketInput

    raw = KanbanTicketInput.model_json_schema()
    assert "$defs" in raw, "fixture assumption: this model nests another model"

    fixed = inline_schema_refs(raw)

    assert json.dumps(fixed).count("$ref") == 0
    assert "$defs" not in fixed
    # The batch field that triggered the rejection resolves to a real schema.
    assert fixed["properties"]["tickets"]["items"].get("properties")
