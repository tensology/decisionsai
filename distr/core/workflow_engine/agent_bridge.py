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
        success = run_result.get("success", False)
        cancelled = run_result.get("cancelled", False)

        steps_summary = run_result.get("steps_summary", [])

        all_steps_ok = bool(steps_summary) and all(
            str(s.get("status") or "").strip().lower() in ("completed", "passed")
            for s in steps_summary
        )
        saw_green = any(
            re.search(r"(?i)\bgreen\b|\bvalidation passed\b", str(s.get("result") or ""))
            for s in steps_summary
        )

        if cancelled:
            status_line = "I stopped that run."
        elif success or (all_steps_ok and saw_green):
            status_line = "All done."
        else:
            status_line = "That didn't work out."

        # Failed steps retain a useful error clue. Completed steps are summarized
        # so chat follow-ups do not receive CLI transcripts or callback payloads.
        _PASS_SNIPPET = 600
        _FAIL_SNIPPET = 900

        step_lines = []
        for i, s in enumerate(steps_summary, 1):
            title = s.get("title") or f"Step {i}"
            result = (s.get("result") or "").strip()
            status = s.get("status", "")
            st_lower = (status or "").strip().lower()
            ok = st_lower in ("completed", "passed") or (success and not st_lower)
            if result:
                lim = _PASS_SNIPPET if ok else _FAIL_SNIPPET
                short = WorkflowAgentBridge._human_step_result(result)
                short = short[:lim] + ("..." if len(short) > lim else "")
                if ok:
                    step_lines.append(f"{title}: {short}")
                else:
                    step_lines.append(f"{title} didn't clear: {short}")
            else:
                if ok:
                    step_lines.append(f"{title} finished.")
                else:
                    step_lines.append(f"{title} didn't clear.")

        if not step_lines:
            return f"{status_line}\nNo steps were recorded."

        return f"{status_line}\n" + "\n".join(step_lines)

    @staticmethod
    def _human_step_result(result: str) -> str:
        clean = re.sub(r"\s+", " ", str(result or "")).strip()
        if re.search(r"(?i)\bvoice note sent\b", clean):
            return "It sent a Telegram voice note with the requested message."
        clean = re.sub(r"(?i)^voice note sent:\s*", "Sent the Telegram voice note.", clean).strip()
        if re.search(r"(?i)\bproject cli backend:\s*", clean):
            return WorkflowAgentBridge._summarize_project_cli_result(clean)
        if re.search(r"(?i)\bnode --test\b|\bpytest\b|\btests?\s+\d+\b", clean):
            return WorkflowAgentBridge._summarize_test_result(clean)
        if re.search(r"(?i)\bgreen\b.*\bvalidation passed\b|\bvalidation passed\b", clean):
            return "Green evidence was recorded."
        return clean

    @staticmethod
    def _summarize_project_cli_result(clean: str) -> str:
        backend_match = re.search(r"(?i)\bProject CLI backend:\s*([A-Za-z0-9_-]+)", clean)
        status_match = re.search(r"(?i)\bStatus:\s*([A-Za-z0-9_-]+)", clean)
        backend = backend_match.group(1).replace("_", " ").title() if backend_match else "The selected CLI backend"
        status = (status_match.group(1).lower() if status_match else "")

        if status in ("completed", "passed", "success"):
            return f"{backend} completed the implementation handoff."
        if status:
            return f"{backend} reported status: {status}."
        return f"{backend} started the implementation handoff."

    @staticmethod
    def _summarize_test_result(clean: str) -> str:
        tests = WorkflowAgentBridge._extract_int_after_label(clean, "tests")
        passed = WorkflowAgentBridge._extract_int_after_label(clean, "pass")
        failed = WorkflowAgentBridge._extract_int_after_label(clean, "fail")

        if passed is not None or failed is not None:
            total = tests if tests is not None else ((passed or 0) + (failed or 0))
            return f"Validation passed: {total} tests, {failed or 0} failures." if (failed or 0) == 0 else (
                f"Validation found {failed} failing test(s) out of {total}."
            )
        if re.search(r"(?i)\bpass(ed)?\b|✔", clean):
            return "Validation checks passed."
        return "Validation checks ran."

    @staticmethod
    def _extract_int_after_label(text: str, label: str) -> Optional[int]:
        match = re.search(rf"(?i)(?:^|\s|ℹ)\b{re.escape(label)}\s+(\d+)\b", text)
        if not match:
            return None
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return None
