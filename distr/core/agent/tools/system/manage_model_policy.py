"""Conversational tool for previewing and applying model routing policy."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

from distr.core.project_cli_backends.policy_manager import (
    apply_model_policy_plan,
    build_model_policy_plan,
)

logger = logging.getLogger(__name__)


class ManageModelPolicyInput(BaseModel):
    action: Literal["preview", "apply"] = Field(
        default="preview",
        description="Preview is non-mutating. Use apply only when the user explicitly asked to set, update, or configure it.",
    )
    scope: Literal["workflow", "global", "both"] = Field(default="workflow")
    workflow_id: int | None = Field(default=None, description="Required for workflow or both scope.")
    mode: Literal["auto", "pinned"] = Field(
        default="auto",
        description="Auto refreshes and resolves routes at preflight. Pinned stores explicit editable selections.",
    )
    preference: Literal["free", "balanced", "performance"] = Field(default="free")
    assignments: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional explicit routes grouped under complexity, roles, or steps. Example: "
            "{'roles': {'planning': {'backend': 'codex', 'model': 'auto'}}}."
        ),
    )


class ManageModelPolicyTool(BaseTool):
    name: str = "manage_model_policy"
    description: str = (
        "Preview or apply editable AI provider/model routing for global low/medium/high complexity and every workflow step. "
        "USE THIS TOOL when asked to refresh models, use the latest/best/free models, configure workflow models, swap a model, "
        "pin a provider/model, or let Auto choose models for planning, implementation, review, validation, and reporting. "
        "Preview unless the user explicitly asks to set, update, apply, configure, or change the policy."
    )
    args_schema: type[BaseModel] = ManageModelPolicyInput

    def get_triggers(self) -> list[str]:
        return [
            "configure workflow models",
            "update the models",
            "use free models",
            "refresh models",
            "choose models for the steps",
            "set the provider",
            "model policy",
            "swap models",
        ]

    def _run(
        self,
        action: str = "preview",
        scope: str = "workflow",
        workflow_id: int | None = None,
        mode: str = "auto",
        preference: str = "free",
        assignments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            plan = build_model_policy_plan(
                scope=scope,
                workflow_id=workflow_id,
                mode=mode,
                preference=preference,
                assignments=assignments,
            )
            result = apply_model_policy_plan(plan) if action == "apply" else None
            verb = "Applied" if action == "apply" else "Previewed"
            workflow = plan.get("workflow") or {}
            summary = (
                f"{verb} the {mode} model policy for {scope}. "
                f"{len(workflow.get('steps') or [])} workflow steps were resolved; "
                f"{plan.get('catalog', {}).get('candidate_count', 0)} live free/local candidates were considered."
            )
            reference = {"action": action, "plan": plan}
            if result is not None:
                reference["applied"] = result
            return summary + "\n\nREFERENCE:\n" + json.dumps(reference, indent=2, sort_keys=True)
        except Exception as exc:
            logger.error("manage_model_policy failed: %s", exc, exc_info=True)
            return f"I could not {action} that model policy: {exc}"

    async def _arun(self, **kwargs: Any) -> str:
        return self._run(**kwargs)

