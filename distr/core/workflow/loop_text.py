"""Shared loop kickoff footer text (avoids import cycles)."""

SELF_PACE_FOOTER = (
    "Self-pace this loop. After each iteration, run the check command, read the output, "
    "and only continue if the exit condition is not met. Stop when the exit condition "
    "passes or max iterations is reached. Give a short status update each pass."
)

GUARDRAILS_FOOTER = """Guardrails (do not skip):
- Do not modify the check command or exit criteria to force success
- Do not skip, disable, or bypass checks to pass the exit condition
- If stuck after several iterations, stop and report blockers instead of gaming metrics"""
