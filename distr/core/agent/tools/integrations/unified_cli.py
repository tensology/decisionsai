"""
Unified CLI dispatch — ensures the project's pi RPC session exists
and sends prompts through it so the CLI tab shows real-time output.
"""
import logging
import re
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# Serialises get-or-create so two concurrent dispatches cannot each spawn a
# ``pi --mode rpc`` process for the same project.
_ensure_lock = threading.Lock()

# ``pi -p`` can exit 0 while only printing a provider/model error (402 no
# credit, missing key, 429). Only unambiguous error lines count.
_PI_ERROR_LINE = re.compile(
    r"(?im)^\s*(?:error|fatal)\b\s*[:\-]"
    r"|no api key (?:found|configured|set)"
    r"|insufficient (?:credits|balance|funds)"
    r"|\b(?:401|402|403|429)\b[^\n]{0,40}\b(?:unauthori[sz]ed|forbidden|payment required|credits?|rate.?limit|too many requests)"
)


def pi_oneshot_error(returncode: int, output: str) -> str:
    """Return a failure reason for a ``pi -p`` run, or "" when it succeeded.

    Exit code alone is not enough: Pi prints provider errors and exits 0.
    """
    text = (output or "").strip()
    if returncode != 0:
        return f"pi exited with code {returncode}"
    if not text:
        return "pi produced no output"
    match = _PI_ERROR_LINE.search(text)
    if match:
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        return text[line_start: line_end if line_end != -1 else None].strip()[:300]
    return ""


def ensure_rpc_session(project_id: int, cwd: str, append_system_prompt: str = "") -> Optional["PiRpcSession"]:
    """Get or create the pi RPC session for a project.
    Guarantees the session exists and is alive, so the CLI WebSocket
    can connect and show real-time output. Returns None if pi cannot start.
    """
    from distr.core.pi_rpc import get_rpc_session, PiRpcSession, _rpc_sessions, _rpc_key

    # The registry is keyed by (project_id, board_id); storing under the bare
    # int meant get_rpc_session never found it and every dispatch spawned (and
    # orphaned) a new pi process.
    key = _rpc_key(project_id, None)
    with _ensure_lock:
        rpc = get_rpc_session(project_id)
        if rpc and rpc.is_alive:
            return rpc

        # Clean up dead session
        if rpc:
            try:
                rpc._running = False
                if rpc._process:
                    rpc._process.terminate()
                    rpc._process.wait(timeout=3)
            except Exception:
                logger.debug("ensure_rpc_session: dead session cleanup failed", exc_info=True)
            _rpc_sessions.pop(key, None)

        # Create new session
        rpc = PiRpcSession(project_id, cwd, append_system_prompt=append_system_prompt)
        if not rpc.start():
            logger.error(f"Failed to start pi RPC session for project {project_id}")
            return None

        _rpc_sessions[key] = rpc
    logger.info(f"Created pi RPC session for project {project_id} (cwd={cwd})")
    return rpc


def dispatch_to_cli(project_id: int, cwd: str, instruction: str,
                    project_name: str = "project",
                    append_system_prompt: str = "",
                    ticket_id: Optional[int] = None) -> dict:
    """Send an instruction to the project's pi CLI.
    
    Returns dict with:
      - success: bool
      - message: str (human-readable result)
      - method: "rpc" or "oneshot"
    
    This is the SINGLE entry point that all agent tools should use
    when pushing anything to the CLI. It ensures:
    1. The RPC session exists (created if needed)
    2. The prompt goes through send_prompt() (not send_and_wait)
    3. The CLI tab will show real-time output via the WebSocket
    """
    rpc = ensure_rpc_session(project_id, cwd, append_system_prompt)

    if rpc and rpc.is_alive:
        success = rpc.send_prompt(
            instruction,
            origin="desktop",
            ticket_id_for_writeback=ticket_id,
        )
        if success:
            logger.info(f"dispatch_to_cli: sent via RPC to project {project_id}")
            return {
                "success": True,
                "message": f"Sent to {project_name} CLI. Check the CLI tab for progress.",
                "method": "rpc"
            }

    # Fallback: one-shot pi -p (no real-time feed, but at least it runs)
    try:
        import subprocess
        result = subprocess.run(
            ["pi", "-p", "--append-system-prompt",
             append_system_prompt or f"You are working on project: {project_name}",
             instruction],
            capture_output=True, text=True, timeout=600,
            stdin=subprocess.DEVNULL,
            cwd=cwd,
        )
        output = (result.stdout + result.stderr).strip()[:2000]
        error = pi_oneshot_error(result.returncode, output)
        logger.info(f"dispatch_to_cli: one-shot for project {project_id}, {len(output)} chars")
        if error:
            logger.warning("dispatch_to_cli: one-shot for project %s failed: %s", project_id, error)
        if ticket_id is not None:
            try:
                from distr.core.kanban.ticket_writeback import append_pi_cli_summary_to_ticket

                outcome = "failed" if error else "completed"
                append_pi_cli_summary_to_ticket(
                    ticket_id,
                    output or "(no output)",
                    outcome_status=outcome,
                )
            except Exception:
                logger.debug("dispatch_to_cli: ticket write-back failed", exc_info=True)
        return {
            "success": not error,
            "message": output or error or "Completed with no output.",
            "method": "oneshot"
        }
    except Exception as e:
        logger.error(f"dispatch_to_cli: one-shot failed: {e}")
        if ticket_id is not None:
            try:
                from distr.core.kanban.ticket_writeback import append_pi_cli_summary_to_ticket

                append_pi_cli_summary_to_ticket(ticket_id, str(e), outcome_status="failed")
            except Exception:
                logger.debug("dispatch_to_cli: ticket write-back on error failed", exc_info=True)
        return {
            "success": False,
            "message": f"Failed: {e}",
            "method": "oneshot"
        }
