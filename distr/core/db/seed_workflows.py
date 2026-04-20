"""
Seed data for pre-populated workflow templates.

Populates empty draft workflows with default steps. Idempotent — skips
workflows that already have steps.
"""
import logging

from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep

logger = logging.getLogger(__name__)

# ── Workflow step definitions ──

DEVELOPMENT_STEPS = [
    {
        "position": 0,
        "name": "Planning",
        "action_type": "agent_instruction",
        "instruction": (
            "Review the ticket context and produce a concrete plan before changing code. "
            "Call out assumptions, implementation steps, and explicit success criteria."
        ),
        "validation_type": "text_match",
        "validation_prompt": "success criteria",
        "wait_for_continue": False,
    },
    {
        "position": 1,
        "name": "Execution",
        "action_type": "agent_instruction",
        "instruction": (
            "Implement the plan and apply code changes. Keep updates scoped to the ticket, "
            "and report what was changed and why."
        ),
        "validation_type": "rule_based",
        "validation_prompt": "contains: changed\ncontains: implemented\nmin_length: 20",
        "wait_for_continue": False,
    },
    {
        "position": 2,
        "name": "Validation",
        "action_type": "agent_instruction",
        "instruction": (
            "Validate the implementation against the success criteria. Run unit tests and "
            "Playwright checks when applicable. Report which checks passed, and clearly list "
            "any failing requirements."
        ),
        "validation_type": "rule_based",
        "validation_prompt": "contains: pass\ncontains: requirement\nmin_length: 20",
        "wait_for_continue": False,
    },
]

BILLING_STEPS = [
    {
        "position": 0,
        "name": "Research",
        "action_type": "agent_instruction",
        "instruction": "Review pending invoices and billing records. Identify any outstanding payments or discrepancies that need attention.",
    },
    {
        "position": 1,
        "name": "Process",
        "action_type": "agent_instruction",
        "instruction": "Process the identified billing items. Generate invoices, send payment reminders, and record any completed transactions.",
    },
    {
        "position": 2,
        "name": "Verify",
        "action_type": "agent_instruction",
        "instruction": "Verify all processed billing items are accurate. Reconcile records and confirm payments have been applied correctly.",
    },
]

EMAIL_MARKETING_STEPS = [
    {
        "position": 0,
        "name": "Prepare",
        "action_type": "agent_instruction",
        "instruction": "Segment the target audience and draft the email campaign content. Review messaging, subject lines, and call-to-action elements.",
    },
    {
        "position": 1,
        "name": "Send",
        "action_type": "agent_instruction",
        "instruction": "Schedule and send the email campaign to the segmented audience. Ensure delivery settings and tracking pixels are configured.",
    },
    {
        "position": 2,
        "name": "Report",
        "action_type": "agent_instruction",
        "instruction": "Analyze campaign metrics including open rates, click-through rates, and conversions. Generate a summary report with recommendations.",
    },
]

FINDING_PROSPECTS_STEPS = [
    {
        "position": 0,
        "name": "Research",
        "action_type": "agent_instruction",
        "instruction": "Define ideal customer criteria and research potential prospect sources. Identify databases, directories, and platforms to search.",
    },
    {
        "position": 1,
        "name": "Qualify",
        "action_type": "agent_instruction",
        "instruction": "Compile a list of prospects matching the criteria. Enrich contact data and score leads based on fit and engagement potential.",
    },
    {
        "position": 2,
        "name": "Outreach",
        "action_type": "agent_instruction",
        "instruction": "Prepare initial outreach messages for qualified prospects. Personalize messaging based on prospect research and scoring.",
    },
]

FACEBOOK_MARKETING_STEPS = [
    {
        "position": 0,
        "name": "Plan",
        "action_type": "agent_instruction",
        "instruction": "Define the target audience and campaign objectives for Facebook. Research competitor ads and plan creative assets and messaging.",
    },
    {
        "position": 1,
        "name": "Execute",
        "action_type": "agent_instruction",
        "instruction": "Create ad content, set budget and bidding strategy, and launch the Facebook campaign. Configure targeting and placement options.",
    },
    {
        "position": 2,
        "name": "Analyze",
        "action_type": "agent_instruction",
        "instruction": "Monitor campaign performance metrics. Analyze reach, engagement, and conversion data. Recommend optimizations for future campaigns.",
    },
]

LINKEDIN_MARKETING_STEPS = [
    {
        "position": 0,
        "name": "Plan",
        "action_type": "agent_instruction",
        "instruction": "Identify target professionals and companies on LinkedIn. Research their profiles, interests, and recent activity to personalize outreach.",
    },
    {
        "position": 1,
        "name": "Execute",
        "action_type": "agent_instruction",
        "instruction": "Draft and send connection requests and outreach messages. Follow up with engaged prospects and share relevant content.",
    },
    {
        "position": 2,
        "name": "Analyze",
        "action_type": "agent_instruction",
        "instruction": "Track response rates, connection acceptance rates, and conversation outcomes. Update the CRM with engagement data and next steps.",
    },
]

PROSPECT_FOLLOWUP_STEPS = [
    {
        "position": 0,
        "name": "Review",
        "action_type": "agent_instruction",
        "instruction": "Review the current prospect pipeline. Identify prospects requiring follow-up based on last contact date and engagement status.",
    },
    {
        "position": 1,
        "name": "Follow-up",
        "action_type": "agent_instruction",
        "instruction": "Draft and send personalized follow-up messages to identified prospects. Reference previous conversations and provide additional value.",
    },
    {
        "position": 2,
        "name": "Update",
        "action_type": "agent_instruction",
        "instruction": "Log all follow-up responses and update prospect records in the CRM. Adjust lead scores and schedule next follow-up actions.",
    },
]

# Map workflow names to their step definitions
WORKFLOW_SEEDS = {
    "Development": DEVELOPMENT_STEPS,
    "Billing": BILLING_STEPS,
    "Email Marketing": EMAIL_MARKETING_STEPS,
    "Finding Prospects": FINDING_PROSPECTS_STEPS,
    "Facebook Marketing": FACEBOOK_MARKETING_STEPS,
    "LinkedIn Marketing": LINKEDIN_MARKETING_STEPS,
    "Prospect Follow-up": PROSPECT_FOLLOWUP_STEPS,
}


def _seed_steps_for_workflow(db, workflow_id: int, steps_data: list):
    """Create steps for a workflow from seed data definitions."""
    step_objects = []
    for s_data in steps_data:
        step = AutoWorkflowStep(
            workflow_id=workflow_id,
            position=s_data.get("position", 0),
            name=s_data.get("name", "Step"),
            action_type=s_data.get("action_type", "agent_instruction"),
            instruction=s_data.get("instruction", ""),
            validation_type=s_data.get("validation_type", "none"),
            validation_prompt=s_data.get("validation_prompt", ""),
            routing_mode=s_data.get("routing_mode", "static"),
            wait_before_next=s_data.get("wait_before_next", 0),
            timeout_seconds=s_data.get("timeout_seconds", 300),
            max_retries=s_data.get("max_retries", 0),
            require_approval=s_data.get("require_approval", False),
            wait_for_continue=s_data.get("wait_for_continue", False),
        )
        db.add(step)
        step_objects.append((step, s_data))

    # Flush to get IDs, then wire up on_pass_goto routing (position N -> position N+1)
    db.flush()
    position_to_id = {s.position: s.id for s in (obj for obj, _ in step_objects)}
    for step, s_data in step_objects:
        next_pos = step.position + 1
        if next_pos in position_to_id:
            step.on_pass_goto = position_to_id[next_pos]


def seed_workflows(force_reset: bool = False, workflow_names=None):
    """Populate seeded workflows with default steps.

    Args:
        force_reset: If True, replace existing steps for matching seeded workflows.
        workflow_names: Optional iterable of workflow names to seed. Defaults to all seeded names.

    Returns:
        Summary dict with counts and seeded workflow names.
    """
    target_names = set(workflow_names) if workflow_names else set(WORKFLOW_SEEDS.keys())
    seeded_names = []
    skipped_names = []

    with get_session() as db:
        workflows = db.query(AutoWorkflow).all()
        for wf in workflows:
            if wf.name not in WORKFLOW_SEEDS or wf.name not in target_names:
                continue

            has_steps = len(wf.steps) > 0
            if has_steps and not force_reset:
                skipped_names.append(wf.name)
                continue

            if has_steps and force_reset:
                logger.info("Resetting workflow '%s' (id=%d) steps before reseed", wf.name, wf.id)
                for step in list(wf.steps):
                    db.delete(step)
                db.flush()

            logger.info("Seeding workflow '%s' (id=%d) with default steps", wf.name, wf.id)
            _seed_steps_for_workflow(db, wf.id, WORKFLOW_SEEDS[wf.name])
            seeded_names.append(wf.name)

        db.commit()

    return {
        "seeded_count": len(seeded_names),
        "skipped_count": len(skipped_names),
        "seeded_names": seeded_names,
        "skipped_names": skipped_names,
        "force_reset": force_reset,
    }
