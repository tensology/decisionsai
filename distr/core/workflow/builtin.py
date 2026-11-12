"""Built-in Development workflows the orchestrator can select (2026-11-11, split 2026-11-12).

Five bundle files are seeded as built-in workflow rows:
  new-work-design-to-plan       New Work: Plan wire DSL design -> approval -> derived plan -> tickets
  existing-project-to-plan      Existing Project: scan/ERD/pages/history -> Requirements Pack -> review
  development-implement-verify  Development (slim): understand -> contract -> implement -> review -> correct
                                -> final verification -> report. Never deploys.
  deploy-to-development         Deploy to Development: evidence -> its own approval -> deploy -> verify -> report
  deploy-to-production          Deploy to Production: evidence -> its own approval -> deploy -> verify -> report

The two deploy workflows are separate, each carries its own approval gate, and
nothing chains them: Development never starts a deploy, and Deploy to
Development never starts Deploy to Production. A deploy is selected only when
the request itself asks to deploy (and not also to implement).

``development-ticket-to-implementation`` (the legacy 11-step everything
workflow) stays a retired slug: never seeded, never selected for new work. Any
existing row or in-flight run that uses it is left untouched.

Seeded rows are materialised idempotently as ordinary, visible workflow rows
marked with ``workflow_input.builtin_slug``. Seeding never runs under
DECISIONS_TEST_MODE unless forced, never touches boards (no default_workflow_id
is written), and never deletes.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEVELOPMENT = "development-ticket-to-implementation"  # legacy 11-step preset; retired, never seeded
NEW_WORK = "new-work-design-to-plan"
EXISTING_PROJECT = "existing-project-to-plan"
DEVELOPMENT_SLIM = "development-implement-verify"
DEPLOY_DEV = "deploy-to-development"
DEPLOY_PROD = "deploy-to-production"
DEPLOY_SLUGS = (DEPLOY_DEV, DEPLOY_PROD)
BUILTIN_SLUGS = (NEW_WORK, EXISTING_PROJECT, DEVELOPMENT_SLIM, DEPLOY_DEV, DEPLOY_PROD)
RETIRED_BUILTIN_SLUGS = (DEVELOPMENT,)
KNOWN_BUILTIN_SLUGS = BUILTIN_SLUGS + RETIRED_BUILTIN_SLUGS
BUILTIN_VERSION = 2
BUILTIN_LABELS = {
    NEW_WORK: "New Work", EXISTING_PROJECT: "Existing Project", DEVELOPMENT_SLIM: "Development",
    DEPLOY_DEV: "Deploy to Development", DEPLOY_PROD: "Deploy to Production", DEVELOPMENT: "Development (legacy 11-step)",
}

_LOCK = threading.Lock()
_SEEDED = False

_NEW_STRONG = re.compile(
    r"\b(new (project|site|website|web ?app|webapp|app|product|platform|portal)|from scratch|greenfield|"
    r"start(ing)? a new|brand[- ]new (site|app|project))\b", re.I)
_NEW_DESIGN = re.compile(
    r"\b(design (the |a |new |our )?(site|website|pages?|screens?|ui|interface|layout)|wireframes?|mock-?ups?|"
    r"site ?map|page designs?|preview the site)\b", re.I)
_EXISTING = re.compile(
    r"\b(reverse[- ]engineer\w*|existing (project|codebase|code ?base|site|website|app|system)|"
    r"scan (the |this )?(project|repo|repository|codebase)|map (the |this )?(project|codebase|data model|database)|"
    r"build (out )?(the )?(erd|plan|data model)|requirements? pack|document (the |this )?(project|codebase)|"
    r"onboard (this|the) project|refresh (the )?plan)\b", re.I)
# Deploy intent. Negations ("do not deploy", "without deploying") are removed first.
_DEPLOY_NEGATION = re.compile(
    r"\b(do not|don't|dont|never|without|no|not|before)\s+(\w+\s+){0,2}?(deploy\w*|releas\w*|promot\w*|go(ing)? live|push\w* (it |this )?live)",
    re.I)
_DEPLOY = re.compile(
    r"\b(deploy\w*|redeploy\w*|(releas|promot)\w* (\S+ ){0,3}?to (prod\w*|live|dev\w*|staging)|roll ?out to|"
    r"go(ing)?[- ]live|push\w* (it |this |the \w+ )?(live|to (prod|production|staging|dev|development)))\b", re.I)
_PROD_ENV = re.compile(r"\b(prod|production|live|go(ing)?[- ]live|promot\w*)\b", re.I)
_DEV_ENV = re.compile(r"\b(dev|development|staging|test (server|environment)|preview (server|environment))\b", re.I)
# Implementation verbs. "build", "update" and "make" are left out on purpose: they
# appear in pure deploy requests ("deploy the build", "deploy the update").
_IMPLEMENT = re.compile(
    r"\b(implement\w*|fix\w*|bug\w*|add(s|ed|ing)?|chang(e|es|ed|ing)|refactor\w*|creat(e|es|ed|ing)|feature\w*|"
    r"wire (up|in)|rewrit\w*|redesign\w*|remov(e|es|ed|ing)|replac(e|es|ed|ing)|migrat(e|es|ed|ing)|"
    r"rename\w*|support (for )?\w+|code (the|a|this))\b", re.I)
_CODE_MARKERS = (
    "package.json", "manage.py", "pyproject.toml", "requirements.txt", "setup.py", "go.mod", "Cargo.toml",
    "composer.json", "Gemfile", "pom.xml", "build.gradle", "index.html", "src", "app", "lib", "distr",
)


def _input_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def builtin_slug_of(workflow: Any) -> Optional[str]:
    """The built-in slug recorded on a workflow row or serialized workflow dict."""
    raw = workflow.get("workflow_input") if isinstance(workflow, dict) else getattr(workflow, "workflow_input", None)
    slug = _input_dict(raw).get("builtin_slug")
    return slug if slug in KNOWN_BUILTIN_SLUGS else None


def is_deploy_workflow(workflow: Any) -> bool:
    return builtin_slug_of(workflow) in DEPLOY_SLUGS


def bundle_path(slug: str) -> Path:
    from distr.core.workflow.loop_preset_loader import bundles_dir

    return bundles_dir() / f"{slug}.json"


def load_builtin_bundle(slug: str) -> Optional[dict[str, Any]]:
    if slug not in KNOWN_BUILTIN_SLUGS:
        return None
    path = bundle_path(slug)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def builtin_workflow_ids(*, include_archived: bool = False) -> dict[str, int]:
    """slug -> lowest workflow id carrying that built-in marker."""
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow

    found: dict[str, int] = {}
    with get_session() as db:
        query = db.query(AutoWorkflow.id, AutoWorkflow.workflow_input, AutoWorkflow.status).filter(
            AutoWorkflow.workflow_input.like('%"builtin_slug"%'))
        for wf_id, raw, status in query.order_by(AutoWorkflow.id).all():
            if not include_archived and str(status or "").lower() == "archived":
                continue
            slug = _input_dict(raw).get("builtin_slug")
            if slug in BUILTIN_SLUGS and slug not in found:
                found[slug] = int(wf_id)
    return found


def _mark(workflow_id: int, slug: str) -> None:
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow

    with get_session() as db:
        wf = db.get(AutoWorkflow, int(workflow_id))
        if wf is None:
            return
        data = _input_dict(wf.workflow_input)
        data.update({
            "builtin_slug": slug,
            "builtin_version": BUILTIN_VERSION,
            # Built-ins never chain into another workflow. Deploys need their own approval.
            "auto_chain": False,
            "chains_to": [],
            "requires_own_approval": slug in DEPLOY_SLUGS,
            "selectable_only_on_explicit_request": slug in DEPLOY_SLUGS,
        })
        data.pop("skip_human_checkpoints", None)
        wf.workflow_input = json.dumps(data)
        db.commit()


def ensure_builtin_workflows(*, force: bool = False) -> dict[str, int]:
    """Create any missing built-in workflow rows. Idempotent; returns slug -> id.

    An archived built-in counts as present (Paul retired it), so it is not recreated.
    Retired slugs (the legacy 11-step Development) are never seeded, even if no row exists.
    Existing rows (and their runs) are never rewritten here.
    """
    global _SEEDED
    if not force and os.environ.get("DECISIONS_TEST_MODE") == "1":
        return builtin_workflow_ids()
    with _LOCK:
        if _SEEDED and not force:
            return builtin_workflow_ids()
        from distr.core.workflow.loop_presets import apply_loop_bundle
        from distr.core.workflow.service import create_workflow, delete_workflow

        present = builtin_workflow_ids(include_archived=True)
        for slug in BUILTIN_SLUGS:
            if slug in present:
                continue
            bundle = load_builtin_bundle(slug)
            if not bundle:
                logger.warning("Built-in workflow bundle missing: %s", slug)
                continue
            wf_id = create_workflow(name=str(bundle.get("name") or slug), description=str(bundle.get("description") or ""))
            result = apply_loop_bundle(wf_id, bundle, mode="replace")
            if not result.get("success"):
                delete_workflow(wf_id)
                logger.warning("Built-in workflow %s could not be applied: %s", slug, result.get("error"))
                continue
            _mark(wf_id, slug)
        _SEEDED = True
    return builtin_workflow_ids()


def reset_builtin_workflow(workflow_id: int) -> dict[str, Any]:
    """Restore a built-in row's steps from its bundle (used instead of deleting it)."""
    from distr.core.workflow.loop_presets import apply_loop_bundle
    from distr.core.workflow.service import get_workflow

    workflow = get_workflow(int(workflow_id))
    slug = builtin_slug_of(workflow or {})
    if not slug:
        return {"success": False, "error": "Not a built-in workflow."}
    bundle = load_builtin_bundle(slug)
    if not bundle:
        return {"success": False, "error": "The built-in definition is unavailable."}
    result = apply_loop_bundle(int(workflow_id), bundle, mode="replace")
    if result.get("success"):
        _mark(int(workflow_id), slug)
    return result


def project_has_code(project_folder: Optional[str]) -> Optional[bool]:
    """None when no folder is linked/available; else whether it looks like an existing codebase."""
    if not project_folder:
        return None
    try:
        root = Path(project_folder).expanduser()
        if not root.is_dir():
            return None
        names = {entry.name for entry in root.iterdir()}
    except OSError:
        return None
    if names & set(_CODE_MARKERS):
        return True
    return any(name.endswith((".py", ".js", ".ts", ".php", ".rb", ".go", ".html")) for name in names)


def deploy_intent(text: str) -> Optional[str]:
    """'production' / 'development' when the text asks to deploy, else None (negations ignored)."""
    cleaned = _DEPLOY_NEGATION.sub(" ", str(text or ""))
    if not _DEPLOY.search(cleaned):
        return None
    if _DEV_ENV.search(cleaned):
        return "development"  # dev first, even when production is also named
    if _PROD_ENV.search(cleaned):
        return "production"
    return "development"  # recommended default: an unqualified deploy targets development first


def implement_intent(text: str) -> bool:
    return bool(_IMPLEMENT.search(str(text or "")))


def _plan_summary(plan: Any) -> Optional[dict[str, Any]]:
    if not plan:
        return None
    if "sections" in plan:
        from distr.core.planning.sections import plan_sections_summary

        return plan_sections_summary(plan)
    return plan if isinstance(plan, dict) else None


def _plan_note(summary: Optional[dict[str, Any]]) -> str:
    if not summary:
        return ""
    have = [f"{label} ({summary.get(key, 0)})" for key, label in
            (("interfaces", "Interfaces"), ("data", "Data"), ("documents", "Documents")) if summary.get(key)]
    missing = summary.get("missing") or []
    bits = []
    if have:
        bits.append("Plan read: " + ", ".join(have) + ".")
    if missing:
        bits.append("Plan has no " + " or ".join(missing) + " yet.")
    return " ".join(bits)


def discern_builtin(request_text: str, *, project_folder: Optional[str] = None, has_code: Optional[bool] = None,
                    plan: Any = None) -> dict[str, Any]:
    """Decide which built-in structure fits a ticket/thread, using the request and the Plan.

    Returns {"slug", "reason", "signals"}. ``slug`` is None when no built-in fits
    (the reason then explains why). Deploy is chosen only for a pure deploy
    request; implement-and-deploy requests get Development, with deploy left as
    a separate, separately approved workflow.
    """
    from distr.core.workflow.selection import classify_work_request

    text = str(request_text or "")
    if has_code is None:
        has_code = project_has_code(project_folder)
    summary = _plan_summary(plan)
    deploy = deploy_intent(text)
    implement = implement_intent(text)
    software = bool(classify_work_request(text).get("software"))
    signals = {"deploy_intent": deploy, "implement_intent": implement, "software": software,
               "has_code": has_code, "plan": summary}
    note = _plan_note(summary)

    def out(slug: Optional[str], reason: str, *, with_note: bool = False) -> dict[str, Any]:
        return {"slug": slug, "reason": (reason + (" " + note if with_note and note else "")).strip(), "signals": signals}

    if deploy and not implement:
        if deploy == "production":
            return out(DEPLOY_PROD, "The request asks to deploy to production; Deploy to Production runs with its own approval.")
        return out(DEPLOY_DEV, "The request asks to deploy to development; Deploy to Development runs with its own approval.")
    if _NEW_STRONG.search(text) and not _EXISTING.search(text):
        return out(NEW_WORK, "The request starts new work; design the pages first.")
    if _NEW_DESIGN.search(text) and has_code is not True:
        return out(NEW_WORK, "Design work on a project without existing code.")
    if _EXISTING.search(text) and has_code is not False:
        return out(EXISTING_PROJECT, "The request asks to map an existing project into the plan.")
    if has_code is False:
        if summary and summary.get("interfaces"):
            return out(DEVELOPMENT_SLIM, "The project has no code yet but its Plan already has Interfaces; implement "
                                         "and verify from that design.", with_note=True)
        return out(NEW_WORK, "The linked project folder has no code yet.")
    if software or implement:
        reason = "Implementation work: Development implements and verifies, and never deploys."
        if deploy:
            reason += (f" Deployment was also asked for: run Deploy to {deploy.title()} separately after Development "
                       "passes; it needs its own approval and is never started automatically.")
        if has_code is True and summary and not summary.get("interfaces") and not summary.get("data"):
            reason += " Consider Existing Project first to map Interfaces and Data into the Plan."
        return out(DEVELOPMENT_SLIM, reason, with_note=True)
    return out(None, "None of the built-in workflows fits: this is not software delivery, deployment, new-project design "
                     "or existing-project mapping. Run it directly, or create a specialized workflow.")


def choose_builtin_slug(request_text: str, *, project_folder: Optional[str] = None,
                        has_code: Optional[bool] = None, plan: Any = None) -> tuple[Optional[str], str]:
    """Pick which built-in fits the request: (slug, reason). See ``discern_builtin``."""
    decision = discern_builtin(request_text, project_folder=project_folder, has_code=has_code, plan=plan)
    return decision["slug"], decision["reason"]


def _project_folder_and_plan(ticket: Any, board: Any) -> tuple[Optional[str], Any]:
    project_id = getattr(ticket, "linked_project_id", None) or getattr(board, "default_project_id", None)
    if not project_id:
        return None, None
    from distr.core.db import get_session
    from distr.core.db.projects import Project

    with get_session() as db:
        project = db.get(Project, int(project_id))
        folder = project.folder_location if project else None
    plan = None
    try:
        from distr.core.planning.sections import plan_sections

        plan = plan_sections(int(project_id))
    except Exception as exc:
        logger.debug("Plan sections unavailable for selection: %s", exc)
    return folder, plan


def builtin_recommendation(ticket: Any, board: Any) -> Optional[tuple[int, str]]:
    """Orchestrator fallback when a ticket has no workflow and its board has no default.

    Explicit new/existing/deploy requests get their built-in; high-complexity
    implementation gets slim Development. Ordinary tickets keep running direct.
    Uses the ticket text plus the project's Plan (Interfaces, Data, Documents).
    The recommendation is still a draft that needs review before it starts, and
    a deploy additionally stops at its own approval gate. Never seeds, never writes.
    """
    text = "\n".join(str(getattr(ticket, field, "") or "") for field in ("title", "description"))
    complexity = str(getattr(ticket, "complexity", "") or "").strip().casefold()
    explicit = bool(_NEW_STRONG.search(text) or _NEW_DESIGN.search(text) or _EXISTING.search(text)
                    or (deploy_intent(text) and not implement_intent(text)))
    if not explicit and complexity not in {"high", "complex", "very_complex"}:
        return None
    try:
        ids = builtin_workflow_ids()
        if not ids:
            return None
        folder, plan = _project_folder_and_plan(ticket, board)
        slug, reason = choose_builtin_slug(text, project_folder=folder, plan=plan)
        workflow_id = ids.get(slug) if slug else None
        if workflow_id is not None:
            return workflow_id, reason
    except Exception as exc:  # selection must never break ticket scanning
        logger.debug("Built-in assignment skipped: %s", exc)
    return None
