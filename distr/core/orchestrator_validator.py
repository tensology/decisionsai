"""Orchestrator validator LLM — second-pass and primary LLM judgment for workflow steps."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def is_orchestrator_validator_second_pass_enabled() -> bool:
    try:
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        return bool(settings.get("orchestrator_validator_second_pass_enabled", True))
    except Exception:
        return True


def _validation_criteria(step: Any, fallback: str = "") -> str:
    return (
        str(getattr(step, "validation_prompt", None) or getattr(step, "verification", None) or fallback or "")
        .strip()
    )


def _parse_pass_fail_response(content: str) -> tuple[bool, str]:
    text = (content or "").strip()
    if not text:
        return False, "Empty validator response"
    upper = text.upper()
    if upper.startswith("PASS"):
        return True, text
    if upper.startswith("FAIL"):
        return False, text
    if "PASS" in upper[:20]:
        return True, text
    return False, text


def run_orchestrator_validator_judgment(
    *,
    result: str,
    validation_prompt: str,
    standards_context: str = "",
    ticket_context: str = "",
    mode: str = "primary",
    route: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Ask the orchestrator validator model to judge step output.

    Returns None when validator model is not configured or the call fails.
    """
    try:
        from distr.core.orchestrator import get_orchestrator_role_model
        from distr.core.settings import load_settings_from_db
        from distr.core.workflow.planning import _litellm_model

        import litellm

        route = route if isinstance(route, dict) else {}
        provider = str(route.get("model_provider") or route.get("provider") or "").strip()
        model = str(route.get("model") or "").strip()
        if not provider or not model or model.lower() == "auto":
            provider, model = get_orchestrator_role_model("validator")
        if not provider and not model:
            return None

        settings = load_settings_from_db()
        criteria = validation_prompt.strip() or "The step output should satisfy the ticket acceptance criteria."
        prompt = {
            "task": "Validate whether the workflow step result passes. Respond with PASS or FAIL then a brief rationale.",
            "mode": mode,
            "validation_criteria": criteria[:6000],
            "standards_context": (standards_context or "")[:4000],
            "ticket_context": (ticket_context or "")[:3000],
            "step_result": (result or "")[:12000],
            "response_format": "PASS: <reason> or FAIL: <reason>",
        }
        response = litellm.completion(
            model=_litellm_model(provider.strip().lower(), model, settings),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the workflow validation judge. "
                        "Be strict about acceptance criteria and evidence. "
                        "Do not pass vague or incomplete work. Do not mention internal system names."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            max_tokens=512,
            temperature=0.1,
        )
        content = (response.choices[0].message.content or "").strip()
        content = re.sub(r"^```\w*\s*", "", content)
        content = re.sub(r"\s*```\s*$", "", content)
        passed, rationale = _parse_pass_fail_response(content)
        return {
            "passed": passed,
            "rationale": rationale[:2000],
            "mode": mode,
            "provider": provider,
            "model": model,
            "route_source": route.get("source") or "orchestrator_validator",
        }
    except Exception as exc:
        logger.debug("Orchestrator validator judgment skipped: %s", exc)
        return None


def apply_orchestrator_validator_overlay(
    *,
    step: Any,
    result: str,
    caller_passed: bool,
    mechanical_passed: bool,
    standards_context: str = "",
    ticket_context: str = "",
    validation_routes: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """
    Optional orchestrator second pass after mechanical verification.

    Runs when mechanical verification passed but we want an LLM sanity check
    (playwright/text_match/rule_based/none paths).
    """
    if not is_orchestrator_validator_second_pass_enabled():
        return None
    if not caller_passed or not mechanical_passed:
        return None

    vtype = (getattr(step, "validation_type", None) or "none").strip().lower()
    # Primary LLM judgment already consumes the coordination plan's concrete
    # validator routes. Running the same routes again here doubles latency and
    # cost without adding independence.
    if vtype == "llm_judgment":
        return None

    criteria = _validation_criteria(step)
    if not criteria and not ticket_context.strip():
        criteria = "Confirm the step result is complete, accurate, and matches the ticket intent."

    routes = [item for item in (validation_routes or []) if isinstance(item, dict)]
    verdicts: list[dict[str, Any]] = []
    if routes:
        # Independent evaluators do not depend on each other's output, so a
        # dual review should not impose twice the wall-clock latency.
        from concurrent.futures import ThreadPoolExecutor

        def judge(route: dict[str, Any]) -> dict[str, Any] | None:
            return run_orchestrator_validator_judgment(
                result=result,
                validation_prompt=criteria,
                standards_context=standards_context,
                ticket_context=ticket_context,
                mode="independent_second_pass",
                route=route,
            )

        with ThreadPoolExecutor(max_workers=min(2, len(routes))) as pool:
            verdicts = [item for item in pool.map(judge, routes[:2]) if item is not None]
    else:
        verdict = run_orchestrator_validator_judgment(
            result=result,
            validation_prompt=criteria,
            standards_context=standards_context,
            ticket_context=ticket_context,
            mode="second_pass",
        )
        if verdict is not None:
            verdicts = [verdict]
    if not verdicts:
        return None
    verdict = dict(verdicts[0])
    verdict["passed"] = all(bool(item.get("passed")) for item in verdicts)
    verdict["mode"] = "dual_independent" if len(verdicts) > 1 else verdict.get("mode", "independent")
    verdict["reviews"] = verdicts
    if len(verdicts) > 1:
        verdict["rationale"] = " | ".join(str(item.get("rationale") or "") for item in verdicts)[:2000]

    try:
        from distr.core.orchestrator import emit_event

        emit_event(
            source="orchestrator",
            event_type="validation_second_pass",
            status="pass" if verdict.get("passed") else "fail",
            step_id=getattr(step, "id", None),
            summary=(verdict.get("rationale") or "Validator second pass")[:240],
            payload=verdict,
        )
    except Exception:
        logger.debug("Could not emit validation_second_pass event", exc_info=True)

    return verdict
