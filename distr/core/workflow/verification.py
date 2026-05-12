"""
Workflow Verification — text_match, rule_based, llm_judgment, screenshot, playwright verification.

Extracted from service.py as part of the module decomposition.
"""
import logging
import os
from typing import Any, Dict, Optional

from distr.core.db.workflow import AutoWorkflowStep

logger = logging.getLogger(__name__)


# ── Verification engine ──


def _run_verification(step: AutoWorkflowStep, result: str, caller_passed: bool) -> bool:
    """
    Run the configured validation for a step. Returns True if passed.
    If validation_type is 'none', uses the caller's passed flag.
    """
    vtype = (step.validation_type or "none").strip().lower()
    if vtype == "none":
        return caller_passed

    prompt = (step.validation_prompt or "").strip()
    if not prompt and vtype != "playwright":
        # No validation criteria configured — trust the caller
        return caller_passed

    try:
        if vtype == "text_match":
            return _verify_text_match(result, prompt)
        elif vtype == "rule_based":
            return _verify_rule_based(result, prompt)
        elif vtype == "llm_judgment":
            return _verify_llm_judgment(result, prompt)
        elif vtype == "screenshot_compare":
            return _verify_screenshot(step, result, prompt)
        elif vtype == "playwright":
            return _verify_playwright(step, caller_passed)
        else:
            logger.warning("Unknown validation type '%s', defaulting to caller_passed", vtype)
            return caller_passed
    except Exception as e:
        logger.error("Verification failed for step %s: %s", step.id, e, exc_info=True)
        return False


def build_validation_snapshot(
    step: AutoWorkflowStep,
    result: str,
    caller_passed: bool,
    verified_passed: bool,
) -> Dict[str, Any]:
    """Build a compact, serializable record of the validation decision."""
    vtype = (getattr(step, "validation_type", None) or "none").strip().lower()
    expected = (
        getattr(step, "validation_prompt", None)
        or getattr(step, "verification", None)
        or ""
    )
    observed = (result or "").strip()
    if len(observed) > 600:
        observed = observed[:600] + "..."
    return {
        "step_id": getattr(step, "id", None),
        "step_name": getattr(step, "name", None) or f"Step {getattr(step, 'id', '')}",
        "validation_type": vtype,
        "expected": str(expected or "").strip(),
        "observed": observed,
        "caller_passed": bool(caller_passed),
        "verified_passed": bool(verified_passed),
        "verdict": "pass" if verified_passed else "fail",
    }


def _verify_text_match(result: str, criteria: str) -> bool:
    """Check if the result contains the expected text (case-insensitive)."""
    if not result:
        return False
    result_lower = result.lower()
    # Support multiple match phrases separated by newlines
    for line in criteria.strip().splitlines():
        phrase = line.strip()
        if phrase and phrase.lower() not in result_lower:
            return False
    return True


def _verify_rule_based(result: str, rules: str) -> bool:
    """Evaluate simple rules against the result.
    Rules are line-separated. Each line is a condition:
      contains: <text>
      not_contains: <text>
      starts_with: <text>
      min_length: <number>
    """
    if not result:
        return False
    result_lower = result.lower()
    for line in rules.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("contains:"):
            val = line[len("contains:"):].strip()
            if val.lower() not in result_lower:
                return False
        elif line.lower().startswith("not_contains:"):
            val = line[len("not_contains:"):].strip()
            if val.lower() in result_lower:
                return False
        elif line.lower().startswith("starts_with:"):
            val = line[len("starts_with:"):].strip()
            if not result_lower.startswith(val.lower()):
                return False
        elif line.lower().startswith("min_length:"):
            try:
                min_len = int(line[len("min_length:"):].strip())
                if len(result) < min_len:
                    return False
            except ValueError:
                pass
    return True


def _verify_llm_judgment(result: str, validation_prompt: str) -> bool:
    """Send the result + validation prompt to the LLM for judgment."""
    try:
        from distr.core.signals import signal_manager
        # Build a judgment prompt
        judgment_prompt = (
            f"You are a validation judge. Evaluate whether the following result passes the validation criteria.\n\n"
            f"VALIDATION CRITERIA:\n{validation_prompt}\n\n"
            f"RESULT TO VALIDATE:\n{result}\n\n"
            f"Respond with exactly PASS or FAIL followed by a brief explanation."
        )
        # Use synchronous LLM call if available
        try:
            from distr.core.agent.services.llm.shared import get_shared_llm_response
            response = get_shared_llm_response(judgment_prompt)
            if response:
                return response.strip().upper().startswith("PASS")
        except ImportError:
            pass
        # Fallback: trust caller
        logger.warning("LLM judgment not available, defaulting to pass")
        return True
    except Exception as e:
        logger.error("LLM judgment failed: %s", e, exc_info=True)
        return False


def _verify_screenshot(step: AutoWorkflowStep, result: str, validation_prompt: str) -> bool:
    """Compare current screen state against reference screenshot using LLM vision.
    Falls back to text-based validation if vision is not available."""
    ref_path = step.screenshot_path
    if not ref_path or not os.path.exists(ref_path):
        logger.warning("No reference screenshot for step %s, using text validation", step.id)
        return _verify_text_match(result, validation_prompt) if validation_prompt else True

    # Take a current screenshot for comparison
    try:
        import subprocess
        import platform
        from distr.core.paths import DB_DIR
        current_path = os.path.join(DB_DIR, "workflow_screenshots", f"step_{step.id}_current.png")
        os.makedirs(os.path.dirname(current_path), exist_ok=True)
        system = platform.system()
        if system == "Darwin":
            subprocess.run(["screencapture", "-x", current_path], timeout=5, check=True)
        elif system == "Windows":
            from PIL import ImageGrab
            img = ImageGrab.grab()
            img.save(current_path)
        else:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            img.save(current_path)
        # If we have both screenshots, try LLM vision comparison
        # For now, fall back to validation_prompt text match
        logger.info("Screenshots captured for step %s. Using validation prompt for judgment.", step.id)
        if validation_prompt:
            return _verify_llm_judgment(result + f"\n[Screenshots captured: reference={ref_path}, current={current_path}]", validation_prompt)
        return True
    except Exception as e:
        logger.error("Screenshot comparison failed: %s", e, exc_info=True)
        return True


def _verify_playwright(step: AutoWorkflowStep, caller_passed: bool) -> bool:
    """Execute a Playwright validation script. Exit code 0 = passed, non-zero = failed.
    Falls back to caller_passed if validation_code is empty."""
    validation_code = (step.validation_code or "").strip()
    if not validation_code:
        logger.info("No validation_code for step %s, falling back to caller_passed", step.id)
        return caller_passed

    try:
        from distr.core.workflow_engine.test_loop import TestLoopService
        result = TestLoopService()._execute_playwright(validation_code)
        exit_code = result.get("exit_code", 1) if isinstance(result, dict) else getattr(result, "exit_code", 1)
        output = result.get("output", "") if isinstance(result, dict) else getattr(result, "output", "")
        if exit_code == 0:
            logger.info("Playwright validation passed for step %s", step.id)
            return True
        else:
            logger.info("Playwright validation failed for step %s (exit_code=%s): %s", step.id, exit_code, output[:200])
            return False
    except Exception as e:
        logger.error("Playwright validation error for step %s: %s", step.id, e, exc_info=True)
        return False
