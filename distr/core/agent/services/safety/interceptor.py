"""
File Safety Interceptor - Intercepts code execution to enforce file safety rules.

This module checks code before execution
and require confirmation for any write/destructive operations.
"""

import logging
import re
import time
from typing import List, Tuple, Optional, Dict
from distr.core.files.safety import get_file_safety, OperationType
from distr.core.files.user_library_guard import is_protected_library_root
from distr.gui.dialogs.file_operation import confirm_file_operations_with_plan
from distr.gui.dialogs.rename_preview import show_rename_preview, extract_renames_from_code

logger = logging.getLogger(__name__)

# Module-level shared response dict for inter-process communication
# This will be initialized when first needed
_response_dict = None
_response_lock = None

def _predict_outcome_with_llm(code: str, task: str, operations: List[Dict], llm_service=None) -> Optional[str]:
    """
    Use LLM to analyze code and predict the actual outcome/result.
    
    Returns:
        A clear description of what will actually happen as a result of executing this code
    """
    if not llm_service:
        return None
    
    try:
        # Build context about the operations
        operation_summary = []
        for op in operations:
            op_type = op.get('type', 'UNKNOWN')
            source = op.get('source', '')
            dest = op.get('destination', '')
            if source and source not in ['(dynamic)', '(dynamic path)', '$target']:
                operation_summary.append(f"{op_type}: {source}")
            if dest and dest not in ['(dynamic)', '(dynamic path)', '$target']:
                operation_summary.append(f"  -> {dest}")
        
        prompt = f"""Analyze this Python code and predict what will actually happen as a result of executing it.

User's request: {task}

Code to execute:
```python
{code[:2000]}
```

File operations detected:
{chr(10).join(operation_summary) if operation_summary else 'None detected'}

Based on the code analysis, provide a clear, concise description of what will actually happen:
- What files/folders will be created, modified, deleted, moved, or renamed?
- What will be the final state after execution?
- What is the actual outcome/result?

Respond with ONLY a clear outcome description (2-3 sentences max). Be specific about file paths and what will happen to them."""
        
        # Try to call LLM
        if hasattr(llm_service, 'generate_text'):
            response = llm_service.generate_text(prompt, max_tokens=300)
            return response.strip() if response else None
        elif hasattr(llm_service, 'chat') or hasattr(llm_service, '_chat'):
            if hasattr(llm_service, 'chat'):
                response = llm_service.chat(prompt)
            else:
                response = llm_service._chat(prompt)
            
            if isinstance(response, str):
                return response.strip()
            elif hasattr(response, 'content'):
                return response.content.strip()
            elif isinstance(response, dict) and 'content' in response:
                return response['content'].strip()
        
        return None
    except Exception as e:
        logger.warning(f"Failed to predict outcome with LLM: {e}")
        return None


def _extract_intent_from_code(code: str, task: str, op_type: OperationType) -> str:
    """
    Extract a clear, descriptive intent from the code by analyzing what it does.
    
    Returns:
        A clear description of what the code will do
    """
    code_lower = code.lower()
    
    # If task is provided and seems meaningful, use it
    if task and len(task) > 10 and task != f"{op_type.value} operation detected":
        return task
    
    # Analyze code patterns to determine intent
    intent_parts = []
    
    # File operations
    if 'rename' in code_lower or 'os.rename' in code_lower or 'shutil.move' in code_lower:
        intent_parts.append("Rename/move files")
    if 'delete' in code_lower or 'remove' in code_lower or 'rm' in code_lower or 'unlink' in code_lower:
        intent_parts.append("Delete files")
    if 'copy' in code_lower or 'shutil.copy' in code_lower:
        intent_parts.append("Copy files")
    if 'write' in code_lower or ('open(' in code_lower and ('w' in code_lower or 'a' in code_lower)):
        intent_parts.append("Write/modify files")
    
    # Specific operations
    if 'clean' in code_lower or 'cleanup' in code_lower:
        intent_parts.append("Clean up files/folders")
    if 'organize' in code_lower:
        intent_parts.append("Organize files")
    if 'convert' in code_lower or 'ffmpeg' in code_lower:
        intent_parts.append("Convert files")
    if 'extract' in code_lower:
        intent_parts.append("Extract files")
    if 'compress' in code_lower or 'zip' in code_lower:
        intent_parts.append("Compress files")
    
    # Path patterns
    if '/music' in code_lower or 'music/' in code_lower or 'music' in code_lower:
        intent_parts.append("in music folder")
    if 'mp3' in code_lower:
        intent_parts.append("MP3 files")
    if 'flac' in code_lower:
        intent_parts.append("FLAC files")
    if 'junk' in code_lower:
        intent_parts.append("move junk files")
    if 'covers' in code_lower or 'cover' in code_lower:
        intent_parts.append("cover art")
    if 'plixid' in code_lower or 'bracket' in code_lower or 'remove' in code_lower:
        intent_parts.append("remove unwanted text/tags")
    
    # Build intent string
    if intent_parts:
        intent = " ".join(intent_parts)
        # Clean up common patterns
        intent = re.sub(r'\s+', ' ', intent).strip()
        return intent.capitalize()
    
    # Fallback to operation type
    return f"{op_type.value.replace('_', ' ').title()} operation"


def _ensure_response_dict_initialized():
    """Initialize _response_dict and _response_lock if not already initialized."""
    global _response_dict, _response_lock
    if _response_dict is None or _response_lock is None:
        import threading
        _response_dict = {}
        _response_lock = threading.Lock()
        logger.debug("[GUARDRAIL] Initialized _response_dict and _response_lock for fallback confirmation mechanism")


def extract_code_blocks(text: str) -> List[Tuple[str, str]]:
    """
    Extract code blocks from text (Python, bash, etc.).
    
    Returns:
        List of (language, code) tuples
    """
    code_blocks = []
    
    # Find Python code blocks
    python_blocks = re.findall(r'```python\n?(.*?)```', text, re.DOTALL | re.IGNORECASE)
    for code in python_blocks:
        code_blocks.append(('python', code.strip()))
    
    # Find bash code blocks
    bash_blocks = re.findall(r'```bash\n?(.*?)```', text, re.DOTALL | re.IGNORECASE)
    for code in bash_blocks:
        code_blocks.append(('bash', code.strip()))
    
    # Find shell code blocks
    shell_blocks = re.findall(r'```sh\n?(.*?)```', text, re.DOTALL | re.IGNORECASE)
    for code in shell_blocks:
        code_blocks.append(('bash', code.strip()))
    
    return code_blocks


def extract_rename_operations(code: str) -> List[Dict[str, str]]:
    """
    Extract rename/move operations from code, including loop-based renames.
    
    This function:
    1. Extracts hardcoded renames (os.rename with string literals)
    2. Detects loop-based renames and tries to predict all renames by analyzing the code
    3. Scans folders to find actual files that will be renamed
    
    Returns:
        List of dicts with 'source', 'destination', 'type' keys
    """
    renames = []
    
    # Pattern for os.rename(source, dest) with string literals
    os_rename_pattern = r'os\.rename\s*\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']\s*\)'
    for match in re.finditer(os_rename_pattern, code):
        import os as os_module
        renames.append({
            'source': match.group(1),
            'destination': match.group(2),
            'type': 'folder' if os_module.path.isdir(match.group(1)) else 'file'
        })
    
    # Pattern for shutil.move(source, dest) with string literals
    shutil_move_pattern = r'shutil\.move\s*\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']\s*\)'
    for match in re.finditer(shutil_move_pattern, code):
        import os as os_module
        renames.append({
            'source': match.group(1),
            'destination': match.group(2),
            'type': 'folder' if os_module.path.isdir(match.group(1)) else 'file'
        })
    
    # Pattern for Path.rename() with string literals
    path_rename_pattern = r'Path\s*\(\s*["\']([^"\']+)["\']\s*\)\.rename\s*\(\s*["\']([^"\']+)["\']\s*\)'
    for match in re.finditer(path_rename_pattern, code):
        import os as os_module
        renames.append({
            'source': match.group(1),
            'destination': match.group(2),
            'type': 'folder' if os_module.path.isdir(match.group(1)) else 'file'
        })
    
    # Now detect loop-based renames (variable-based renames in loops)
    # Look for patterns like: for f in files: os.rename(old_path, new_path)
    loop_rename_patterns = [
        r'for\s+(\w+)\s+in\s+([^:]+):.*?os\.rename\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)',
        r'for\s+(\w+)\s+in\s+([^:]+):.*?shutil\.move\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)',
    ]
    
    # Try to extract folder path and predict renames
    folder_match = re.search(r'folder\s*=\s*["\']([^"\']+)["\']', code, re.IGNORECASE)
    if folder_match:
        folder_path = folder_match.group(1)
        try:
            from pathlib import Path
            folder = Path(folder_path)
            if folder.exists() and folder.is_dir():
                # Look for rename operations in loops
                # Pattern: for f in os.listdir(folder): ... os.rename(...)
                if re.search(r'for\s+\w+\s+in\s+.*?os\.rename|for\s+\w+\s+in\s+.*?shutil\.move', code, re.IGNORECASE | re.DOTALL):
                    # Try to extract the rename logic by analyzing the code
                    # Look for patterns that generate new names
                    files_in_folder = []
                    try:
                        for item in os.listdir(folder):
                            item_path = folder / item
                            if item_path.is_file() or item_path.is_dir():
                                files_in_folder.append(item)
                    except Exception as e:
                        logger.debug(f"Could not list files in {folder_path}: {e}")
                        files_in_folder = []
                    
                    # Try to predict new names by analyzing the rename logic in the code
                    # Look for patterns like: new_name = old_name.replace(...) or re.sub(...)
                    for filename in files_in_folder[:100]:  # Limit to 100 to avoid too many
                        old_path = str(folder / filename)
                        new_name = filename
                        name_changed = False
                        
                        # Pattern 1: .replace() calls
                        replace_matches = re.finditer(r'\.replace\s*\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']*)["\']', code, re.IGNORECASE)
                        for match in replace_matches:
                            old_text = match.group(1)
                            new_text = match.group(2)
                            if old_text in new_name:
                                new_name = new_name.replace(old_text, new_text)
                                name_changed = True
                        
                        # Pattern 2: re.sub() calls
                        resub_matches = re.finditer(r're\.sub\s*\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']*)["\']\s*,\s*(\w+)', code, re.IGNORECASE)
                        for match in resub_matches:
                            pattern = match.group(1)
                            replacement = match.group(2)
                            try:
                                new_name = re.sub(pattern, replacement, new_name, flags=re.IGNORECASE)
                                name_changed = True
                            except (re.error, ValueError):
                                pass
                        
                        # Pattern 3: Remove brackets [text]
                        if re.search(r'\[.*?\]', filename):
                            # Check if code removes brackets
                            if re.search(r'\.replace.*?\[|re\.sub.*?\[|\[.*?\].*?remove', code, re.IGNORECASE):
                                new_name = re.sub(r'\[.*?\]', '', new_name)
                                name_changed = True
                        
                        # Pattern 4: Remove specific substrings mentioned in code
                        # Look for patterns like "remove plixid" or "clean up plixid"
                        if 'plixid' in code.lower():
                            new_name = re.sub(r'plixid[^.]*', '', new_name, flags=re.IGNORECASE)
                            name_changed = True
                        
                        # Pattern 5: Strip whitespace
                        if '.strip()' in code or 'strip(' in code:
                            new_name = new_name.strip()
                            name_changed = True
                        
                        # Pattern 6: Remove leading/trailing characters
                        if '.lstrip()' in code or '.rstrip()' in code:
                            if '.lstrip()' in code:
                                new_name = new_name.lstrip()
                            if '.rstrip()' in code:
                                new_name = new_name.rstrip()
                            name_changed = True
                        
                        # Pattern 7: Normalize spaces (multiple spaces to single)
                        if 'normalize' in code.lower() or '  ' in code and 'replace' in code.lower():
                            new_name = re.sub(r'\s+', ' ', new_name)
                            name_changed = True
                        
                        # Only add if name actually changed
                        if name_changed and new_name != filename and new_name.strip():
                            new_path = str(folder / new_name)
                            renames.append({
                                'source': old_path,
                                'destination': new_path,
                                'type': 'folder' if (folder / filename).is_dir() else 'file'
                            })
        except Exception as e:
            logger.debug(f"Could not analyze folder for renames: {e}")
    
    return renames


def show_rename_preview_via_queue(
    renames: List[Dict[str, str]],
    event_queue,
    command_queue,
    confirmation_results_dict,
    timeout: int = 120
) -> bool:
    """
    Show rename preview dialog via inter-process communication.
    
    Args:
        renames: List of rename operations
        event_queue: Queue for sending to main process
        command_queue: Queue for receiving from main process
        confirmation_results_dict: Shared dict for results
        timeout: Timeout in seconds
    
    Returns:
        True if user confirmed, False otherwise
    """
    import time
    
    confirmation_id = f"rename_preview_{int(time.time() * 1000)}"
    
    # Send request to main process
    try:
        event_queue.put(('rename_preview_request', {
            'renames': renames,
            'confirmation_id': confirmation_id
        }), block=False)
        logger.info(f"[RENAME PREVIEW] Sent request to main process (ID: {confirmation_id}, {len(renames)} renames)")
    except Exception as e:
        logger.error(f"[RENAME PREVIEW] Failed to send request: {e}")
        return False
    
    # Wait for result
    start_time = time.time()
    while time.time() - start_time < timeout:
        # Check _response_dict
        _ensure_response_dict_initialized()
        if _response_dict is not None and _response_lock is not None:
            with _response_lock:
                if confirmation_id in _response_dict:
                    result = _response_dict.pop(confirmation_id)
                    confirmed = result if isinstance(result, bool) else result.get('confirmed', False)
                    logger.info(f"[RENAME PREVIEW] Got result: confirmed={confirmed}")
                    return confirmed
        
        # Check confirmation_results_dict
        if confirmation_results_dict is not None:
            try:
                if confirmation_id in confirmation_results_dict:
                    result = confirmation_results_dict.pop(confirmation_id)
                    confirmed = result.get('confirmed', False)
                    logger.info(f"[RENAME PREVIEW] Got result from Manager dict: confirmed={confirmed}")
                    return confirmed
            except Exception:
                pass
        
        time.sleep(0.1)
    
    logger.warning(f"[RENAME PREVIEW] Timeout waiting for user response")
    return False


def check_and_confirm_code_execution(
    code: str, 
    language: str, 
    task: str = "",
    event_queue = None,
    command_queue = None,
    confirmation_results_dict = None
) -> Tuple[bool, Optional[Dict]]:
    """
    Check code for file operations and require confirmation if needed.
    
    Args:
        code: The code to check
        language: Language of the code ('python' or 'bash')
        task: Optional task description for context
        event_queue: Queue for sending events to main GUI process (for inter-process communication)
        command_queue: Queue for receiving commands from main process
        confirmation_results_dict: Shared dict for confirmation results
        
    Returns:
        (allowed, plan) - allowed=True if execution can proceed, plan dict if confirmation needed
    """
    file_safety = get_file_safety()
    
    # Classify the operation
    op_type = file_safety.classify_operation(code, task)
    
    logger.info(f"[FILE SAFETY] Code classification: {op_type.value}")
    logger.debug(f"[FILE SAFETY] Code snippet: {code[:100]}...")
    
    # READ_ONLY operations can proceed without confirmation
    if op_type == OperationType.READ_ONLY:
        file_safety.log_operation('read_only_allowed', {
            'code': code[:500],  # Log first 500 chars
            'language': language,
            'task': task
        })
        return (True, None)
    
    # WRITE or DESTRUCTIVE operations require confirmation
    operations = file_safety.extract_file_operations(code)
    
    # Generate a quick plan to check for overwrite and other destructive indicators
    # This is needed to properly determine if we can auto-approve
    intent = _extract_intent_from_code(code, task, op_type) if operations else (task or 'File operation')
    quick_plan = file_safety.generate_plan(operations, intent) if operations else None
    
    # Check if file change confirmations are enabled (Initiative settings)
    try:
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        ask_file_changes = settings.get('initiative_ask_file_changes', True)
        logger.debug(f"[FILE SAFETY] initiative_ask_file_changes setting: {ask_file_changes}")
        
        # If user has disabled confirmations, auto-approve low-impact operations only.
        # Never auto-approve destructive code — that bypass wiped user folders when the model mis-routed.
        if not ask_file_changes:
            if file_safety.cannot_bypass_file_confirmation(
                plan=quick_plan,
                classified_operation_type=op_type,
            ):
                logger.warning(
                    "[FILE SAFETY] initiative_ask_file_changes is False but operation is destructive/high-impact — "
                    "confirmation is still required"
                )
                file_safety.log_operation('mandatory_confirmation_enforced', {
                    'code': code[:500],
                    'language': language,
                    'task': task,
                    'classification': op_type.value,
                    'reason': 'destructive_or_bulk_requires_dialog',
                })
                # Fall through to confirmation UI below
            else:
                logger.debug("[FILE SAFETY] File operation confirmations disabled — auto-approving low-impact operation")
                file_safety.log_operation('bypassed_confirmation_disabled', {
                    'code': code[:500],
                    'language': language,
                    'task': task,
                    'operation_type': op_type.value
                })
                return (True, quick_plan or {
                    'intent': intent,
                    'operations': operations,
                    'auto_approved': True
                })
    except Exception as e:
        logger.warning(f"[FILE SAFETY] Could not check initiative_ask_file_changes setting: {e}")
        logger.debug("[FILE SAFETY] Defaulting to require confirmation (setting check failed)")
    
    # Check for rename operations - these get a special preview dialog
    rename_operations = extract_rename_operations(code)
    if rename_operations:
        logger.debug(f"[FILE SAFETY] Detected {len(rename_operations)} rename operation(s) - showing rename preview dialog")
        
        # Try to show rename preview dialog via inter-process communication
        if event_queue is not None and confirmation_results_dict is not None:
            confirmed = show_rename_preview_via_queue(
                rename_operations, event_queue, command_queue, confirmation_results_dict
            )
            plan = {
                'intent': 'Rename/move files',
                'renames': rename_operations,
                'rename_count': len(rename_operations)
            }
            if confirmed:
                file_safety.log_operation('rename_confirmed', {'renames': rename_operations})
                return (True, plan)
            else:
                file_safety.log_operation('rename_cancelled', {'renames': rename_operations})
                return (False, plan)
        else:
            # No inter-process communication - try to show dialog directly
            try:
                from PyQt6.QtWidgets import QApplication
                if QApplication.instance():
                    confirmed = show_rename_preview(rename_operations, title=f"Rename {len(rename_operations)} File(s)")
                    plan = {
                        'intent': 'Rename/move files',
                        'renames': rename_operations,
                        'rename_count': len(rename_operations)
                    }
                    if confirmed:
                        file_safety.log_operation('rename_confirmed', {'renames': rename_operations})
                        return (True, plan)
                    else:
                        file_safety.log_operation('rename_cancelled', {'renames': rename_operations})
                        return (False, plan)
            except Exception as e:
                logger.warning(f"[FILE SAFETY] Could not show rename preview dialog: {e}")
                # Fall through to generic confirmation
    
    if not operations:
        # No specific file paths extracted, but code is WRITE/DESTRUCTIVE
        # Still show confirmation dialog with the code snippet so user can review
        logger.warning(f"Code classified as {op_type.value} but no specific paths extracted - showing generic confirmation")
        
        # Extract better intent from code analysis
        intent = _extract_intent_from_code(code, task, op_type)
        
        # Generate a generic plan for the dialog
        generic_plan = {
            'intent': intent,
            'file_count': 0,
            'directory_count': 0,
            'files': [],
            'directories': [],
            'operations': [{
                'type': op_type.value,
                'name': 'dynamic_operation',
                'source': '(dynamic paths - see code below)',
                'destination': None,
                'will_overwrite': op_type == OperationType.WRITE,
                'will_delete': op_type == OperationType.DESTRUCTIVE,
                'code_snippet': code[:1000] if len(code) > 1000 else code,
            }],
            'will_overwrite': op_type == OperationType.WRITE,
            'overwrite_files': [],
            'will_delete': op_type == OperationType.DESTRUCTIVE,
            'delete_files': [],
            'code_preview': code[:3000] if len(code) > 3000 else code,  # Show more code
            'rollback_strategy': 'Review the code carefully before confirming',
            'warning': 'Specific file paths could not be extracted - please review the code below carefully',
        }
        
        file_safety.log_operation('generic_plan_generated', {
            'code': code[:500],
            'language': language,
            'task': task,
            'classification': op_type.value
        })
        
        # Show confirmation dialog with generic plan
        try:
            from PyQt6.QtWidgets import QApplication
            from PyQt6.QtCore import QCoreApplication
            
            app_instance = QCoreApplication.instance()
            qapp_instance = None
            
            if app_instance and isinstance(app_instance, QApplication):
                qapp_instance = app_instance
            else:
                # Try to get QApplication instance directly
                try:
                    qapp_instance = QApplication.instance()
                    if qapp_instance and not isinstance(qapp_instance, QApplication):
                        qapp_instance = None
                except Exception:
                    pass
            
            if qapp_instance is None:
                # No QApplication - try inter-process communication
                logger.debug(f"[FILE SAFETY] No QApplication - checking queues: event_queue={event_queue is not None}, confirmation_results_dict={confirmation_results_dict is not None}")
                if event_queue is not None and confirmation_results_dict is not None:
                    logger.debug("[FILE SAFETY] ✅ Using inter-process communication for confirmation dialog")
                    return _request_confirmation_via_queue(
                        op_type.value, '(dynamic paths)', None, task,
                        'code_execution', generic_plan, event_queue, command_queue,
                        confirmation_results_dict, file_safety
                    )
                else:
                    logger.warning(f"[FILE SAFETY] ❌ No event_queue or confirmation_results_dict - cannot show dialog. event_queue={event_queue is not None}, confirmation_results_dict={confirmation_results_dict is not None}")
                    file_safety.log_operation('blocked_no_gui', {'plan': generic_plan})
                    return (False, generic_plan)
            
            # Show confirmation dialog with generic plan
            confirmed = confirm_file_operations_with_plan(
                generic_plan,
                require_confirmation_phrase=True,
                confirmation_phrase=file_safety.CONFIRM_FILE_CHANGES
            )
            
            if confirmed:
                file_safety.log_operation('confirmed_generic', {'plan': generic_plan})
                return (True, generic_plan)
            else:
                file_safety.log_operation('cancelled_generic', {'plan': generic_plan})
                return (False, generic_plan)
                
        except Exception as e:
            logger.error(f"Error showing confirmation dialog: {e}", exc_info=True)
            file_safety.log_operation('error', {'plan': generic_plan, 'error': str(e)})
            return (False, generic_plan)
    
    # Extract better intent from code
    intent = _extract_intent_from_code(code, task, op_type)
    
    # Generate plan with improved intent
    plan = file_safety.generate_plan(operations, intent)  # Use extracted intent instead of task
    
    # Include rename operations in plan if we detected any (for display in confirmation dialog)
    if rename_operations and len(rename_operations) > 0:
        plan['renames'] = rename_operations
        plan['rename_count'] = len(rename_operations)
    
    # Try to predict actual outcome using LLM (if available)
    # This helps the user understand what will actually happen
    try:
        # Try to get LLM service from context if available
        llm_service = None
        # Check if we can access LLM service (this might not always be available)
        # For now, we'll skip LLM prediction if not available - it's optional enhancement
        predicted_outcome = _predict_outcome_with_llm(code, task, operations, llm_service)
        if predicted_outcome:
            plan['predicted_outcome'] = predicted_outcome
            logger.debug(f"[FILE SAFETY] Predicted outcome: {predicted_outcome[:100]}...")
    except Exception as e:
        logger.debug(f"Could not predict outcome with LLM: {e}")
        # Not critical - continue without prediction
    
    # Add code preview if operations are dynamic (so user can see what will execute)
    has_dynamic = any(op.get('is_dynamic') for op in operations)
    if has_dynamic and code:
        plan['code_preview'] = code[:3000] if len(code) > 3000 else code
        plan['warning'] = 'Some file paths are determined dynamically at runtime - review the code below carefully'
    
    # Check for high-risk conditions
    is_high_risk, risk_reasons = file_safety.check_high_risk(operations)
    if is_high_risk:
        logger.warning(f"High-risk operation detected: {risk_reasons}")
        plan['high_risk'] = True
        plan['risk_reasons'] = risk_reasons
    
    # Log the plan
    file_safety.log_operation('plan_generated', {
        'plan': plan,
        'code': code[:500],
        'language': language,
        'task': task
    })
    
    # Show confirmation dialog
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QCoreApplication
        
        app_instance = QCoreApplication.instance()
        qapp_instance = None
        
        if app_instance and isinstance(app_instance, QApplication):
            qapp_instance = app_instance
        else:
            # Try to get QApplication instance directly
            try:
                qapp_instance = QApplication.instance()
                if qapp_instance and not isinstance(qapp_instance, QApplication):
                    qapp_instance = None
            except Exception:
                pass
        
        if qapp_instance is None:
            # No QApplication - try inter-process communication
            logger.debug(f"[FILE SAFETY] No QApplication - checking queues: event_queue={event_queue is not None}, confirmation_results_dict={confirmation_results_dict is not None}")
            if event_queue is not None and confirmation_results_dict is not None:
                logger.debug("[FILE SAFETY] ✅ Using inter-process communication for confirmation dialog (extracted paths)")
                # Get operation details for the confirmation request
                first_op = operations[0] if operations else {}
                source_path = first_op.get('path', '(multiple files)')
                return _request_confirmation_via_queue(
                    op_type.value, source_path, None, task,
                    'code_execution', plan, event_queue, command_queue,
                    confirmation_results_dict, file_safety
                )
            else:
                logger.warning(f"[FILE SAFETY] ❌ No event_queue or confirmation_results_dict - cannot show dialog. event_queue={event_queue is not None}, confirmation_results_dict={confirmation_results_dict is not None}")
                file_safety.log_operation('blocked_no_gui', {'plan': plan})
                return (False, plan)
        
        # Show confirmation dialog
        confirmed = confirm_file_operations_with_plan(
            plan,
            require_confirmation_phrase=True,
            confirmation_phrase=file_safety.CONFIRM_FILE_CHANGES
        )
        
        if confirmed:
            file_safety.log_operation('confirmed', {'plan': plan})
            return (True, plan)
        else:
            file_safety.log_operation('cancelled', {'plan': plan})
            return (False, plan)
            
    except Exception as e:
        logger.error(f"Error showing confirmation dialog: {e}", exc_info=True)
        file_safety.log_operation('error', {'plan': plan, 'error': str(e)})
        # On error, default to deny for safety
        return (False, plan)


def check_and_confirm_direct_file_operation(
    operation_type: str,
    source_path: str,
    destination_path: Optional[str] = None,
    task: str = "",
    originating_pathway: str = "file_operations_tool",
    event_queue=None,
    command_queue=None,
    confirmation_results_dict=None
) -> Tuple[bool, Optional[Dict]]:
    """
    Check and confirm a direct file operation (not from code).
    
    This is the centralized guardrail enforcement point for all file operations
    that bypass code execution (e.g., file_operations tool).
    
    Args:
        operation_type: Type of operation ('DELETE', 'MOVE', 'COPY', 'WRITE')
        source_path: Source file/directory path
        destination_path: Destination path (for MOVE/COPY operations)
        task: Optional task description for context
        originating_pathway: Where the operation originated from (for logging)
        
    Returns:
        (allowed, plan) - allowed=True if operation can proceed, plan dict if confirmation needed
    """
    import os
    from pathlib import Path
    
    logger.info(f"[GUARDRAIL] Operation requested: {operation_type} on {source_path} (pathway: {originating_pathway})")
    
    file_safety = get_file_safety()
    
    # Build operation dict
    operation = {
        'type': operation_type,
        'name': f"{operation_type.lower()}_operation",
        'source': source_path,
        'destination': destination_path,
        'will_overwrite': False,
        'will_delete': operation_type == 'DELETE',
    }
    
    # Check for overwrite risk
    if destination_path and os.path.exists(destination_path):
        operation['will_overwrite'] = True
        logger.debug(f"[GUARDRAIL] Destination exists - will overwrite: {destination_path}")
    
    # Check if file exists (for delete operations)
    if operation_type == 'DELETE' and not os.path.exists(source_path):
        logger.warning(f"[GUARDRAIL] Delete operation requested on non-existent path: {source_path}")
        return (False, None)
    
    # Check if source exists (for move/copy operations)
    if operation_type in ('MOVE', 'COPY') and not os.path.exists(source_path):
        logger.warning(f"[GUARDRAIL] {operation_type} operation requested on non-existent path: {source_path}")
        return (False, None)
    
    # Check if write operation would overwrite
    if operation_type == 'WRITE' and os.path.exists(source_path):
        operation['will_overwrite'] = True
        logger.debug(f"[GUARDRAIL] Write operation will overwrite existing file: {source_path}")
    
    # Generate plan
    plan = file_safety.generate_plan([operation], task)
    plan['originating_pathway'] = originating_pathway
    
    # Check for high-risk conditions
    is_high_risk, risk_reasons = file_safety.check_high_risk([operation])
    if is_high_risk:
        logger.warning(f"[GUARDRAIL] High-risk operation detected: {risk_reasons}")
        plan['high_risk'] = True
        plan['risk_reasons'] = risk_reasons
    
    # Log the operation request
    file_safety.log_operation('operation_requested', {
        'operation_type': operation_type,
        'source_path': source_path,
        'destination_path': destination_path,
        'originating_pathway': originating_pathway,
        'plan': plan,
        'task': task
    })

    # Hard deny: never delete standard library folder roots (Downloads, Documents, …) via tooling.
    if operation_type == 'DELETE':
        try:
            resolved_root = os.path.realpath(source_path)
        except OSError:
            resolved_root = source_path
        if is_protected_library_root(resolved_root):
            logger.error(
                "[GUARDRAIL] Blocked DELETE on protected library/home root: %s",
                resolved_root,
            )
            file_safety.log_operation('blocked_protected_library_root', {
                'operation_type': operation_type,
                'source_path': source_path,
                'resolved_path': resolved_root,
                'originating_pathway': originating_pathway,
            })
            return (False, plan)

        try:
            if os.path.isdir(source_path):
                logger.error(
                    "[GUARDRAIL] Blocked DELETE on directory (bulk delete policy): %s",
                    source_path,
                )
                file_safety.log_operation('blocked_directory_bulk_delete', {
                    'operation_type': operation_type,
                    'source_path': source_path,
                    'originating_pathway': originating_pathway,
                })
                return (False, plan)
        except OSError:
            pass
    
    # Respect user's preference to skip *routine* prompts — never for DELETE or bulk/high-risk plans.
    try:
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        ask_file_changes = settings.get('initiative_ask_file_changes', True)
        logger.debug(f"[GUARDRAIL] initiative_ask_file_changes setting: {ask_file_changes}")
        
        if not ask_file_changes:
            if file_safety.cannot_bypass_file_confirmation(
                operation_type=operation_type,
                plan=plan,
            ):
                logger.warning(
                    "[GUARDRAIL] initiative_ask_file_changes is False but %s on %s requires explicit confirmation "
                    "(destructive or bulk) — showing dialog or denying if no GUI",
                    operation_type,
                    source_path,
                )
                file_safety.log_operation('mandatory_confirmation_enforced', {
                    'operation_type': operation_type,
                    'source_path': source_path,
                    'plan_summary': {
                        'intent': plan.get('intent'),
                        'file_count': plan.get('file_count'),
                        'will_delete': plan.get('will_delete'),
                        'high_risk': plan.get('high_risk'),
                    },
                    'reason': 'destructive_or_bulk_requires_dialog',
                })
                # Fall through — must confirm or deny safely below
            else:
                logger.debug(
                    f"[GUARDRAIL] File confirmations disabled — auto-approving low-impact {operation_type}"
                )
                file_safety.log_operation('bypassed_confirmation_disabled', {
                    'operation_type': operation_type,
                    'source_path': source_path,
                    'plan': plan
                })
                return (True, plan)
    except Exception as e:
        logger.warning(f"[GUARDRAIL] Could not check initiative_ask_file_changes setting: {e}")
        logger.debug(f"[GUARDRAIL] Defaulting to require confirmation (setting check failed)")
    
    # Show confirmation dialog
    logger.debug(f"[GUARDRAIL] Showing confirmation dialog for {operation_type} operation on {source_path} (pathway: {originating_pathway})")
    file_safety.log_operation('guardrail_evaluation_started', {
        'operation_type': operation_type,
        'source_path': source_path,
        'destination_path': destination_path,
        'originating_pathway': originating_pathway,
        'plan': plan,
        'will_show_dialog': True
    })
    
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QCoreApplication, QMetaObject, Qt, QThread
        import threading
        
        # Try to get QApplication instance - may be in different thread
        app_instance = QCoreApplication.instance()
        qapp_instance = None
        
        # First try: check if current instance is QApplication
        if app_instance and isinstance(app_instance, QApplication):
            qapp_instance = app_instance
            logger.debug("[GUARDRAIL] Found QApplication instance in current context")
        else:
            # Second try: get QApplication instance directly (works across threads in same process)
            try:
                qapp_instance = QApplication.instance()
                # Double-check it's actually a QApplication, not QCoreApplication
                if qapp_instance and isinstance(qapp_instance, QApplication):
                    logger.debug("[GUARDRAIL] Found QApplication instance via QApplication.instance()")
                else:
                    qapp_instance = None  # It's a QCoreApplication, not QApplication
                    logger.debug("[GUARDRAIL] Found QCoreApplication instance (not QApplication) - will use inter-process communication")
            except Exception as e:
                logger.warning(f"[GUARDRAIL] Could not get QApplication instance: {e}")
        
        if qapp_instance is None:
            # No QApplication in this process - try inter-process communication
            if event_queue is not None and confirmation_results_dict is not None:
                logger.debug("[GUARDRAIL] No QApplication in subprocess - using inter-process communication")
                return _request_confirmation_via_queue(
                    operation_type, source_path, destination_path, task, 
                    originating_pathway, plan, event_queue, command_queue, 
                    confirmation_results_dict, file_safety
                )
            else:
                logger.warning(f"[GUARDRAIL] No QApplication found and missing required queues (event_queue={event_queue is not None}, confirmation_results_dict={confirmation_results_dict is not None}) - cannot show dialog, denying file operations for safety")
                file_safety.log_operation('blocked_no_gui', {
                    'operation_type': operation_type,
                    'source_path': source_path,
                    'destination_path': destination_path,
                    'originating_pathway': originating_pathway,
                    'plan': plan,
                    'dialog_shown': False
                })
                return (False, plan)
        
        # Use the QApplication instance we found
        app_instance = qapp_instance
        
        # Check if we're in the main thread
        main_thread = app_instance.thread()
        current_thread = QThread.currentThread()
        in_main_thread = (current_thread == main_thread)
        
        logger.debug(f"[GUARDRAIL] Current thread: {current_thread}, Main thread: {main_thread}, In main thread: {in_main_thread}")
        logger.debug(f"[GUARDRAIL] QApplication found: {qapp_instance is not None}, will show dialog")
        
        # Use a threading event and result container to wait for the dialog result
        result_event = threading.Event()
        confirmed_result = [False]  # Use list to allow modification in nested function
        
        def show_dialog_in_main_thread():
            """Show dialog in main thread and set result"""
            try:
                logger.debug(f"[GUARDRAIL] Showing dialog in main thread for {operation_type} operation")
                confirmed = confirm_file_operations_with_plan(
                    plan,
                    require_confirmation_phrase=True,
                    confirmation_phrase=file_safety.CONFIRM_FILE_CHANGES
                )
                confirmed_result[0] = confirmed
                logger.debug(f"[GUARDRAIL] Dialog result: {confirmed}")
            except Exception as e:
                logger.error(f"[GUARDRAIL] Error in dialog callback: {e}", exc_info=True)
                confirmed_result[0] = False
            finally:
                result_event.set()
        
        if in_main_thread:
            # Already in main thread - show dialog directly
            logger.debug(f"[GUARDRAIL] Already in main thread - showing dialog directly")
            confirmed = confirm_file_operations_with_plan(
                plan,
                require_confirmation_phrase=True,
                confirmation_phrase=file_safety.CONFIRM_FILE_CHANGES
            )
            confirmed_result[0] = confirmed
        else:
            # Not in main thread - use QTimer to schedule in main thread
            logger.debug(f"[GUARDRAIL] Not in main thread - scheduling dialog in main thread using QTimer")
            from PyQt6.QtCore import QTimer
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(show_dialog_in_main_thread)
            timer.start(0)
            
            # Process events to allow the timer to fire and dialog to show
            # Keep processing events until we get a result or timeout
            logger.debug(f"[GUARDRAIL] Processing events and waiting for dialog result...")
            timeout_seconds = 300  # 5 minutes
            start_time = time.time()
            
            while not result_event.is_set():
                if time.time() - start_time > timeout_seconds:
                    logger.error(f"[GUARDRAIL] Dialog timeout after {timeout_seconds} seconds")
                    confirmed_result[0] = False
                    break
                
                # Process events to allow dialog to show and process
                app_instance.processEvents()
                time.sleep(0.01)  # Small sleep to prevent busy-waiting
        
        confirmed = confirmed_result[0]
        logger.info(f"[GUARDRAIL] Final dialog result: {confirmed}")
        
        if confirmed:
            logger.info(f"[GUARDRAIL] Operation confirmed - proceeding with {operation_type} on {source_path}")
            file_safety.log_operation('guardrail_decision', {
                'operation_type': operation_type,
                'source_path': source_path,
                'destination_path': destination_path,
                'originating_pathway': originating_pathway,
                'decision': 'allowed',
                'dialog_shown': True,
                'user_confirmed': True,
                'plan': plan
            })
            return (True, plan)
        else:
            logger.info(f"[GUARDRAIL] Operation cancelled by user - {operation_type} on {source_path} blocked")
            file_safety.log_operation('guardrail_decision', {
                'operation_type': operation_type,
                'source_path': source_path,
                'destination_path': destination_path,
                'originating_pathway': originating_pathway,
                'decision': 'blocked',
                'dialog_shown': True,
                'user_confirmed': False,
                'plan': plan
            })
            return (False, plan)
            
    except Exception as e:
        logger.error(f"[GUARDRAIL] Error showing confirmation dialog: {e}", exc_info=True)
        file_safety.log_operation('error', {
            'operation_type': operation_type,
            'source_path': source_path,
            'destination_path': destination_path,
            'originating_pathway': originating_pathway,
            'plan': plan,
            'error': str(e)
        })
        # On error, default to deny for safety
        return (False, plan)


def _request_confirmation_via_queue(
    operation_type: str,
    source_path: str,
    destination_path: Optional[str],
    task: str,
    originating_pathway: str,
    plan: Dict,
    event_queue,
    command_queue,
    confirmation_results_dict,
    file_safety
) -> Tuple[bool, Optional[Dict]]:
    """
    Request file operation confirmation from main GUI process via event queue.
    
    This is used when running in a subprocess that doesn't have QApplication.
    """
    import time
    import queue as queue_module
    
    logger.debug(f"[GUARDRAIL] Sending confirmation request to main process via event queue")
    
    # Generate unique confirmation ID
    confirmation_id = f"file_confirm_{int(time.time() * 1000)}_{operation_type}"
    
    # Send confirmation request to main process
    try:
        event_queue.put(('file_operation_confirmation_request', {
            'operation_type': operation_type,
            'source_path': source_path,
            'destination_path': destination_path,
            'task': task,
            'originating_pathway': originating_pathway,
            'plan': plan,
            'confirmation_id': confirmation_id
        }), block=False)
        logger.debug(f"[GUARDRAIL] Sent confirmation request (ID: {confirmation_id}) to main process")
    except Exception as e:
        logger.error(f"[GUARDRAIL] Failed to send confirmation request: {e}", exc_info=True)
        file_safety.log_operation('error', {
            'operation_type': operation_type,
            'source_path': source_path,
            'error': f"Failed to send confirmation request: {e}"
        })
        return (False, plan)
    
    # Wait for confirmation result from main process via shared dict or command_queue fallback
    logger.debug(f"[GUARDRAIL] Waiting for confirmation result (ID: {confirmation_id})...")
    timeout = 60  # 60 seconds - reasonable timeout for user confirmation
    start_time = time.time()
    confirmed = False
    dont_show_again = False
    manager_connection_broken = False
    
    # Initialize fallback mechanism
    _ensure_response_dict_initialized()
    
    # Use the shared confirmation_results_dict passed from main process
    # Also check _response_dict as fallback (populated by command_queue responses)
    if confirmation_results_dict is None:
        logger.warning("[GUARDRAIL] confirmation_results_dict is None - will use _response_dict fallback")
        manager_connection_broken = True
    
    while time.time() - start_time < timeout:
        # Check both mechanisms - _response_dict FIRST (more reliable, populated by command_queue)
        # then confirmation_results_dict as fallback
        result_found = False
        
        # CRITICAL: Check _response_dict FIRST (populated by command_queue responses via agent session)
        # This is more reliable than the Manager dict which can have connection issues
        try:
            _ensure_response_dict_initialized()
            if _response_dict is not None and _response_lock is not None:
                with _response_lock:
                    all_keys = list(_response_dict.keys())
                    logger.debug(f"[GUARDRAIL] Polling _response_dict - looking for: {confirmation_id}, available keys: {all_keys}")
                    if confirmation_id in _response_dict:
                        confirmed = _response_dict.pop(confirmation_id)  # Get and remove
                        # Handle both boolean and dict formats
                        if isinstance(confirmed, dict):
                            confirmed = confirmed.get('confirmed', False)
                        logger.debug(f"[GUARDRAIL] ✅ FOUND confirmation result in _response_dict: confirmed={confirmed} (ID: {confirmation_id})")
                        result_found = True
                        break
                    else:
                        # Log if we see similar keys (helps debug ID mismatches)
                        matching_keys = [k for k in all_keys if confirmation_id.split('_')[-1] in k]
                        if matching_keys and int((time.time() - start_time)) % 5 == 0:
                            logger.debug(f"[GUARDRAIL] Confirmation ID {confirmation_id} not found, but similar keys exist: {matching_keys}")
        except Exception as e:
            logger.debug(f"[GUARDRAIL] Error checking _response_dict: {e}")
        
        # Second try: Check confirmation_results_dict (Manager dict) if _response_dict didn't have it
        if not result_found and not manager_connection_broken and confirmation_results_dict is not None:
            try:
                # Check shared confirmation_results_dict for our confirmation result
                if confirmation_id in confirmation_results_dict:
                    result = confirmation_results_dict.pop(confirmation_id)  # Get and remove
                    confirmed = result.get('confirmed', False)
                    dont_show_again = result.get('dont_show_again', False)
                    logger.debug(f"[GUARDRAIL] Received confirmation result from Manager dict: confirmed={confirmed}, dont_show_again={dont_show_again} (ID: {confirmation_id})")
                    result_found = True
                    break
            except BrokenPipeError as e:
                # Manager connection broken - switch to fallback
                logger.warning(f"[GUARDRAIL] Manager connection broken - will use _response_dict only: {e}")
                manager_connection_broken = True
                # Continue to next iteration
            except (OSError, ConnectionError) as e:
                # Connection errors - switch to fallback
                logger.warning(f"[GUARDRAIL] Manager connection error - will use _response_dict only: {e}")
                manager_connection_broken = True
                # Continue to next iteration
            except Exception as e:
                # Other errors - log but continue polling
                logger.debug(f"[GUARDRAIL] Error checking confirmation_results_dict (will retry): {e}")
                # Don't break on other errors - might be transient
        
        if result_found:
            break
        
        # Log progress every 5 seconds with more detail to help debug
        elapsed = time.time() - start_time
        if int(elapsed) % 5 == 0 and elapsed > 0:
            # Log available keys in _response_dict for debugging
            try:
                _ensure_response_dict_initialized()
                if _response_dict is not None and _response_lock is not None:
                    with _response_lock:
                        all_keys = list(_response_dict.keys())
                        logger.debug(f"[GUARDRAIL] Still waiting for confirmation (ID: {confirmation_id})... {elapsed:.1f}s elapsed. Available keys in _response_dict: {all_keys}")
                else:
                    logger.debug(f"[GUARDRAIL] Still waiting for confirmation (ID: {confirmation_id})... {elapsed:.1f}s elapsed. _response_dict not available")
            except Exception:
                logger.debug(f"[GUARDRAIL] Still waiting for confirmation (ID: {confirmation_id})... {elapsed:.1f}s elapsed")
        
        time.sleep(0.1)  # Small sleep to prevent busy-waiting
    
    # Cleanup: Remove any stale entries older than 5 minutes
    try:
        from distr.core.agent.services.safety.interceptor import _response_dict, _response_lock
        if _response_dict is not None and _response_lock is not None:
            with _response_lock:
                current_time = int(time.time() * 1000)
                stale_keys = []
                for key in list(_response_dict.keys()):
                    if key.startswith("file_confirm_"):
                        try:
                            # Extract timestamp from ID: file_confirm_{timestamp}_{operation_type}
                            parts = key.split("_")
                            if len(parts) >= 3:
                                key_timestamp = int(parts[2])
                                if current_time - key_timestamp > 300000:  # 5 minutes in milliseconds
                                    stale_keys.append(key)
                        except (ValueError, IndexError):
                            pass
                for key in stale_keys:
                    _response_dict.pop(key, None)
                    logger.debug(f"[GUARDRAIL] Cleaned up stale confirmation entry: {key}")
    except Exception as e:
        logger.debug(f"[GUARDRAIL] Error cleaning up stale entries: {e}")
    
    elapsed_time = time.time() - start_time
    
    # Final check: if we timed out, log what we found
    if elapsed_time >= timeout:
        logger.warning(f"[GUARDRAIL] ⚠️ Confirmation timeout after {elapsed_time:.2f}s (ID: {confirmation_id})")
        # Do a final check of both mechanisms before giving up
        if not confirmed:
            # Final check of _response_dict
            try:
                _ensure_response_dict_initialized()
                if _response_dict is not None and _response_lock is not None:
                    with _response_lock:
                        if confirmation_id in _response_dict:
                            confirmed = _response_dict.pop(confirmation_id)
                            if isinstance(confirmed, dict):
                                confirmed = confirmed.get('confirmed', False)
                            logger.debug(f"[GUARDRAIL] ✅ Found confirmation result in final check: {confirmed} (ID: {confirmation_id})")
                            result_found = True
                        else:
                            # Log all keys to help debug
                            all_keys = list(_response_dict.keys())
                            logger.warning(f"[GUARDRAIL] ⚠️ Final check: confirmation_id {confirmation_id} not in _response_dict")
                            logger.warning(f"[GUARDRAIL] ⚠️ Available keys in _response_dict: {all_keys}")
            except Exception as e:
                logger.error(f"[GUARDRAIL] Error in final check: {e}", exc_info=True)
    
    if confirmed:
        logger.debug(f"[GUARDRAIL] ✅ Operation confirmed via inter-process communication (took {elapsed_time:.2f}s, ID: {confirmation_id})")
        file_safety.log_operation('guardrail_decision', {
            'operation_type': operation_type,
            'source_path': source_path,
            'destination_path': destination_path,
            'originating_pathway': originating_pathway,
            'decision': 'allowed',
            'dialog_shown': True,
            'user_confirmed': True,
            'plan': plan,
            'via_interprocess': True,
            'confirmation_time_seconds': elapsed_time,
            'confirmation_id': confirmation_id
        })
        return (True, plan)
    else:
        if elapsed_time >= timeout:
            logger.warning(f"[GUARDRAIL] ⚠️ Operation timeout after {elapsed_time:.2f}s - no confirmation received")
            reason = "timeout"
        else:
            logger.debug(f"[GUARDRAIL] ❌ Operation cancelled by user (took {elapsed_time:.2f}s)")
            reason = "cancelled"
        
        file_safety.log_operation('guardrail_decision', {
            'operation_type': operation_type,
            'source_path': source_path,
            'destination_path': destination_path,
            'originating_pathway': originating_pathway,
            'decision': 'blocked',
            'dialog_shown': True,
            'user_confirmed': False,
            'plan': plan,
            'via_interprocess': True,
            'reason': reason,
            'confirmation_time_seconds': elapsed_time
        })
        return (False, plan)


def check_code_blocks_before_execution(code_blocks: List[Tuple[str, str]], task: str = "") -> Tuple[bool, Optional[Dict], List[str]]:
    """
    Check multiple code blocks before execution.
    Combines all file operations from all code blocks into a single plan and shows ONE dialog.
    
    Args:
        code_blocks: List of (language, code) tuples
        task: Optional task description
        
    Returns:
        (allowed, plan, blocked_codes) - allowed=True if all can proceed, combined plan, list of blocked code snippets
    """
    file_safety = get_file_safety()
    all_operations = []
    blocked_codes = []
    
    # Collect all file operations from all code blocks
    for language, code in code_blocks:
        op_type = file_safety.classify_operation(code, task)
        
        # READ_ONLY operations can proceed without confirmation
        if op_type == OperationType.READ_ONLY:
            continue
        
        # Extract file operations from this code block
        operations = file_safety.extract_file_operations(code)
        
        if not operations:
            # Classified as write but no operations extracted - block for safety
            blocked_codes.append(code)
            continue
        
        # Add operations to the combined list
        all_operations.extend(operations)
    
    # If no file operations found, allow execution
    if not all_operations:
        return (True, None, blocked_codes)
    
    # Generate combined plan for all operations
    combined_plan = file_safety.generate_plan(all_operations, task)
    
    # Check for high-risk conditions
    is_high_risk, risk_reasons = file_safety.check_high_risk(all_operations)
    if is_high_risk:
        logger.warning(f"High-risk operation detected: {risk_reasons}")
        combined_plan['high_risk'] = True
        combined_plan['risk_reasons'] = risk_reasons
    
    # Log the combined plan
    file_safety.log_operation('plan_generated', {
        'plan': combined_plan,
        'code_blocks_count': len(code_blocks),
        'task': task
    })
    
    # Show ONE confirmation dialog for all operations
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QCoreApplication
        
        app_instance = QCoreApplication.instance()
        if app_instance is None:
            logger.warning("No QApplication - denying file operations for safety")
            file_safety.log_operation('blocked_no_gui', {'plan': combined_plan})
            return (False, combined_plan, [code for lang, code in code_blocks])
        
        is_qapplication = isinstance(app_instance, QApplication) if app_instance else False
        if not is_qapplication:
            logger.warning("No QApplication (only QCoreApplication) - denying file operations for safety")
            file_safety.log_operation('blocked_no_gui', {'plan': combined_plan})
            return (False, combined_plan, [code for lang, code in code_blocks])
        
        # Show confirmation dialog with combined plan
        confirmed = confirm_file_operations_with_plan(
            combined_plan,
            require_confirmation_phrase=True,
            confirmation_phrase=file_safety.CONFIRM_FILE_CHANGES
        )
        
        if confirmed:
            file_safety.log_operation('confirmed', {'plan': combined_plan})
            return (True, combined_plan, [])
        else:
            file_safety.log_operation('cancelled', {'plan': combined_plan})
            return (False, combined_plan, [code for lang, code in code_blocks])
            
    except Exception as e:
        logger.error(f"Error showing confirmation dialog: {e}", exc_info=True)
        file_safety.log_operation('error', {'plan': combined_plan, 'error': str(e)})
        # On error, default to deny for safety
        return (False, combined_plan, [code for lang, code in code_blocks])

