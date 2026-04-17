"""
Unified CLI dispatch — ensures the project's pi RPC session exists
and sends prompts through it so the CLI tab shows real-time output.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def ensure_rpc_session(project_id: int, cwd: str, append_system_prompt: str = "") -> "PiRpcSession":
    """Get or create the pi RPC session for a project.
    Guarantees the session exists and is alive, so the CLI WebSocket
    can connect and show real-time output.
    """
    from distr.core.pi_rpc import get_rpc_session, PiRpcSession, _rpc_sessions

    rpc = get_rpc_session(project_id)
    if rpc and rpc.is_alive:
        return rpc

    # Clean up dead session
    if rpc:
        try:
            rpc._running = False
            if rpc._process:
                rpc._process.terminate()
        except Exception:
            pass
        _rpc_sessions.pop(project_id, None)

    # Create new session
    rpc = PiRpcSession(project_id, cwd, append_system_prompt=append_system_prompt)
    started = rpc.start()
    if not started:
        logger.error(f"Failed to start pi RPC session for project {project_id}")
        return None

    _rpc_sessions[project_id] = rpc
    logger.info(f"Created pi RPC session for project {project_id} (cwd={cwd})")
    return rpc


def dispatch_to_cli(project_id: int, cwd: str, instruction: str,
                    project_name: str = "project",
                    append_system_prompt: str = "") -> dict:
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
        success = rpc.send_prompt(instruction, origin="desktop")
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
            cwd=cwd,
        )
        output = (result.stdout + result.stderr).strip()[:2000]
        logger.info(f"dispatch_to_cli: one-shot for project {project_id}, {len(output)} chars")
        return {
            "success": result.returncode == 0,
            "message": output or "Completed with no output.",
            "method": "oneshot"
        }
    except Exception as e:
        logger.error(f"dispatch_to_cli: one-shot failed: {e}")
        return {
            "success": False,
            "message": f"Failed: {e}",
            "method": "oneshot"
        }
