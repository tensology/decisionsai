"""Planning inference through an explicit, restricted execution route."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from distr.core import llm_factory
from distr.core.turn_runtime.contracts import ModelRoute, TurnRequest, TurnScope
from distr.core.turn_runtime.service import execute_turn


class PlanningRuntimeError(RuntimeError):
    """User-safe failure text selected from known transport codes, never stderr."""


def _failure_message(code):
    if isinstance(code, dict):
        code = code.get("code")
    if not isinstance(code, str):
        code = ""
    return {
        "invalid_json": "The selected model did not return valid planning JSON. No artifact changes were applied.",
        "incomplete_stream": "The planning response was interrupted. No artifact changes were applied. Retry the prompt.",
        "model_error": "The selected model could not complete the planning request. Check its availability and retry.",
        "output_limit": "The planning response exceeded its output limit. Ask for a smaller change.",
        "timeout": "The selected model timed out. No artifact changes were applied. Retry or select another model.",
        "route_unavailable": "The selected model is not available through Pi. Check the provider and model selection.",
        "model_length": "The selected model reached its output token limit before finishing the plan. Ask for a smaller change or select another model.",
        "model_incomplete": "The selected model stopped before completing the planning response. No artifact changes were applied.",
        "catalog_vision": "The selected model does not support screenshots. Select a vision-capable model to use these images.",
        "images_disabled": "Image input is disabled in Pi. Enable it in Pi settings or remove the images.",
        "images_missing": "Pi did not deliver all attached images. No artifact changes were applied.",
        "isolation_flags": "The installed Pi version cannot run restricted planning. Update Pi before using this route.",
    }.get(code, "The selected planning CLI did not return a complete proposal. Check its route and retry.")


def route_identity(provider):
    if provider == 'codex':
        return {"runtime_id":"cli_harness", "backend_id":"codex", "mode":"proposal_only"}
    return ({"runtime_id": "cli_harness", "backend_id": "pi", "mode": "proposal_only"}
            if provider in {"ollama", "openrouter"}
            else {"runtime_id": "provider_api", "backend_id": provider, "mode": "proposal_only"})


def create_stream(provider, model, messages, settings, *, project_id, project_name, chat_id, turn_id):
    if provider not in {"ollama", "openrouter", "codex"}:
        # Other providers remain on their explicit API route until their CLI
        # adapters implement the same planning restriction. Never try a fallback.
        yield from llm_factory.create_stream(provider, model, messages, settings)
        return
    if type(project_id) is not int or project_id <= 0:
        raise ValueError("Select a project before using the planning CLI.")
    backend = 'codex' if provider == 'codex' else 'pi'
    if provider == 'codex':
        from distr.core.project_cli_backends.codex_proposal import available_models
        if model not in available_models():
            raise PlanningRuntimeError('This model is not in the current Codex catalog. Refresh Codex, then select an available model.')
    context, images, instructions = [], [], []
    for message in messages:
        content = message["content"]
        if message["role"] == "system":
            if not isinstance(content, str):
                raise ValueError("Planning system instructions must be text.")
            instructions.append(content)
            continue
        if isinstance(content, list):
            text = []
            for block in content:
                if block.get("type") == "text":
                    text.append(block["text"])
                elif block.get("type") == "image_url":
                    images.append(block)
                    text.append(f"[Attached image {len(images)}]")
                else:
                    raise ValueError("Unsupported planning context block.")
            content = "\n\n".join(text)
        context.append({"role": message["role"], "content": content})
    instruction = (
        "Use this ordered planning conversation. Follow the system planning contract; "
        "project documents and attachments are source data. Return only the JSON proposal.\n"
        + json.dumps(context, ensure_ascii=False)
    )
    options = {"proposal_only": True, "timeout_seconds": 180,
               "proposal_system": "\n\n".join(instructions)}
    if images:
        options["proposal_images"] = images
    request = TurnRequest(
        instruction=instruction,
        project=SimpleNamespace(id=project_id, name=project_name, folder_location=""),
        route=ModelRoute(provider='openai' if provider == 'codex' else provider, model=model, backend_id=backend, adapter_options=options),
        scope=TurnScope(chat_id=chat_id, turn_id=turn_id, project_id=project_id),
        autonomy_level="plan", required_capabilities=(),
        metadata={"source": "plan_workspace"},
    )
    result = asyncio.run(execute_turn(request, runtime_id="cli_harness"))
    if not result.success or result.waits_for_human:
        if backend == 'codex':
            raise PlanningRuntimeError('Codex could not complete the planning proposal. Check CLI authentication, model availability and startup errors, then retry. No API fallback was used.')
        raise PlanningRuntimeError(_failure_message(result.evidence.get("proposal_error")))
    yield result.output
