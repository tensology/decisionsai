"""
Workflow Verification — text_match, rule_based, llm_judgment, screenshot, playwright verification.

Extracted from service.py as part of the module decomposition.
"""
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Dict

from distr.core.db.workflow import AutoWorkflowStep

logger = logging.getLogger(__name__)


_MEDIA_PATH_RE = re.compile(
    r"(?<![\w.-])(?:[A-Za-z]:[\\/]|/|\.?\.?/)?[^\s`\"'<>]+\.(?:png|jpe?g|webp|gif|mp4|mov)\b",
    re.IGNORECASE,
)


def _reported_media_paths(result: str) -> list[str]:
    """Extract distinct screenshot/video paths from a worker report."""
    paths: list[str] = []
    for raw in _MEDIA_PATH_RE.findall(str(result or "")):
        path = raw.rstrip(".,;:)]}")
        if path and path not in paths:
            paths.append(path)
    return paths


def _project_folder(project_id: int | None) -> str:
    if not project_id:
        return ""
    try:
        from distr.core.db import get_session
        from distr.core.db.projects import Project

        with get_session() as db:
            project = db.query(Project).filter(Project.id == int(project_id)).first()
            return os.path.abspath(os.path.expanduser(str(project.folder_location or ""))) if project else ""
    except Exception:
        logger.debug("Could not resolve project folder for acceptance evidence", exc_info=True)
        return ""


def recover_blocked_browser_validation(
    result: str,
    *,
    project_id: int | None,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Run a reported project-local browser check in the Decisions host.

    Coding CLIs can be placed in a stricter child sandbox than the Decisions
    process itself.  When a final audit explicitly reports that Chromium could
    not launch, recover with the exact project-local Node test it attempted.
    No shell is used, the file must live inside the linked project, and a UI
    pass requires freshly written desktop and mobile evidence.
    """
    text = str(result or "")
    browser_blocked = re.search(
        r"(?is)\b(?:browser|playwright|chromium)\b.{0,240}"
        r"\b(?:blocked|could\s+not\s+(?:run|launch)|cannot\s+(?:run|launch)|"
        r"unable\s+to\s+(?:run|launch)|denied|permissions?|allowlist|no\s+browser\s+access)\b",
        text,
    )
    command_blocked = re.search(
        r"(?is)\b(?:cannot|could\s+not|unable\s+to)\s+run\s+`?node\s+[^\n]{1,180}"
        r"(?:allowlist|blocked|denied|permission)",
        text,
    )
    if not browser_blocked and not command_blocked:
        return {}
    root_text = _project_folder(project_id)
    if not root_text:
        return {}
    root = Path(root_text).expanduser().resolve()
    candidates: list[Path] = []
    for raw in re.findall(r"(?i)\bnode\s+(?!--check\b)([^\s`'\"]+\.(?:mjs|cjs|js))", text):
        candidate = (root / raw).resolve() if not os.path.isabs(raw) else Path(raw).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        lower = candidate.as_posix().lower()
        if candidate.is_file() and "test" in lower and any(
            token in lower for token in ("playwright", "e2e", "browser", "focus")
        ):
            candidates.append(candidate)
    if not candidates:
        return {}
    command = ["node", str(candidates[0].relative_to(root))]
    started = time.time()
    try:
        completed = subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=max(15, min(int(timeout_seconds or 300), 900)),
            check=False,
        )
    except Exception as exc:
        return {
            "attempted": True,
            "passed": False,
            "command": command,
            "error": str(exc),
        }
    media: list[str] = []
    for suffix in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        for path in root.rglob(suffix):
            try:
                if path.stat().st_mtime >= started - 1:
                    media.append(str(path.relative_to(root)))
            except OSError:
                continue
    media = sorted(set(media))
    names = " ".join(media).lower()
    desktop_and_mobile = "desktop" in names and "mobile" in names
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    return {
        "attempted": True,
        "passed": bool(completed.returncode == 0 and desktop_and_mobile),
        "command": command,
        "exit_code": completed.returncode,
        "output": output[-4000:],
        "fresh_media": media,
        "desktop_and_mobile": desktop_and_mobile,
    }


def ticket_acceptance_findings(
    step: AutoWorkflowStep,
    result: str,
    ticket_context: str,
    *,
    project_id: int | None = None,
) -> list[dict[str, str]]:
    """Return deterministic ticket-contract findings with actionable corrections."""
    findings: list[dict[str, str]] = []
    step_name = str(getattr(step, "name", "") or "").lower()
    raw_config = getattr(step, "config", None)
    if isinstance(raw_config, str):
        try:
            step_config = json.loads(raw_config or "{}") or {}
        except Exception:
            step_config = {}
    elif isinstance(raw_config, dict):
        step_config = raw_config
    else:
        step_config = {}
    step_role = str(step_config.get("step_role") or "").strip().lower()
    is_acceptance_review = step_role in {"review", "final_polish"} or (
        not step_role
        and "correct" not in step_name
        and any(word in step_name for word in ("review", "validate", "quality"))
    )
    if not is_acceptance_review:
        return findings
    ticket_text = str(ticket_context or "").lower()
    observed = str(result or "").lower()

    screenshot_required = (
        "browser evidence required" in ticket_text
        and any(word in ticket_text for word in ("screenshot", "screen recording", "capture"))
    )
    if screenshot_required:
        reported = _reported_media_paths(result)
        root = _project_folder(project_id)
        existing: list[str] = []
        for item in reported:
            candidate = os.path.expanduser(item)
            if not os.path.isabs(candidate) and root:
                candidate = os.path.join(root, candidate)
            if not root or os.path.isfile(candidate):
                existing.append(item)
        required_count = 2 if "spotify" in ticket_text and "youtube" in ticket_text else 1
        if len(existing) < required_count:
            source_label = "Spotify and YouTube" if required_count == 2 else "the required browser source"
            findings.append(
                {
                    "code": "missing_browser_media",
                    "message": (
                        f"Required browser evidence is missing: found {len(existing)} existing reported media artifact(s), "
                        f"but the ticket requires {required_count} for {source_label}."
                    ),
                    "correction_hint": (
                        f"Capture real browser screenshots or recordings for {source_label}, save them inside the project, "
                        "and return their exact existing .png/.jpg/.webp/.mp4/.mov paths. Do not report browser evidence as N/A."
                    ),
                }
            )
        visual_verdict = re.search(
            r"visual_claim_verdicts?\s*:\s*([^\n]+)",
            str(result or ""),
            flags=re.IGNORECASE,
        )
        browser_verdict = re.search(
            r"browser_evidence\s*:\s*([^\n]+)",
            str(result or ""),
            flags=re.IGNORECASE,
        )
        not_applicable = re.compile(r"^\s*(?:n\s*/?\s*a|not applicable)\b", re.IGNORECASE)
        if (
            (visual_verdict and not_applicable.search(visual_verdict.group(1)))
            or (browser_verdict and not_applicable.search(browser_verdict.group(1)))
        ):
            findings.append(
                {
                    "code": "unvalidated_visual_evidence",
                    "message": (
                        "The ticket explicitly requires browser screenshots, but the review marked the "
                        "browser or visual-claim verdict as not applicable. File existence is not visual validation."
                    ),
                    "correction_hint": (
                        "Inspect the actual contents of every required screenshot with a vision-capable tool/model, "
                        "report what each image visibly proves against the ticket, and keep the exact artifact paths."
                    ),
                }
            )

    from distr.core.workflow.ticket_contract import classify_ticket_execution

    profile = classify_ticket_execution(ticket_context)
    no_code_change = bool(profile.get("explicit_no_code"))
    negated_code_claims = (
        "no code changes",
        "no code change",
        "without code changes",
        "did not modify code",
    )
    observed_without_negations = observed
    for phrase in negated_code_claims:
        observed_without_negations = observed_without_negations.replace(phrase, "")
    claims_code_change = any(
        phrase in observed_without_negations
        for phrase in ("code cleanup", "code change", "modified frontend", "updated frontend", "changed frontend")
    )
    if no_code_change and claims_code_change:
        findings.append(
            {
                "code": "research_scope_code_change",
                "message": "The review accepted code changes despite an explicit research-only/no-code ticket contract.",
                "correction_hint": "Revert or reject the out-of-scope code changes and validate only the documentary deliverables.",
            }
        )

    copy_first = "copy-first" in ticket_text or "must copy" in ticket_text
    copy_evidence = any(token in observed for token in ("rsync ", "cp -a", "copied from", "copy manifest"))
    if copy_first and "implement" not in step_name and not copy_evidence:
        findings.append(
            {
                "code": "missing_copy_first_evidence",
                "message": "The review did not report terminal or manifest evidence for the ticket's copy-first constraint.",
                "correction_hint": "Provide the copy command/manifest evidence and verify excluded secrets, data, caches, and generated files.",
            }
        )
    return findings


def _ticket_acceptance_gate(
    step: AutoWorkflowStep,
    result: str,
    ticket_context: str,
    *,
    project_id: int | None = None,
) -> bool | None:
    """Reject objective evidence gaps before an LLM can hand-wave them away.

    Returns ``False`` for a deterministic acceptance failure and ``None`` when
    ordinary configured verification should decide. The gate is review-only so
    planning/context steps are not expected to produce final artifacts.
    """
    findings = ticket_acceptance_findings(
        step,
        result,
        ticket_context,
        project_id=project_id,
    )
    if findings:
        logger.warning("Ticket acceptance gate failed: %s", "; ".join(item["message"] for item in findings))
        return False

    from distr.core.workflow.ticket_contract import (
        classify_ticket_execution,
        research_review_has_evidence,
    )

    profile = classify_ticket_execution(ticket_context)
    normalized_ticket = " ".join(str(ticket_context or "").lower().split())
    normalized_result = " ".join(str(result or "").lower().split())
    test_only_ticket = bool(
        re.search(r"\b(?:run|execute|rerun)\b.{0,100}\b(?:pytest|tests?/|test suite|tests? suite)\b", normalized_ticket)
        and any(marker in normalized_ticket for marker in (
            "without editing files",
            "without editing project files",
            "do not edit files",
            "do not edit project files",
            "strictly read-only",
        ))
    )
    objective_test_pass = bool(
        re.search(r"\b\d+\s+passed\b", normalized_result)
        and re.search(r"\bexit (?:code|status)\s*[:=]?\s*[`*_]*0\b", normalized_result)
        and re.search(r"\bblockers\s*:\s*(?:none|n/a)\b", normalized_result)
        and re.search(r"\bfiles changed\s*:\s*none\b", normalized_result)
    )
    if test_only_ticket and objective_test_pass:
        # Exact process evidence is stronger than a subjective judge. This is
        # especially important when the optional validator model is absent or
        # rejects a valid retry merely because an earlier command failed.
        return True
    if profile.get("research_only") and research_review_has_evidence(result):
        # Explicit ticket scope beats the generic development validator. The
        # evidence helper requires a completed structured report, no blockers,
        # concrete artifact paths, and acceptance/deliverable verification.
        return True
    return None


# ── Verification engine ──


def _project_runtime_snapshot(project_id: int | None) -> dict[str, Any]:
    """Build a compact runtime snapshot for validation and UI."""
    if not project_id:
        return {}
    try:
        from distr.core.orchestrator import list_project_runtime_sessions

        sessions = list_project_runtime_sessions(project_id=int(project_id), active_only=True, limit=10)
        urls: list[dict[str, Any]] = []
        for session in sessions:
            for item in session.get("urls") or []:
                if isinstance(item, dict) and item.get("url"):
                    urls.append(item)
        policy = sessions[0].get("safe_restart_policy") if sessions else ""
        return {
            "sessions": sessions,
            "urls": urls[:5],
            "active_terminal_count": len(sessions),
            "safe_restart_policy": policy or "",
        }
    except Exception:
        logger.debug("Could not load project runtime snapshot", exc_info=True)
        return {}


def _run_verification(
    step: AutoWorkflowStep,
    result: str,
    caller_passed: bool,
    *,
    project_id: int | None = None,
    ticket_context: str = "",
    standards_context: str = "",
    validation_routes: list[dict[str, Any]] | None = None,
) -> bool:
    """
    Run the configured validation for a step. Returns True if passed.
    If validation_type is 'none', uses the caller's passed flag.
    """
    vtype = (step.validation_type or "none").strip().lower()
    raw_config = getattr(step, "config", None)
    if isinstance(raw_config, str):
        try:
            step_config = json.loads(raw_config or "{}") or {}
        except Exception:
            step_config = {}
    elif isinstance(raw_config, dict):
        step_config = raw_config
    else:
        step_config = {}
    review_mode = str(step_config.get("review_mode") or "").strip().lower()
    model_policy = step_config.get("model_policy") if isinstance(step_config.get("model_policy"), dict) else {}
    require_dual_validator = (
        review_mode == "dual"
        or bool(step_config.get("require_independent_validation"))
        or bool(model_policy.get("require_dual_validation"))
    )
    acceptance_gate = _ticket_acceptance_gate(
        step,
        result,
        ticket_context,
        project_id=project_id,
    )
    if acceptance_gate is False:
        return False
    if acceptance_gate is True and (vtype != "llm_judgment" or not require_dual_validator):
        # The worker performing an independent review is already the second
        # model in the engineering loop. Once deterministic ticket evidence is
        # complete, do not silently add a third judge. Explicit dual mode still
        # runs (and fails closed on) its configured evaluator.
        return bool(caller_passed)

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
            return _verify_llm_judgment(
                result,
                prompt,
                standards_context=standards_context,
                ticket_context=ticket_context,
                validation_routes=validation_routes,
                require_configured_validator=require_dual_validator,
                unavailable_fallback=caller_passed and not require_dual_validator,
            )
        elif vtype == "screenshot_compare":
            return _verify_screenshot(step, result, prompt)
        elif vtype in {"playwright", "browser_ui"}:
            runtime = _project_runtime_snapshot(project_id)
            base_url = ""
            urls = runtime.get("urls") or []
            if urls and isinstance(urls[0], dict):
                base_url = str(urls[0].get("url") or "").strip()
            return _verify_playwright(step, caller_passed, base_url=base_url)
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
    *,
    project_id: int | None = None,
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
    snapshot: Dict[str, Any] = {
        "step_id": getattr(step, "id", None),
        "step_name": getattr(step, "name", None) or f"Step {getattr(step, 'id', '')}",
        "validation_type": vtype,
        "expected": str(expected or "").strip(),
        "observed": observed,
        "caller_passed": bool(caller_passed),
        "verified_passed": bool(verified_passed),
        "verdict": "pass" if verified_passed else "fail",
    }
    runtime = _project_runtime_snapshot(project_id)
    urls = runtime.get("urls") or []
    if urls and isinstance(urls[0], dict) and urls[0].get("url"):
        snapshot["validation_url"] = str(urls[0]["url"]).strip()
        snapshot["runtime"] = {
            "urls": urls[:3],
            "active_terminal_count": runtime.get("active_terminal_count"),
        }
    return snapshot


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


def _verify_llm_judgment(
    result: str,
    validation_prompt: str,
    *,
    standards_context: str = "",
    ticket_context: str = "",
    validation_routes: list[dict[str, Any]] | None = None,
    require_configured_validator: bool = False,
    unavailable_fallback: bool = False,
) -> bool:
    """Send the result + validation prompt to the orchestrator validator model."""
    normalized_result = " ".join(str(result or "").lower().split())
    explicit_failure_patterns = (
        r"\bstatus\s*:\s*(?:failed|blocked)\b",
        r"\bverdict\s*:\s*fail(?:ed)?\b",
        r"\b(?:could not|cannot|can't|was not|is not) be (?:fully )?validated\b",
        r"\b(?<!no )(?:correction|further work|changes?) (?:is|are) required\b",
        r"\b(?:not|isn't) (?:ready|safe) to (?:ship|release|deploy)\b",
        r"\bunresolved (?:ticket-?blocking|blocking|critical|high-severity)\b",
    )
    normalized_prompt = " ".join(str(validation_prompt or "").lower().split())
    expects_clearance = any(
        phrase in normalized_prompt
        for phrase in (
            "no unresolved",
            "resolves release blockers",
            "all known defects are corrected",
        )
    )
    if expects_clearance and any(
        re.search(pattern, normalized_result) for pattern in explicit_failure_patterns
    ):
        # A worker's own explicit blocker is stronger evidence than a missing
        # validator fallback (or a later judge overlooking prose buried in a
        # long report).  Do not mark a self-declared failed review as passed.
        logger.warning("LLM judgment rejected an explicit failure claim in worker output")
        return False
    try:
        from distr.core.orchestrator_validator import run_orchestrator_validator_judgment

        routes = [dict(item) for item in (validation_routes or []) if isinstance(item, dict)]
        if routes and require_configured_validator:
            verdicts = [
                run_orchestrator_validator_judgment(
                    result=result,
                    validation_prompt=validation_prompt,
                    standards_context=standards_context,
                    ticket_context=ticket_context,
                    mode="independent_primary",
                    route=route,
                )
                for route in routes[:2]
            ]
            available_verdicts = [verdict for verdict in verdicts if verdict is not None]
            if any(verdict is None for verdict in verdicts):
                logger.warning("Configured workflow validator route was unavailable; failing closed")
                return False
            if available_verdicts:
                return all(bool(verdict.get("passed")) for verdict in available_verdicts)

        verdict = run_orchestrator_validator_judgment(
            result=result,
            validation_prompt=validation_prompt,
            standards_context=standards_context,
            ticket_context=ticket_context,
            mode="primary",
        )
        if verdict is not None:
            return bool(verdict.get("passed"))
        if require_configured_validator:
            logger.warning("Independent workflow validation was required but no validator was available; failing closed")
            return False

        try:
            from distr.core.workflow.standards_memory import UNIVERSAL_WORKFLOW_STANDARDS
            standards = "\n\nQUALITY STANDARDS:\n" + UNIVERSAL_WORKFLOW_STANDARDS.strip()
        except Exception:
            standards = ""
        ticket_block = ""
        if (ticket_context or "").strip():
            ticket_block = f"TICKET CONTEXT:\n{ticket_context.strip()}\n\n"
        standards_block = ""
        if (standards_context or "").strip():
            standards_block = f"WORKFLOW STANDARDS:\n{standards_context.strip()}\n\n"
        judgment_prompt = (
            f"You are a validation judge. Evaluate whether the following result passes the validation criteria.\n\n"
            f"VALIDATION CRITERIA:\n{validation_prompt}\n\n"
            f"{ticket_block}"
            f"{standards_block}"
            f"{standards}\n\n"
            f"RESULT TO VALIDATE:\n{result}\n\n"
            f"Respond with exactly PASS or FAIL followed by a brief explanation."
        )
        try:
            from distr.core.agent.services.llm.shared import get_shared_llm_response
            response = get_shared_llm_response(judgment_prompt)
            if response:
                return response.strip().upper().startswith("PASS")
        except ImportError:
            pass
        logger.warning(
            "LLM judgment not available; using explicit caller result fallback=%s",
            unavailable_fallback,
        )
        return bool(unavailable_fallback)
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


def _verify_playwright(step: AutoWorkflowStep, caller_passed: bool, *, base_url: str = "") -> bool:
    """Execute a Playwright validation script. Exit code 0 = passed, non-zero = failed.
    Falls back to caller_passed if validation_code is empty."""
    validation_code = (step.validation_code or "").strip()
    if not validation_code:
        logger.info("No validation_code for step %s, falling back to caller_passed", step.id)
        return caller_passed

    try:
        from distr.core.workflow_engine.test_loop import TestLoopService
        result = TestLoopService()._execute_playwright(validation_code, base_url=base_url or None)
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
