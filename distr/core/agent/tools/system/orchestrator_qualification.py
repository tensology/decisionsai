"""Agent-facing inspection of DecisionsAI production qualification evidence."""

from __future__ import annotations

import json
from typing import Literal

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

from distr.core.qualification import certify_backend_inventory, qualification_snapshot


class OrchestratorQualificationInput(BaseModel):
    view: Literal["status", "scenarios", "providers"] = Field(
        default="status",
        description="Show the overall production gate, acceptance scenarios, or provider/model certifications.",
    )


class OrchestratorQualificationTool(BaseTool):
    name: str = "orchestrator_qualification"
    description: str = (
        "Inspect whether DecisionsAI is proven safe enough for assist, operate, or own autonomy. "
        "Use this when asked whether the orchestrator can be trusted, is production ready, what still needs testing, "
        "which providers/models are certified, or which acceptance scenarios remain."
    )
    args_schema: type[BaseModel] = OrchestratorQualificationInput

    def get_triggers(self) -> list[str]:
        return [
            "is the orchestrator production ready",
            "can i trust the orchestrator",
            "qualification status",
            "certified models",
            "what still needs testing",
            "autonomy readiness",
        ]

    def _run(self, view: str = "status", **_kwargs) -> str:
        try:
            from distr.core.project_cli_backends import get_backend_statuses

            certify_backend_inventory(get_backend_statuses())
        except Exception:
            # The snapshot still reports stored evidence and makes the missing
            # live certification visible rather than failing the conversation.
            pass
        snapshot = qualification_snapshot()
        if view == "scenarios":
            rows = snapshot["acceptance_matrix"]
            summary = (
                f"The production gate covers {len(rows)} orchestrator scenarios. "
                "A scenario only counts after its route, heartbeat, terminal report, lifecycle, and result evidence are recorded."
            )
            reference = {"acceptance_matrix": rows}
        elif view == "providers":
            providers = snapshot["providers"]
            counts = providers["status_counts"]
            summary = (
                f"There are {providers['certification_count']} provider/model capability checks: "
                f"{counts['certified']} certified, {counts['limited']} limited, "
                f"{counts['unavailable']} unavailable, and {counts['unknown']} unknown."
            )
            reference = {"providers": providers}
        else:
            autonomy = snapshot["recommended_autonomy"]
            if snapshot["production_ready"]:
                summary = (
                    "DecisionsAI has passed the production qualification gate and is eligible for own mode "
                    "inside the user's approved project boundaries."
                )
            else:
                reasons = " ".join(snapshot["reasons"][:3]) or "Qualification evidence has not been recorded yet."
                summary = f"DecisionsAI remains in {autonomy} mode. {reasons}"
            reference = {
                "production_ready": snapshot["production_ready"],
                "recommended_autonomy": autonomy,
                "reasons": snapshot["reasons"],
                "runs": snapshot["runs"],
            }
        return summary + "\n\nREFERENCE:\n" + json.dumps(reference, indent=2, sort_keys=True)

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)
