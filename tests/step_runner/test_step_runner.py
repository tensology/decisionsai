"""
Step Runner tests.

Covers:
  1. Instruction breakdown (simple vs multi-step)
  2. build_step_context_prompt - raw passthrough for single steps
  3. Fast action detection through step runner prompts (the 'run action fuzzy' case)
  4. schedule_to_cron conversion
  5. _next_run_from_cron calculation
  6. Scheduled session step reset logic
  7. list_sessions includes schedule_time / schedule_days

Run with:
    python tests/step_runner/test_step_runner.py
"""

import sys
import os
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS = "✅"
FAIL = "❌"
_failures = []


def check(label: str, condition: bool, detail: str = ""):
    if condition:
        print(f"  {PASS} {label}")
    else:
        msg = f"  {FAIL} {label}" + (f" — {detail}" if detail else "")
        print(msg)
        _failures.append(label)


# ---------------------------------------------------------------------------
# 1. _is_simple_instruction
# ---------------------------------------------------------------------------

def test_simple_instruction_detection():
    print("\n[1] _is_simple_instruction")
    from distr.core.workflow.service import _is_simple_instruction

    # Should be simple (single-step, no breakdown needed)
    simple = [
        "run action fuzzy",
        "open chrome",
        "take a screenshot",
        "check my email",
    ]
    for s in simple:
        check(f"simple: '{s}'", _is_simple_instruction(s), f"returned False")

    # Should NOT be simple (needs breakdown)
    complex_ = [
        "open chrome and then navigate to gmail",
        "first open the terminal, then run git status",
        "check my calendar and reply to urgent emails",
        "step 1: open app, step 2: click settings",
        # Long instruction (>80 chars)
        "open chrome, navigate to gmail, find emails from john, and reply saying I will be there",
    ]
    for s in complex_:
        check(f"complex: '{s}'", not _is_simple_instruction(s), f"returned True (should be False)")


# ---------------------------------------------------------------------------
# 2. build_step_context_prompt — single step passthrough
# ---------------------------------------------------------------------------

def test_build_step_context_prompt():
    print("\n[2] build_step_context_prompt")
    from distr.core.workflow.service import build_step_context_prompt

    # Single step, no prior results → raw instruction passthrough
    result = build_step_context_prompt(
        step_index=0,
        total_steps=1,
        session_instruction="run action fuzzy",
        step_title="Step 1",
        step_instruction="run action fuzzy",
        prior_results=[],
    )
    check(
        "single step returns raw instruction",
        result == "run action fuzzy",
        f"got: {repr(result)}",
    )

    # Multi-step → should include [STEP RUNNER] wrapper
    result_multi = build_step_context_prompt(
        step_index=0,
        total_steps=3,
        session_instruction="Set up project",
        step_title="Create folder",
        step_instruction="Create a folder called my_project",
        prior_results=[],
    )
    check(
        "multi-step includes [STEP RUNNER] header",
        "[STEP RUNNER]" in result_multi,
        f"got: {repr(result_multi[:80])}",
    )
    check(
        "multi-step includes task instruction",
        "Create a folder called my_project" in result_multi,
        "",
    )

    # Step 2 of 3 with prior results → includes previous steps
    result_step2 = build_step_context_prompt(
        step_index=1,
        total_steps=3,
        session_instruction="Set up project",
        step_title="Add README",
        step_instruction="Create a README.md file",
        prior_results=[{"title": "Create folder", "result": "Folder created."}],
    )
    check(
        "step 2 includes prior results",
        "Create folder" in result_step2 and "Folder created." in result_step2,
        "",
    )

    # Single step WITH prior results → should still use wrapper (prior context matters)
    result_single_with_prior = build_step_context_prompt(
        step_index=0,
        total_steps=1,
        session_instruction="run action fuzzy",
        step_title="Step 1",
        step_instruction="run action fuzzy",
        prior_results=[{"title": "Previous", "result": "Done."}],
    )
    check(
        "single step with prior results uses wrapper",
        "[STEP RUNNER]" in result_single_with_prior,
        f"got: {repr(result_single_with_prior[:80])}",
    )

    # --- context_rules tests ---

    # Single step with context_rules → should include [CONTEXT AND RULES] and [STEP RUNNER]
    result_ctx = build_step_context_prompt(
        step_index=0,
        total_steps=1,
        session_instruction="run action fuzzy",
        step_title="Step 1",
        step_instruction="run action fuzzy",
        prior_results=[],
        context_rules="Always respond in JSON format.",
    )
    check(
        "single step with context_rules includes [CONTEXT AND RULES]",
        "[CONTEXT AND RULES]" in result_ctx,
        f"got: {repr(result_ctx[:120])}",
    )
    check(
        "single step with context_rules includes the rules text",
        "Always respond in JSON format." in result_ctx,
        "",
    )
    check(
        "single step with context_rules includes [STEP RUNNER]",
        "[STEP RUNNER]" in result_ctx,
        f"got: {repr(result_ctx[:200])}",
    )

    # Multi-step with context_rules → [CONTEXT AND RULES] before [STEP RUNNER]
    result_multi_ctx = build_step_context_prompt(
        step_index=0,
        total_steps=3,
        session_instruction="Set up project",
        step_title="Create folder",
        step_instruction="Create a folder called my_project",
        prior_results=[],
        context_rules="Use Python 3.12 only.",
    )
    ctx_pos = result_multi_ctx.index("[CONTEXT AND RULES]")
    sr_pos = result_multi_ctx.index("[STEP RUNNER]")
    check(
        "multi-step context_rules appears before [STEP RUNNER]",
        ctx_pos < sr_pos,
        f"ctx_pos={ctx_pos}, sr_pos={sr_pos}",
    )
    check(
        "multi-step context_rules text present",
        "Use Python 3.12 only." in result_multi_ctx,
        "",
    )

    # Empty context_rules → no [CONTEXT AND RULES] section
    result_no_ctx = build_step_context_prompt(
        step_index=0,
        total_steps=3,
        session_instruction="Set up project",
        step_title="Create folder",
        step_instruction="Create a folder called my_project",
        prior_results=[],
        context_rules="",
    )
    check(
        "empty context_rules omits [CONTEXT AND RULES]",
        "[CONTEXT AND RULES]" not in result_no_ctx,
        f"got: {repr(result_no_ctx[:120])}",
    )


# ---------------------------------------------------------------------------
# 3. Fast action detection through step runner prompts
# ---------------------------------------------------------------------------

def test_fast_action_through_step_runner():
    print("\n[3] Fast action detection via step runner prompts")
    from distr.core.agent.services.llm.fast_action_detector import FastActionDetector, ActionType

    detector = FastActionDetector()

    # --- Single step: raw passthrough (the main fix) ---
    raw = "run action fuzzy"
    result = detector.detect(raw)
    check(
        "raw 'run action fuzzy' → ACTION_PLAY",
        result.action_type == ActionType.ACTION_PLAY,
        f"got {result.action_type}",
    )
    check(
        "raw 'run action fuzzy' → action_name is 'fuzzy'",
        result.tool_args.get("action_name", "").strip().lower() == "fuzzy",
        f"got: {repr(result.tool_args.get('action_name'))}",
    )

    # --- Multi-step wrapper: raw instruction is NOT sent to fast action detector ---
    # Wrapped prompts go to WorkflowAgent, not fast-action detection.
    # Only single-step raw passthroughs hit the fast action detector.
    wrapped = (
        "[STEP RUNNER] Executing step 2 of 3.\n"
        "Overall goal: Set up project\n"
        "\n"
        "Previous steps:\n"
        "- Create folder: Folder created.\n"
        "\n"
        "Current step: Run action\n"
        "Task: run action fuzzy\n"
        "\n"
        "Execute this step. When finished, confirm exactly what you accomplished."
    )
    result_wrapped = detector.detect(wrapped)
    # Wrapped prompts are intentionally NOT fast-action detected (they go to WorkflowAgent)
    check(
        "wrapped prompt → not fast-action detected (goes to WorkflowAgent)",
        result_wrapped.action_type == ActionType.UNKNOWN,
        f"got {result_wrapped.action_type}",
    )

    # --- Other fast actions through single-step passthrough ---
    cases = [
        ("run action my_workflow", ActionType.ACTION_PLAY),
        ("play action test", ActionType.ACTION_PLAY),
        # Screenshot needs "my screen" or similar context to trigger fast detection
        ("look at my screen", ActionType.SCREENSHOT_ANALYZE),
        ("can you see my screen", ActionType.SCREENSHOT_ANALYZE),
    ]
    for text, expected_type in cases:
        r = detector.detect(text)
        check(
            f"'{text}' → {expected_type.value}",
            r.action_type == expected_type,
            f"got {r.action_type}",
        )

    # --- Action name extraction for various phrasings ---
    name_cases = [
        ("run action my workflow", "my workflow"),
        ("play action send report", "send report"),
        ("execute action daily backup", "daily backup"),
    ]
    for text, expected_name in name_cases:
        r = detector.detect(text)
        got = r.tool_args.get("action_name", "").strip().lower()
        check(
            f"action_name from '{text}' → '{expected_name}'",
            got == expected_name,
            f"got: {repr(got)}",
        )


# ---------------------------------------------------------------------------
# 4. schedule_to_cron
# ---------------------------------------------------------------------------

def test_schedule_to_cron():
    print("\n[4] schedule_to_cron")
    from distr.core.workflow.scheduler import schedule_to_cron

    cases = [
        # (schedule, schedule_time, schedule_days, expected_cron)
        ("hourly", None, None, "0 * * * *"),
        ("daily", "09:00", None, "0 9 * * *"),
        ("daily", "08:30", None, "30 8 * * *"),
        ("daily", "17:00", None, "0 17 * * *"),
        ("weekly", "09:00", "1", "0 9 * * 1"),
        ("weekly", "14:30", "1,3,5", "30 14 * * 1,3,5"),
        # Raw cron passthrough
        ("0 9 * * 1-5", None, None, "0 9 * * 1-5"),
        # Empty → None
        ("", None, None, None),
        (None, None, None, None),
    ]
    for schedule, stime, sdays, expected in cases:
        result = schedule_to_cron(schedule, stime, None, sdays)
        check(
            f"schedule_to_cron({schedule!r}, {stime!r}, {sdays!r}) → {expected!r}",
            result == expected,
            f"got: {repr(result)}",
        )


# ---------------------------------------------------------------------------
# 5. _next_run_from_cron
# ---------------------------------------------------------------------------

def test_next_run_from_cron():
    print("\n[5] _next_run_from_cron")
    from distr.core.workflow.scheduler import _next_run_from_cron, _utc_offset

    offset = _utc_offset()

    # Daily at 9am local — base is 08:00 local (expressed as UTC)
    # We want croniter to see 08:00 local and return 09:00 local.
    base_local = datetime(2026, 3, 18, 8, 0, 0)
    base_utc = base_local - offset  # convert to UTC for the function
    result = _next_run_from_cron("0 9 * * *", from_dt=base_utc)
    # Result is UTC; convert back to local to check the hour
    result_local = result + offset if result else None
    check(
        "daily 9am from 08:00 local → same day 09:00 local",
        result_local is not None and result_local.hour == 9 and result_local.minute == 0,
        f"got local: {result_local}",
    )

    # Hourly — next run should be 09:00 local
    result_hourly = _next_run_from_cron("0 * * * *", from_dt=base_utc)
    result_hourly_local = result_hourly + offset if result_hourly else None
    check(
        "hourly from 08:00 local → 09:00 local",
        result_hourly_local is not None and result_hourly_local.hour == 9,
        f"got local: {result_hourly_local}",
    )

    # Past time today (17:00 local when it's already 18:00 local) → next day
    base_evening_local = datetime(2026, 3, 18, 18, 0, 0)
    base_evening_utc = base_evening_local - offset
    result_next_day = _next_run_from_cron("0 17 * * *", from_dt=base_evening_utc)
    result_next_day_local = result_next_day + offset if result_next_day else None
    check(
        "daily 17:00 local from 18:00 local → next day",
        result_next_day_local is not None and result_next_day_local.day == 19,
        f"got local: {result_next_day_local}",
    )

    # Invalid cron → None
    result_invalid = _next_run_from_cron("not a cron")
    check(
        "invalid cron → None",
        result_invalid is None,
        f"got: {result_invalid}",
    )


# ---------------------------------------------------------------------------
# 6. Step reset on scheduled run
# ---------------------------------------------------------------------------

def test_step_reset_on_scheduled_run():
    """Verify run_scheduled_workflow resets step statuses.
    
    NOTE: The original run_scheduled_session was removed as part of the
    workflow-step-runner unification.  The new run_scheduled_workflow in
    distr/core/workflow/scheduler.py delegates to start_workflow_run which
    handles step resets.  Scheduler behaviour is covered by property tests
    in tests/core/workflow/.
    """
    print("\n[6] Step reset on scheduled run (skipped — covered by workflow scheduler tests)")
    check("skipped (covered by workflow scheduler property tests)", True, "")


# ---------------------------------------------------------------------------
# 7. list_sessions includes schedule_time and schedule_days
# ---------------------------------------------------------------------------

def test_list_sessions_fields():
    print("\n[7] list_sessions includes schedule_time / schedule_days")

    mock_row = MagicMock()
    mock_row.id = 1
    mock_row.instruction = "check my calendar"
    mock_row.status = "planned"
    mock_row.session_type = "scheduled"
    mock_row.schedule = "weekly"
    mock_row.schedule_time = "09:00"
    mock_row.schedule_days = "1,3,5"
    mock_row.next_run_at = datetime(2026, 3, 23, 9, 0, 0)
    mock_row.enabled = True
    mock_row.created_date = datetime(2026, 3, 18, 10, 0, 0)

    import contextlib

    @contextlib.contextmanager
    def fake_get_session():
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.filter.return_value.filter.return_value \
            .order_by.return_value.limit.return_value.all.return_value = [mock_row]
        mock_db.query.return_value.order_by.return_value.limit.return_value.all.return_value = [mock_row]
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        yield mock_db

    with patch("distr.core.workflow.service.get_session", fake_get_session):
        from distr.core.workflow.service import list_sessions
        results = list_sessions()

    check("list_sessions returns results", len(results) == 1, f"got {len(results)}")
    if results:
        r = results[0]
        check("schedule_time present", "schedule_time" in r, f"keys: {list(r.keys())}")
        check("schedule_days present", "schedule_days" in r, f"keys: {list(r.keys())}")
        check("schedule_time value correct", r.get("schedule_time") == "09:00", f"got: {r.get('schedule_time')}")
        check("schedule_days value correct", r.get("schedule_days") == "1,3,5", f"got: {r.get('schedule_days')}")
        check("next_run_at is ISO string", isinstance(r.get("next_run_at"), str), f"got: {type(r.get('next_run_at'))}")


# ---------------------------------------------------------------------------
# 8. plan_session — LLM breakdown vs single-step fallback
# ---------------------------------------------------------------------------

def test_plan_session_breakdown():
    print("\n[8] plan_session step breakdown")
    from distr.core.workflow.service import _is_simple_instruction

    # Verify the threshold: instructions that should go to LLM
    should_break = [
        "open chrome and navigate to gmail and reply to the first email",
        "check my calendar, find free slots, and send an invite to john",
        "create a new folder on the desktop, add a README file, and open it in vscode",
    ]
    for s in should_break:
        check(
            f"should break down: '{s[:50]}...'",
            not _is_simple_instruction(s),
            "was treated as simple",
        )

    # Instructions that should NOT go to LLM (fast path)
    should_not_break = [
        "run action fuzzy",
        "take a screenshot",
        "open chrome",
        "check email",
    ]
    for s in should_not_break:
        check(
            f"should NOT break down: '{s}'",
            _is_simple_instruction(s),
            "was treated as complex",
        )


# ---------------------------------------------------------------------------
# 9. _parseCustomDesc (JS logic ported to Python for unit testing)
# ---------------------------------------------------------------------------

def _parse_custom_desc_py(desc: str):
    """
    Python port of the JS _parseCustomDesc function for unit testing.
    Returns {"cron": str, "label": str} or None.
    """
    import re
    desc = desc.strip().lower()

    time_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', desc)
    h, m = 9, 0
    if time_match:
        h = int(time_match.group(1))
        m = int(time_match.group(2)) if time_match.group(2) else 0
        if time_match.group(3) == "pm" and h < 12:
            h += 12
        if time_match.group(3) == "am" and h == 12:
            h = 0

    time_label = (str(h % 12 or 12)) + (":" + str(m).zfill(2) if m else "") + ("am" if h < 12 else "pm")

    m_hours = re.search(r'every\s+(\d+)\s+hours?', desc)
    if m_hours:
        n = m_hours.group(1)
        return {"cron": f"0 */{n} * * *", "label": f"Every {n} hours"}
    if re.search(r'every\s+hour', desc):
        return {"cron": "0 * * * *", "label": "Every hour"}

    m_mins = re.search(r'every\s+(\d+)\s+min', desc)
    if m_mins:
        n = m_mins.group(1)
        return {"cron": f"*/{n} * * * *", "label": f"Every {n} minutes"}

    if re.search(r'weekday|mon.*fri|work\s*day', desc):
        if not time_match:
            return {"cron": "0 9 * * 1-5", "label": "Weekdays at 9am"}
        return {"cron": f"{m} {h} * * 1-5", "label": f"Weekdays at {time_label}"}

    if re.search(r'weekend', desc):
        if not time_match:
            return {"cron": "0 9 * * 0,6", "label": "Weekends at 9am"}
        return {"cron": f"{m} {h} * * 0,6", "label": f"Weekends at {time_label}"}

    if re.search(r'daily|every\s+day', desc):
        if not time_match:
            return {"cron": "0 9 * * *", "label": "Daily at 9am"}
        return {"cron": f"{m} {h} * * *", "label": f"Daily at {time_label}"}

    day_map = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
    day_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    found_days = sorted([v for k, v in day_map.items() if k in desc])
    if found_days:
        day_str = ",".join(str(d) for d in found_days)
        day_label = ", ".join(day_names[d] for d in found_days)
        if not time_match:
            return {"cron": f"0 9 * * {day_str}", "label": f"{day_label} at 9am"}
        return {"cron": f"{m} {h} * * {day_str}", "label": f"{day_label} at {time_label}"}

    if time_match:
        return {"cron": f"{m} {h} * * *", "label": f"Daily at {time_label}"}

    return None


def test_parse_custom_desc():
    print("\n[9] _parseCustomDesc")

    cases = [
        # (input, expected_cron, expected_label_contains)
        ("every hour",               "0 * * * *",        "Every hour"),
        ("every 2 hours",            "0 */2 * * *",      "Every 2 hours"),
        ("every 30 minutes",         "*/30 * * * *",     "Every 30 minutes"),
        ("every weekday at 9am",     "0 9 * * 1-5",      "Weekdays"),
        ("every weekday at 8:30am",  "30 8 * * 1-5",     "Weekdays"),
        ("mon-fri at 5pm",           "0 17 * * 1-5",     "Weekdays"),
        ("workday at 10am",          "0 10 * * 1-5",     "Weekdays"),
        ("every weekend",            "0 9 * * 0,6",      "Weekends"),
        ("weekend at 10am",          "0 10 * * 0,6",     "Weekends"),
        ("daily at 9am",             "0 9 * * *",        "Daily"),
        ("every day at 6pm",         "0 18 * * *",       "Daily"),
        ("daily at 8:30am",          "30 8 * * *",       "Daily"),
        ("mon at 9am",               "0 9 * * 1",        "Mon"),
        ("tue and thu at 3pm",       "0 15 * * 2,4",     "Tue"),
        ("9am",                      "0 9 * * *",        "Daily"),
        ("17:00",                    "0 17 * * *",       "Daily"),
        # Unrecognised → None
        ("",                         None,               None),
        ("something random",         None,               None),
    ]

    for desc, expected_cron, expected_label_part in cases:
        result = _parse_custom_desc_py(desc)
        if expected_cron is None:
            check(
                f"'{desc}' → None",
                result is None,
                f"got: {result}",
            )
        else:
            check(
                f"'{desc}' → cron={expected_cron!r}",
                result is not None and result["cron"] == expected_cron,
                f"got: {result}",
            )
            if expected_label_part and result:
                check(
                    f"'{desc}' → label contains '{expected_label_part}'",
                    expected_label_part.lower() in result["label"].lower(),
                    f"got label: {result.get('label')}",
                )




# ---------------------------------------------------------------------------
# Run all
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("Step Runner Tests")
    print("=" * 60)

    test_simple_instruction_detection()
    test_build_step_context_prompt()
    test_fast_action_through_step_runner()
    test_schedule_to_cron()
    test_next_run_from_cron()
    test_step_reset_on_scheduled_run()
    test_list_sessions_fields()
    test_plan_session_breakdown()
    test_parse_custom_desc()

    print("\n" + "=" * 60)
    if _failures:
        print(f"FAILED: {len(_failures)} test(s)")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print(f"All tests passed (9 suites)")
