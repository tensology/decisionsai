"""
Rube Service - Runs Rube MCP commands in a separate thread

This service manages Rube MCP tool execution in a separate thread with
feedback mechanisms and result handling.
"""

import logging
import threading
import queue
import time
from typing import Optional, Dict, Any, List, Callable
import os

logger = logging.getLogger(__name__)


def _run_rube_command(
    command_queue: queue.Queue,
    result_queue: queue.Queue,
    status_queue: queue.Queue,
    rube_token: str
):
    """
    Run Rube MCP commands in a separate thread.
    
    Args:
        command_queue: Queue for receiving commands
        result_queue: Queue for sending results
        status_queue: Queue for sending status updates
        rube_token: Rube MCP token for authentication
    """
    # Set up logging in the thread
    process_logger = logging.getLogger(f"{__name__}.thread")
    
    try:
        process_logger.info("Rube service thread starting...")
        status_queue.put({"status": "initializing", "message": "Starting Rube service..."})
        
        # Set the Rube token as environment variable for MCP tools
        os.environ["RUBE_TOKEN"] = rube_token
        
        status_queue.put({"status": "ready", "message": "Rube service ready"})
        process_logger.info("✓ Rube service ready")
        
        while True:
            try:
                # Get command from queue (with timeout to allow checking for stop)
                try:
                    command_data = command_queue.get(timeout=1.0)
                except queue.Empty:
                    continue
                
                if command_data.get("action") == "stop":
                    process_logger.info("Received stop command")
                    break
                
                if command_data.get("action") == "execute":
                    task = command_data.get("task", "")
                    context = command_data.get("context", {})
                    
                    process_logger.info(f"Executing Rube task: {task}")
                    status_queue.put({"status": "executing", "message": f"Executing: {task}"})
                    
                    try:
                        # Import MCP tools here to avoid import issues
                        from mcp_rube import (
                            RUBE_SEARCH_TOOLS,
                            RUBE_MULTI_EXECUTE_TOOL,
                            RUBE_MANAGE_CONNECTIONS
                        )
                        
                        # Parse the task to determine what Rube should do
                        # For now, we'll use SEARCH_TOOLS to find the right tool, then execute it
                        result = _execute_rube_task(task, context, process_logger)
                        
                        result_queue.put({
                            "success": True,
                            "output": result.get("output", ""),
                            "error": result.get("error", ""),
                            "data": result.get("data", {})
                        })
                        status_queue.put({"status": "completed", "message": "Task completed"})
                        
                    except Exception as e:
                        error_msg = str(e)
                        process_logger.error(f"Error executing Rube task: {error_msg}", exc_info=True)
                        result_queue.put({
                            "success": False,
                            "error": error_msg,
                            "output": "",
                            "data": {}
                        })
                        status_queue.put({"status": "error", "message": error_msg})
                
            except Exception as e:
                process_logger.error(f"Error in Rube service loop: {e}", exc_info=True)
                result_queue.put({
                    "success": False,
                    "error": str(e),
                    "output": "",
                    "data": {}
                })
        
        process_logger.info("Rube service thread stopping...")
        status_queue.put({"status": "stopped", "message": "Rube service stopped"})
        
    except Exception as e:
        process_logger.error(f"Fatal error in Rube service thread: {e}", exc_info=True)
        status_queue.put({"status": "error", "message": f"Fatal error: {str(e)}"})


def _execute_rube_task(task: str, context: Dict[str, Any], logger) -> Dict[str, Any]:
    """
    Execute a Rube task by searching for appropriate tools and executing them.
    
    Args:
        task: The task description
        context: Additional context (e.g., clipboard content, dropped files)
        
    Returns:
        Dict with output, error, and data
    """
    try:
        # Try to import MCP Rube tools
        # Note: These should be available if Rube MCP server is configured
        try:
            # For now, we'll use a simple approach: search for tools and execute
            # In a real implementation, you'd use the MCP client to call the tools
            
            # This is a placeholder - the actual implementation would:
            # 1. Use RUBE_SEARCH_TOOLS to find appropriate tools for the task
            # 2. Use RUBE_MULTI_EXECUTE_TOOL to execute the tools
            # 3. Return the results
            
            # For now, return a message indicating the task was received
            # The actual MCP integration will be done in the tool layer
            return {
                "output": f"Rube task received: {task}. This will be executed via MCP tools.",
                "error": "",
                "data": {}
            }
        except ImportError as e:
            logger.error(f"Rube MCP tools not available: {e}")
            return {
                "output": "",
                "error": "Rube MCP tools are not available. Please ensure Rube MCP server is properly configured.",
                "data": {}
            }
            
    except Exception as e:
        logger.error(f"Error executing Rube task: {e}", exc_info=True)
        return {
            "output": "",
            "error": str(e),
            "data": {}
        }


class RubeService:
    """
    Service for managing Rube MCP tool execution in a separate thread.
    """
    
    def __init__(self, rube_token: str):
        self.rube_token = rube_token
        self.thread: Optional[threading.Thread] = None
        self.command_queue: Optional[queue.Queue] = None
        self.result_queue: Optional[queue.Queue] = None
        self.status_queue: Optional[queue.Queue] = None
        self._status_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self._running = False
        
    def start(self) -> bool:
        """Start the Rube service thread."""
        if self.thread and self.thread.is_alive():
            logger.warning("Rube service thread already running")
            return True
        
        try:
            self.command_queue = queue.Queue()
            self.result_queue = queue.Queue()
            self.status_queue = queue.Queue()
            
            self.thread = threading.Thread(
                target=_run_rube_command,
                args=(
                    self.command_queue,
                    self.result_queue,
                    self.status_queue,
                    self.rube_token
                ),
                daemon=True
            )
            self.thread.start()
            self._running = True
            
            # Wait for ready status
            timeout = 10
            start_time = time.time()
            
            logger.info(f"Waiting for Rube service to be ready (timeout: {timeout}s)...")
            while time.time() - start_time < timeout:
                try:
                    status = self.status_queue.get(timeout=0.5)
                    if status.get("status") == "ready":
                        logger.info("✓ Rube service ready")
                        # Start status monitoring thread
                        self._status_thread = threading.Thread(
                            target=self._monitor_status,
                            daemon=True
                        )
                        self._status_thread.start()
                        return True
                    elif status.get("status") == "error":
                        error_msg = status.get("message", "Unknown error during initialization")
                        logger.error(f"Rube service reported error during init: {error_msg}")
                        return False
                except queue.Empty:
                    if not self.thread.is_alive():
                        logger.error("Rube service thread died during initialization")
                        return False
                    continue
            
            logger.error("Timeout waiting for Rube service to be ready")
            return False
            
        except Exception as e:
            logger.error(f"Failed to start Rube service thread: {e}")
            return False
    
    def _monitor_status(self):
        """Monitor status queue and call callbacks."""
        while self._running:
            try:
                status = self.status_queue.get(timeout=1.0)
                for callback in self._status_callbacks:
                    try:
                        callback(status)
                    except Exception as e:
                        logger.error(f"Error in status callback: {e}")
            except queue.Empty:
                continue
    
    def add_status_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """Add a callback for status updates."""
        self._status_callbacks.append(callback)
    
    def execute(self, task: str, context: Optional[Dict[str, Any]] = None, timeout: int = 300) -> Dict[str, Any]:
        """
        Execute a task with Rube.
        
        Args:
            task: Task description/command
            context: Optional context (clipboard content, dropped files, etc.)
            timeout: Timeout in seconds
            
        Returns:
            Dict with success, output, errors, etc.
        """
        if not self.thread or not self.thread.is_alive():
            logger.info("Rube service thread not running, starting...")
            start_result = self.start()
            if not start_result:
                error_msg = "Failed to start Rube service thread."
                logger.error(error_msg)
                return {
                    "success": False,
                    "error": error_msg
                }
            logger.info("Rube service thread started successfully")
        
        # Send command
        command_data = {
            "action": "execute",
            "task": task,
            "context": context or {}
        }
        
        try:
            self.command_queue.put(command_data)
            
            # Wait for result
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    result = self.result_queue.get(timeout=1.0)
                    return result
                except queue.Empty:
                    if not self.thread.is_alive():
                        return {
                            "success": False,
                            "error": "Rube service thread died"
                        }
                    continue
            
            return {
                "success": False,
                "error": "Timeout waiting for result"
            }
            
        except Exception as e:
            logger.error(f"Error executing task: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def stop(self):
        """Stop the Rube service thread."""
        self._running = False
        
        if self.thread and self.thread.is_alive():
            try:
                self.command_queue.put({"action": "stop"})
                self.thread.join(timeout=5.0)
            except Exception as e:
                logger.error(f"Error stopping Rube service: {e}")










