"""Workflow Agent Bridge — separates workflow execution from the Voice Agent.

Handles workflow completion notifications, queues run history reports to the
agent LLM via a thread-safe queue, and emits the ``workflow_finished`` signal
so the Voice Agent can react.
"""

import logging
import queue
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Module-level thread-safe queue for agent reports.
# Reports are drained by the agent when it is ready to process them.
_agent_report_queue: queue.Queue = queue.Queue(maxsize=500)


class WorkflowAgentBridge:
    """Bridge between the workflow engine and the Voice Agent."""

    def on_workflow_completed(self, session_id: int, run_result: dict) -> None:
        """Finalize a workflow run and notify the Voice Agent.

        Generates a human-readable summary from *run_result*, queues it for
        the agent LLM, and emits the ``workflow_finished`` signal.
        """
        try:
            report = self._generate_report(run_result)
            self.queue_report_to_agent(session_id, report)
            self.notify_voice_agent(session_id, report)
            logger.info(
                "WorkflowAgentBridge: completed notification for session %d",
                session_id,
            )
        except Exception as e:
            logger.error(
                "WorkflowAgentBridge: on_workflow_completed failed for session %d: %s",
                session_id,
                e,
                exc_info=True,
            )

    def queue_report_to_agent(self, session_id: int, report: str) -> None:
        """Put *report* on the thread-safe agent report queue."""
        _agent_report_queue.put({"session_id": session_id, "report": report})
        logger.debug(
            "WorkflowAgentBridge: queued report for session %d (queue size: %d)",
            session_id,
            _agent_report_queue.qsize(),
        )

    def notify_voice_agent(self, session_id: int, summary: str) -> None:
        """Emit ``workflow_finished`` signal for the Voice Agent."""
        from distr.core.signals import signal_manager

        signal_manager.workflow_finished.emit(session_id, summary)
        logger.debug(
            "WorkflowAgentBridge: emitted workflow_finished for session %d",
            session_id,
        )

    @staticmethod
    def get_pending_reports(session_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Drain the queue and return pending reports.

        If *session_id* is provided, only reports for that session are returned;
        non-matching reports are put back so other sessions can consume them.
        This prevents cross-session contamination when multiple workflows run
        concurrently.
        """
        all_items: List[Dict[str, Any]] = []
        while True:
            try:
                all_items.append(_agent_report_queue.get_nowait())
            except queue.Empty:
                break

        if session_id is None:
            return all_items

        matching: List[Dict[str, Any]] = []
        for item in all_items:
            if item.get("session_id") == session_id:
                matching.append(item)
            else:
                _agent_report_queue.put(item)  # return to queue for the correct consumer
        return matching

    @staticmethod
    def _generate_report(run_result: dict) -> str:
        """Generate a human-readable summary from *run_result*."""
        session_id = run_result.get("session_id", "?")
        run_id = run_result.get("run_id", "?")
        success = run_result.get("success", False)
        cancelled = run_result.get("cancelled", False)

        steps_summary = run_result.get("steps_summary", [])
        total = len(steps_summary)

        if cancelled:
            status_line = f"Done - the workflow was cancelled. Session {session_id}, run {run_id}."
        elif success:
            status_line = f"Done - the workflow finished successfully. Session {session_id}, run {run_id}."
        else:
            status_line = f"The workflow failed. Session {session_id}, run {run_id}."

        # Failed steps: include enough detail for diagnosis (tracebacks, stderr).
        # Passed steps: shorter lines keep voice follow-ups and logs manageable.
        _PASS_SNIPPET = 600
        _FAIL_SNIPPET = 12000

        step_lines = []
        for i, s in enumerate(steps_summary, 1):
            title = s.get("title") or f"Step {i}"
            result = (s.get("result") or "").strip()
            status = s.get("status", "")
            st_lower = (status or "").strip().lower()
            ok = st_lower in ("completed", "passed")
            status_tag = f" [{status}]" if status and not ok else ""
            if result:
                lim = _PASS_SNIPPET if ok else _FAIL_SNIPPET
                short = WorkflowAgentBridge._human_step_result(result)
                short = short[:lim] + ("..." if len(short) > lim else "")
                step_lines.append(f"  {i}. {title}{status_tag}: {short}")
            else:
                step_lines.append(f"  {i}. {title}{status_tag}")

        steps_block = "\n".join(step_lines) if step_lines else "  (no steps)"

        return (
            f"{status_line}\n"
            f"Steps ({total}):\n{steps_block}"
        )

    @staticmethod
    def _human_step_result(result: str) -> str:
        clean = re.sub(r"\s+", " ", str(result or "")).strip()
        if re.search(r"(?i)\bvoice note sent\b", clean):
            return "It sent a Telegram voice note with the requested message."
        clean = re.sub(r"(?i)^voice note sent:\s*", "Sent the Telegram voice note.", clean).strip()
        return clean
