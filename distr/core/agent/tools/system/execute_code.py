"""
Execute Code Tool - Direct Python code execution with robust error handling

This tool allows the LLM to execute Python code directly with robust error handling
and safety checks for file operations.
"""

import logging
import subprocess
import sys
import platform
import os
import json
from typing import Optional, Dict, Any, List
import re
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
import io
from contextlib import redirect_stdout, redirect_stderr

logger = logging.getLogger(__name__)

# Maximum retry attempts for code fixing
MAX_CODE_FIX_RETRIES = 3


# Common typo corrections
COMMON_TYPO_CORRECTIONS = {
    'ln': 'len',
    'prnt': 'print',
    'imprt': 'import',
    'retun': 'return',
    'defn': 'def',
    'whle': 'while',
    'fr': 'for',
    'f': 'if',
    'els': 'else',
    'el': 'elif',
    'True': 'True',  # Keep as-is, just for reference
    'False': 'False',  # Keep as-is, just for reference
    'Non': 'None',
    'Nne': 'None',
}


def _suggest_typo_fix(error_msg: str) -> Optional[str]:
    """Suggest fixes for common typos in error messages."""
    if 'NameError' in error_msg and 'is not defined' in error_msg:
        # Extract the undefined name
        import re
        match = re.search(r"name '(\w+)' is not defined", error_msg)
        if match:
            undefined_name = match.group(1)
            # Check if it's a common typo
            if undefined_name in COMMON_TYPO_CORRECTIONS:
                correct_name = COMMON_TYPO_CORRECTIONS[undefined_name]
                return f"Did you mean '{correct_name}' instead of '{undefined_name}'? Common typo detected."
    return None


class ExecuteCodeInput(BaseModel):
    """Input schema for execute_code tool."""
    code: str = Field(description="The Python code to execute. This should be complete, runnable Python code.")
    description: Optional[str] = Field(default=None, description="Optional description of what the code does (for logging)")


class ExecuteCodeTool(BaseTool):
    """Tool for executing Python code directly with robust error handling."""
    
    name: str = "execute_code"
    description: str = (
        "🎯 CODE EXECUTION TOOL - Execute Python code directly with FULL CONTROL of the operating system. "
        "This tool gives you complete programmatic control to do ANYTHING on the computer through Python code. "
        ""
        "⚠️ CRITICAL EXCEPTION - PROJECT MODE: "
        "If there is an ACTIVE PROJECT in context, DO NOT use this tool for work instructions! "
        "Instead, use create_project_ticket for ANY change requests like 'change the button color', "
        "'fix the bug', 'update the layout', 'make the background blue', etc. "
        "The project's IDE (Cursor) will handle the actual code changes via tickets. "
        ""
        "WHEN TO USE THIS TOOL (only when NO active project): "
        "Use for system tasks, file operations, and queries that are NOT project code changes. "
        ""
        "FULL OS CONTROL: "
        "- Execute ANY system command via subprocess "
        "- Access and modify the file system (create, read, write, delete files/folders) "
        "- Query system information (OS, paths, environment variables, hardware) "
        "- Interact with applications and services "
        "- Process data, transform files, generate content "
        "- Control system settings and preferences "
        "- Do ANYTHING that Python can do on the operating system "
        ""
        "WHEN TO USE THIS (PREFERRED - USE FOR THESE SIMPLE TASKS): "
        "- Opening files (open a file, open file with default app, open file in editor) - THIS IS SIMPLE, USE execute_code "
        "- Simple file operations (read, write, create, delete, copy, move) "
        "- Searching for files (find file by name, search in directories) "
        "- Finding/selecting random files (e.g., pick random file from folder) "
        "- System queries (get paths, check OS, list files, check defaults) "
        "- Data processing (parse JSON, transform text, calculate values) "
        "- Quick tasks that need direct code execution "
        "- Any task where you can write the Python code yourself "
        ""
        "TOOL CHAINING - RETURNING RESULTS FOR OTHER TOOLS: "
        "- When you need to find/select a file and then send it via Telegram, use execute_code first "
        "- Set a 'result' variable with the file path, and it will be returned in the output "
        "- Example: To pick a random file from Pictures: "
        "  import os, random; "
        "  pictures_dir = os.path.expanduser('~/Pictures'); "
        "  files = [f for f in os.listdir(pictures_dir) if os.path.isfile(os.path.join(pictures_dir, f))]; "
        "  result = os.path.join(pictures_dir, random.choice(files)) "
        "- The result will be shown as 'Result: /path/to/file' which can be used by send_file_to_telegram "
        "- IMPORTANT: Only chain to send_file_to_telegram if: "
        "  * The request came FROM Telegram (user is already in Telegram context), OR "
        "  * The user explicitly said 'send it to Telegram' or 'send it to my Telegram' (from desktop) "
        "- If request is from desktop and user just says 'send me a file', they likely mean open/show it, not send to Telegram "
        ""
        "SPECIFIC EXAMPLES OF SIMPLE TASKS (USE execute_code): "
        "- 'open a file' or 'open the file' -> Use execute_code with: import subprocess; subprocess.run(['open', '/path/to/file']) "
        "- 'find a file' -> Use execute_code with: import os; [os.path.join(root, f) for root, dirs, files in os.walk('/path') for f in files if 'name' in f] "
        "- 'pick a random file from Pictures' -> Use execute_code to find random file, then send_file_to_telegram with the result "
        "- 'list files' -> Use execute_code with: import os; print(os.listdir('/path')) "
        ""
        "NOTE: This tool handles all code execution tasks. For complex multi-step tasks, you can chain multiple execute_code calls or write more comprehensive Python scripts. "
        ""
        "IMPORTANT CODING GUIDELINES: "
        "- Write complete, runnable Python code "
        "- Use try/except blocks for error handling "
        "- For subprocess calls, use subprocess.run() with capture_output=True (NOT check_output with check=True) "
        "- Handle errors gracefully - check returncode before using output "
        "- For macOS defaults commands, handle cases where keys don't exist "
        ""
        "CODE EXAMPLES: "
        "- Open file: 'import os, subprocess, platform; p = os.path.expanduser(\"~/Desktop/dogbreeds.txt\"); (os.startfile(p) if platform.system()==\"Windows\" else subprocess.run([\"open\", p] if platform.system()==\"Darwin\" else [\"xdg-open\", p])) if os.path.exists(p) else print(\"File not found\")' "
        "- Find and open file: 'import os, subprocess, platform; paths = [os.path.expanduser(x) for x in [\"~/Desktop\", \"~/Documents\", \"~/Downloads\"]]; fp = next((os.path.join(d, \"dogbreeds.txt\") for d in paths if os.path.exists(os.path.join(d, \"dogbreeds.txt\"))), None); (os.startfile(fp) if platform.system()==\"Windows\" else subprocess.run([\"open\", fp] if platform.system()==\"Darwin\" else [\"xdg-open\", fp])) if fp else print(\"File not found\")' "
        "- Get home directory: 'import os; print(os.path.expanduser(\"~\"))' "
        "- List files: 'import os; print(\"\\n\".join(os.listdir(os.path.expanduser(\"~/Desktop\"))))' "
        "- Read file: 'with open(\"/path/to/file.txt\", \"r\") as f: print(f.read())' "
        "- Safe subprocess: 'result = subprocess.run([\"ls\", \"-la\"], capture_output=True, text=True); print(result.stdout if result.returncode == 0 else f\"Error: {result.stderr}\")' "
        "- Create file: 'with open(\"output.txt\", \"w\") as f: f.write(\"content\")' "
    )
    args_schema: type[BaseModel] = ExecuteCodeInput
    
    # Pydantic fields for inter-process communication (excluded from schema)
    event_queue: Optional[Any] = Field(default=None, exclude=True)
    command_queue: Optional[Any] = Field(default=None, exclude=True)
    confirmation_results_dict: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, event_queue=None, command_queue=None, confirmation_results_dict=None, **kwargs):
        """Initialize execute code tool with optional event queues."""
        super().__init__(event_queue=event_queue, command_queue=command_queue, confirmation_results_dict=confirmation_results_dict, **kwargs)
    
    def _get_dropped_files(self) -> Optional[List[str]]:
        """Get files that were dropped on the oracle ball."""
        storage_dir = os.path.join(os.path.expanduser("~"), ".decisionsai", "dropped_files")
        storage_file = os.path.join(storage_dir, "current_files.json")
        
        if not os.path.exists(storage_file):
            return None
        
        try:
            with open(storage_file, 'r') as f:
                data = json.load(f)
                files = data.get("files", [])
                chat_files_index = data.get("chat_files_index", {})

                # Prefer per-chat dropped files when we can resolve active chat.
                try:
                    from distr.core.db import get_session, Settings
                    with get_session() as session:
                        settings = session.query(Settings).first()
                        active_chat_id = None
                        if settings:
                            active_chat_id = getattr(settings, "agent_current_chat_id", None) or getattr(settings, "last_chat_id", None)
                        if active_chat_id is not None:
                            chat_bucket = chat_files_index.get(str(active_chat_id), {})
                            if isinstance(chat_bucket, dict) and chat_bucket.get("files"):
                                files = chat_bucket.get("files", [])
                except Exception:
                    # Fallback to global list if chat-specific lookup isn't available.
                    pass

                # Only return files that still exist
                existing_files = [f for f in files if os.path.exists(f)]
                return existing_files if existing_files else None
        except Exception as e:
            logger.error(f"Error reading dropped files: {e}")
            return None
    
    def _get_last_dropped_file(self) -> Optional[str]:
        """Get the most recently dropped file (last in the list)."""
        dropped_files = self._get_dropped_files()
        if not dropped_files:
            return None
        # Return the last file (most recently dropped)
        # Filter to only files (not directories)
        files_only = [f for f in dropped_files if os.path.isfile(f)]
        return files_only[-1] if files_only else None
    
    def _get_last_dropped_files(self, count: int = 5) -> List[str]:
        """Get the most recently dropped files (last N files in the list)."""
        dropped_files = self._get_dropped_files()
        if not dropped_files:
            return []
        # Filter to only files (not directories) and return the last N
        files_only = [f for f in dropped_files if os.path.isfile(f)]
        return files_only[-count:] if len(files_only) > count else files_only
    
    def _fix_code_with_llm(self, code: str, error: str, attempt: int) -> Optional[str]:
        """
        Use LLM to fix code based on error message.
        
        Args:
            code: The original code that failed
            error: The error message
            attempt: Current retry attempt number
            
        Returns:
            Fixed code or None if fixing failed
        """
        try:
            # Try to import OpenAI client
            try:
                from openai import OpenAI
            except ImportError:
                logger.warning("OpenAI client not available for code fixing")
                return None
            
            # Get API key from environment
            api_key = os.environ.get('OPENAI_API_KEY') or os.environ.get('OPENROUTER_API_KEY')
            if not api_key:
                logger.warning("No OpenAI/OpenRouter API key found for code fixing")
                return None
            
            # Use OpenRouter if OPENROUTER_API_KEY is set, otherwise OpenAI
            base_url = None
            if os.environ.get('OPENROUTER_API_KEY'):
                base_url = "https://openrouter.ai/api/v1"
                model = os.environ.get('OPENROUTER_MODEL', 'openai/gpt-4o-mini')
            else:
                model = os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')
            
            client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
            
            # Create prompt for code fixing
            fix_prompt = f"""Fix the following Python code that failed with an error.

Original code:
```python
{code}
```

Error message:
{error}

Please provide ONLY the corrected Python code without any explanations, markdown formatting, or code blocks. Just the raw Python code that will fix the error.

Corrected code:"""
            
            logger.info(f"Attempting to fix code (attempt {attempt}/{MAX_CODE_FIX_RETRIES})")
            
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a Python code fixer. Fix code errors and return only the corrected code without explanations."},
                    {"role": "user", "content": fix_prompt}
                ],
                temperature=0.1,
                max_tokens=2000
            )
            
            fixed_code = response.choices[0].message.content.strip()
            
            # Remove markdown code blocks if present
            if fixed_code.startswith("```python"):
                fixed_code = fixed_code[9:]
            elif fixed_code.startswith("```"):
                fixed_code = fixed_code[3:]
            if fixed_code.endswith("```"):
                fixed_code = fixed_code[:-3]
            fixed_code = fixed_code.strip()
            
            logger.info(f"LLM generated fixed code (length: {len(fixed_code)})")
            return fixed_code
            
        except Exception as e:
            error_str = str(e)
            # Check for API key errors - don't spam logs for these
            if '401' in error_str or 'invalid_api_key' in error_str or 'authentication' in error_str.lower():
                logger.warning(f"LLM code fixing unavailable: Invalid or missing API key. Returning original error.")
                return None
            else:
                logger.error(f"Error calling LLM to fix code: {e}", exc_info=True)
                return None
    
    def _execute_code_with_retry(self, code: str, description: Optional[str] = None) -> str:
        """
        Execute code with automatic retry and LLM-based fixing.
        
        Args:
            code: Python code to execute
            description: Optional description for logging
            
        Returns:
            Execution result or error message
        """
        current_code = code
        last_error = None
        
        for attempt in range(MAX_CODE_FIX_RETRIES + 1):
            if attempt > 0:
                logger.info(f"Retrying code execution (attempt {attempt + 1}/{MAX_CODE_FIX_RETRIES + 1})")
            
            # Execute the code
            result = self._execute_code_once(current_code, description)
            
            # Check if execution was successful
            if not result.startswith("Error:"):
                if attempt > 0:
                    logger.info(f"Code fixed successfully after {attempt} retry(ies)")
                    return f"Code fixed and executed successfully (after {attempt} retry):\n\n{result}"
                return result
            
            # Extract error message
            error_msg = result.replace("Error: ", "").strip()
            last_error = error_msg
            
            # Don't retry on certain errors that can't be fixed by LLM
            if "Subprocess command failed" in error_msg and "defaults" in error_msg:
                # System-level errors that can't be fixed by code changes
                return result
            
            # If this was the last attempt, return the error
            if attempt >= MAX_CODE_FIX_RETRIES:
                logger.warning(f"Code execution failed after {MAX_CODE_FIX_RETRIES} retry attempts")
                return f"Error: Code execution failed after {MAX_CODE_FIX_RETRIES} retry attempts. Last error: {error_msg}"
            
            # Try to fix the code with LLM
            logger.info(f"Code execution failed, attempting to fix with LLM...")
            fixed_code = self._fix_code_with_llm(current_code, error_msg, attempt + 1)
            
            if not fixed_code:
                logger.warning("LLM code fixing failed or unavailable, returning original error")
                return result
            
            # Update code for next attempt
            current_code = fixed_code
            logger.info(f"Using LLM-fixed code for retry")
        
        # Should never reach here, but just in case
        return f"Error: {last_error}"
    
    def _execute_code_once(self, code: str, description: Optional[str] = None) -> str:
        """
        Execute Python code once (single attempt, no retries).
        
        Args:
            code: Python code to execute
            description: Optional description for logging
            
        Returns:
            Execution result or error message
        """
    
    def _run(self, code: str = "", description: Optional[str] = None, **kwargs) -> str:
        """
        Execute Python code with robust error handling and automatic retry with LLM fixing.
        
        Args:
            code: Python code to execute
            description: Optional description for logging
            **kwargs: Additional arguments (ignored)
            
        Returns:
            Execution result or error message
        """
        if not code:
            return "Error: No code provided. Please provide Python code to execute."
        
        # Preprocess code to fix common issues from LLM-generated code
        # Sometimes LLM sends escaped newlines as literal '\n' instead of actual newlines
        # This happens when the entire code is on one line with \n characters
        if '\\n' in code and '\n' not in code:
            # The code appears to have escaped newlines but no real newlines
            # This is likely a formatting issue from the LLM - convert them
            logger.info(f"[EXECUTE_CODE] Detected escaped newlines in single-line code, converting...")
            # Be careful not to break legitimate backslash-n sequences (like in regex)
            # Only convert if the code looks like it should be multi-line
            if code.count('\\n') > 2:
                # Multiple escaped newlines - likely meant to be real newlines
                code = code.replace('\\n', '\n')
                logger.info(f"[EXECUTE_CODE] Converted escaped newlines to real newlines")
        
        # Also handle escaped tabs
        if '\\t' in code and '\t' not in code:
            if code.count('\\t') > 2:
                code = code.replace('\\t', '\t')
                logger.info(f"[EXECUTE_CODE] Converted escaped tabs to real tabs")
        
        # Use retry mechanism with LLM-based code fixing
        return self._execute_code_with_retry(code, description)
    
    def _execute_code_once(self, code: str, description: Optional[str] = None) -> str:
        """
        Execute Python code once (single attempt, no retries).
        
        Args:
            code: Python code to execute
            description: Optional description for logging
            
        Returns:
            Execution result or error message
        """
        try:
            # Check for file operations and require confirmation if needed
            try:
                from distr.core.agent.services.safety.interceptor import check_and_confirm_code_execution
                
                logger.info(f"[FILE SAFETY] Checking code for file operations")
                logger.info(f"[FILE SAFETY] Code length: {len(code)}, description: {description}")
                logger.info(f"[FILE SAFETY] Code preview: {code[:200]}...")
                
                allowed, plan = check_and_confirm_code_execution(
                    code, "python", description or "",
                    event_queue=self.event_queue,
                    command_queue=self.command_queue,
                    confirmation_results_dict=self.confirmation_results_dict
                )
                
                logger.info(f"[FILE SAFETY] Check result: allowed={allowed}, plan={plan is not None}")
                if plan:
                    logger.info(f"[FILE SAFETY] Plan: {plan.get('intent', 'N/A')}, files: {plan.get('files_affected_count', 0)}")
                
                if not allowed:
                    if plan:
                        files_count = plan.get('files_affected_count', 0)
                        logger.warning(f"File operations blocked - {files_count} file(s) would be affected")
                        return f"File operations were cancelled. The operation would have affected {files_count} file(s)."
                    else:
                        logger.warning("File operations blocked - no plan available")
                        return "File operations were blocked for safety."
            except ImportError:
                # File safety module not available - log and continue
                logger.warning("File safety interceptor not available - executing without safety check")
            except Exception as e:
                logger.error(f"Error checking file safety: {e}", exc_info=True)
                # On error, default to blocking for safety
                return f"File safety check failed - execution blocked for safety. Error: {str(e)}"
            
            # Get dropped files for reference in code
            dropped_files = self._get_dropped_files()
            last_dropped_file = self._get_last_dropped_file()
            last_dropped_files = self._get_last_dropped_files(count=5)  # Last 5 files
            
            # NOTE: Removed TTS status message - tools should only communicate on completion or failure
            # Users don't want intermediate "Ok, generating code quickly..." messages sent to Telegram
            
            # Create execution environment with common imports
            exec_globals = {
                '__builtins__': __builtins__,
                '__name__': '__main__',
                '__file__': '<execute_code>',
                'subprocess': subprocess,
                'sys': sys,
                'platform': platform,
                'os': __import__('os'),
                'pathlib': __import__('pathlib'),
                'json': __import__('json'),
                'shlex': __import__('shlex'),
                'logging': logging,
                # Common standard library imports
                'datetime': __import__('datetime'),
                'time': __import__('time'),
                're': __import__('re'),
                'math': __import__('math'),
                'random': __import__('random'),
                'collections': __import__('collections'),
                'itertools': __import__('itertools'),
                'functools': __import__('functools'),
                'operator': __import__('operator'),
                'string': __import__('string'),
                'unicodedata': __import__('unicodedata'),
                # File and data processing
                'csv': __import__('csv'),
                'hashlib': __import__('hashlib'),
                'base64': __import__('base64'),
                'copy': __import__('copy'),
                'pickle': __import__('pickle'),
                'tempfile': __import__('tempfile'),
                'glob': __import__('glob'),
                'fnmatch': __import__('fnmatch'),
                'zipfile': __import__('zipfile'),
                'tarfile': __import__('tarfile'),
                'gzip': __import__('gzip'),
                'bz2': __import__('bz2'),
                # Network and web
                'urllib': __import__('urllib'),
                'http': __import__('http'),
                'socket': __import__('socket'),
                # Text and markup
                'html': __import__('html'),
                'xml': __import__('xml'),
                'email': __import__('email'),
                # System and utilities
                'uuid': __import__('uuid'),
                'secrets': __import__('secrets'),
                'calendar': __import__('calendar'),
                'configparser': __import__('configparser'),
                'argparse': __import__('argparse'),
                'codecs': __import__('codecs'),
                'locale': __import__('locale'),
                # Data structures and utilities
                'struct': __import__('struct'),
                'array': __import__('array'),
                'weakref': __import__('weakref'),
                'traceback': __import__('traceback'),
                'warnings': __import__('warnings'),
                'abc': __import__('abc'),
                # Data processing libraries (if available)
                'pandas': None,
                'numpy': None,
                # Make dropped files available to code
                'dropped_files': dropped_files if dropped_files else [],  # All dropped files
                'last_dropped_file': last_dropped_file,  # Single most recent file
                'last_dropped_files': last_dropped_files  # List of most recent files (up to 5)
            }
            
            # Try to import optional standard library modules that might not be available on all systems
            optional_stdlib = {
                'sqlite3': 'sqlite3',  # Not available on some embedded Python builds
                'lzma': 'lzma',  # Not available on some older Python versions
            }
            for key, module_name in optional_stdlib.items():
                try:
                    exec_globals[key] = __import__(module_name)
                except ImportError:
                    logger.debug(f"Optional standard library module {module_name} not available")
                    pass
            
            # Try to import optional third-party libraries
            try:
                import pandas
                exec_globals['pandas'] = pandas
            except ImportError:
                pass
            
            try:
                import numpy
                exec_globals['numpy'] = numpy
            except ImportError:
                pass
            
            # Try to import PIL/Pillow for image processing (commonly used for image operations)
            try:
                from PIL import Image
                exec_globals['PIL'] = __import__('PIL')
                exec_globals['Image'] = Image
            except ImportError:
                pass
            # Use one shared namespace so comprehensions/generator expressions can
            # resolve symbols created earlier in the same executed code block.
            exec_namespace = dict(exec_globals)
            
            # Capture stdout and stderr
            stdout_capture = io.StringIO()
            stderr_capture = io.StringIO()
            
            try:
                with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                    exec(code, exec_namespace, exec_namespace)
                
                stdout_output = stdout_capture.getvalue()
                stderr_output = stderr_capture.getvalue()
                
                # Combine outputs
                result_parts = []
                if stdout_output:
                    result_parts.append(f"Output:\n{stdout_output}")
                if stderr_output:
                    result_parts.append(f"Warnings/Errors:\n{stderr_output}")
                
                # Check if there's a result variable
                if 'result' in exec_namespace:
                    result_value = exec_namespace['result']
                    if result_value is not None:
                        result_parts.append(f"Result: {result_value}")
                
                if result_parts:
                    result = "\n\n".join(result_parts)
                    # If there's a Result section with a file path, make it clear this should be used by send_file_to_telegram
                    if 'Result:' in result and ('/' in result or '\\' in result):
                        # Check if result contains a file path
                        path_match = re.search(r'Result:\s*([^\n]+)', result)
                        if path_match:
                            potential_path = os.path.expanduser(path_match.group(1).strip())
                            if os.path.exists(potential_path) and os.path.isfile(potential_path):
                                # Add explicit instruction for tool chaining - the LLM should call send_file_to_telegram
                                result += f"\n\n[ACTION REQUIRED: Call send_file_to_telegram with file_path=\"{potential_path}\" to send this file]"
                                logger.info(f"[EXECUTE_CODE] File path found in result: {potential_path} - added ACTION REQUIRED hint for tool chaining")
                    return result
                else:
                    return "Code executed successfully (no output)"
                    
            except subprocess.CalledProcessError as e:
                # Handle subprocess errors gracefully
                error_msg = f"Subprocess command failed (exit code {e.returncode})"
                if e.stdout:
                    error_msg += f"\nStdout: {e.stdout.decode('utf-8', errors='ignore') if isinstance(e.stdout, bytes) else e.stdout}"
                if e.stderr:
                    error_msg += f"\nStderr: {e.stderr.decode('utf-8', errors='ignore') if isinstance(e.stderr, bytes) else e.stderr}"
                
                # Provide helpful suggestions for common errors
                if 'defaults' in str(e.cmd) and 'PFTypes' in str(e.cmd):
                    error_msg += "\n\nNote: The macOS defaults command failed. This preference key may not exist. "
                    error_msg += "For getting default applications on macOS, consider using 'duti' command or 'LaunchServices' API instead."
                
                logger.warning(f"Subprocess error in execute_code: {error_msg}")
                return f"Error: {error_msg}"
                
            except SyntaxError as e:
                error_msg = f"Syntax error in code: {e}"
                if e.lineno:
                    error_msg += f" at line {e.lineno}"
                if e.text:
                    error_msg += f"\nProblematic line: {e.text.strip()}"
                logger.error(f"Syntax error in execute_code: {error_msg}")
                logger.error(f"Full code that failed:\n{code}")
                
                # Try to auto-fix common syntax errors
                fixed_code = code
                # Fix unterminated f-strings: print(f"Error: {e} -> print(f"Error: {e}")
                if ("unterminated f-string" in str(e).lower() or "unterminated string literal" in str(e).lower()) and e.lineno:
                    lines = code.split('\n')
                    if e.lineno <= len(lines):
                        problematic_line = lines[e.lineno - 1]
                        # Check if it's an f-string that's missing closing quote
                        if ('f"' in problematic_line or "f'" in problematic_line) and not problematic_line.rstrip().endswith('"') and not problematic_line.rstrip().endswith("'"):
                            quote_char = '"' if 'f"' in problematic_line else "'"
                            # Simple fix: add closing quote and parenthesis if it's a print statement
                            fixed_line = problematic_line.rstrip()
                            if 'print(' in fixed_line or 'print (' in fixed_line:
                                # Add closing quote and parenthesis
                                if not fixed_line.endswith(')'):
                                    fixed_line = fixed_line + quote_char + ')'
                                else:
                                    fixed_line = fixed_line + quote_char
                            else:
                                fixed_line = fixed_line + quote_char
                            
                            lines[e.lineno - 1] = fixed_line
                            fixed_code = '\n'.join(lines)
                            logger.info(f"Attempting auto-fix for unterminated f-string at line {e.lineno}")
                            
                            # Try to execute the fixed code
                            try:
                                stdout_capture = io.StringIO()
                                stderr_capture = io.StringIO()
                                with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                                    exec(fixed_code, exec_namespace, exec_namespace)
                                stdout_output = stdout_capture.getvalue()
                                stderr_output = stderr_capture.getvalue()
                                result_parts = []
                                if stdout_output:
                                    result_parts.append(f"Output (after auto-fix):\n{stdout_output}")
                                if stderr_output:
                                    result_parts.append(f"Warnings/Errors:\n{stderr_output}")
                                if 'result' in exec_namespace:
                                    result_value = exec_namespace['result']
                                    if result_value is not None:
                                        result_parts.append(f"Result: {result_value}")
                                        # Check if result is a file path for tool chaining
                                        if isinstance(result_value, str) and os.path.exists(result_value) and os.path.isfile(result_value):
                                            result_parts.append(f"\n[ACTION REQUIRED: Call send_file_to_telegram with file_path=\"{result_value}\" to send this file]")
                                return "\n".join(result_parts) if result_parts else "Code executed successfully (auto-fixed SyntaxError)"
                            except Exception as fix_err:
                                logger.warning(f"Auto-fix for SyntaxError failed: {fix_err}")
                                # Fall through to return original error
                
                return f"Error: {error_msg}"
                
            except NameError as e:
                error_msg = str(e)
                logger.error(f"NameError in code execution: {error_msg}")
                logger.error(f"Full code that failed:\n{code}")
                
                # Try to auto-fix common NameError issues in generator expressions
                # Pattern: name 'X' is not defined in generator expression
                name_error_match = re.search(r"name '(\w+)' is not defined", error_msg)
                if name_error_match:
                    undefined_var = name_error_match.group(1)
                    # Check if this is a generator expression scoping issue
                    if 'iterdir' in code or 'glob' in code or 'rglob' in code or 'Path' in code:
                        # Likely a Path operation with generator expression scoping issue
                        fixed_code = code
                        
                        # Find the loop variable (e.g., 'p' in 'for p in downloads.iterdir()')
                        loop_var_match = re.search(r'for\s+(\w+)\s+in\s+.*?(?:iterdir|glob|rglob)\(\)', code)
                        if loop_var_match:
                            loop_var = loop_var_match.group(1)
                            
                            # Check if undefined_var is assigned from loop_var (e.g., fname = p.name.lower())
                            var_assignment_pattern = rf'{undefined_var}\s*=\s*{loop_var}\.name\.lower\(\)'
                            if re.search(var_assignment_pattern, code):
                                # Fix generator expressions: sum(1 for k in keywords if k in fname) -> sum(1 for k in keywords if k in p.name.lower())
                                # Pattern: generator expression using undefined_var
                                fixed_code = re.sub(
                                    rf'\b{undefined_var}\b(?!\s*=)',
                                    f'{loop_var}.name.lower()',
                                    fixed_code
                                )
                        
                        if fixed_code != code:
                            logger.info(f"Attempting auto-fix for NameError: fixing '{undefined_var}' variable scoping in generator expression")
                            try:
                                stdout_capture = io.StringIO()
                                stderr_capture = io.StringIO()
                                with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                                    exec(fixed_code, exec_namespace, exec_namespace)
                                stdout_output = stdout_capture.getvalue()
                                stderr_output = stderr_capture.getvalue()
                                result_parts = []
                                if stdout_output:
                                    result_parts.append(f"Output (after auto-fix):\n{stdout_output}")
                                if stderr_output:
                                    result_parts.append(f"Warnings/Errors:\n{stderr_output}")
                                if 'result' in exec_namespace:
                                    result_value = exec_namespace['result']
                                    if result_value is not None:
                                        result_parts.append(f"Result: {result_value}")
                                        # Check if result is a file path for tool chaining
                                        if isinstance(result_value, str) and os.path.exists(result_value) and os.path.isfile(result_value):
                                            result_parts.append(f"\n[ACTION REQUIRED: Call send_file_to_telegram with file_path=\"{result_value}\" to send this file]")
                                return "\n".join(result_parts) if result_parts else "Code executed successfully (auto-fixed NameError)"
                            except Exception as fix_err:
                                logger.warning(f"Auto-fix failed: {fix_err}")
                                # Fall through to return error message
                
                # Legacy: Try to auto-fix common NameError issues (for 'name' specifically)
                if "'name' is not defined" in error_msg or '"name" is not defined' in error_msg:
                    # Check if code uses 'name' without a prefix - likely should be p.name, file.name, etc.
                    # Common pattern: filtering files by name in Path operations
                    if 'iterdir' in code or 'glob' in code or 'rglob' in code or 'Path' in code:
                        # Likely a Path operation - name should be p.name or path.name
                        fixed_code = code
                        
                        # Find the loop variable (e.g., 'p' in 'for p in downloads.iterdir()')
                        loop_var_match = re.search(r'for\s+(\w+)\s+in\s+.*?(?:iterdir|glob|rglob)\(\)', code)
                        if loop_var_match:
                            loop_var = loop_var_match.group(1)
                            # Fix: Replace 'name' variable with loop_var.name.lower() in generator expressions
                            # Pattern: all(k in name for k in keywords) -> all(k in loop_var.name.lower() for k in keywords)
                            # This handles scoping issues in generator expressions
                            fixed_code = re.sub(
                                r'\ball\(([^)]+)\s+in\s+name\b([^)]*)\)',
                                rf'all(\1 in {loop_var}.name.lower()\2)',
                                fixed_code
                            )
                            # Also fix standalone 'name' references in conditions (but not assignments)
                            # Pattern: if 'something' in name -> if 'something' in loop_var.name.lower()
                            fixed_code = re.sub(
                                r'(\w+)\s+in\s+name\b(?!\s*=)',
                                rf'\1 in {loop_var}.name.lower()',
                                fixed_code
                            )
                            # Fix: name.lower() -> loop_var.name.lower() (but not name = ...)
                            fixed_code = re.sub(
                                r'(?<!=\s)\bname\.(lower|upper|startswith|endswith|contains)\(',
                                rf'{loop_var}.name.\1(',
                                fixed_code
                            )
                        
                        # Fallback: if no loop var found, try common patterns with 'p'
                        if fixed_code == code:
                            # Fix: if 'something' in name -> if 'something' in p.name
                            fixed_code = re.sub(r"(\w+)\s+in\s+name\b(?!\s*=)", r"\1 in p.name.lower()", fixed_code)
                            # Fix: name.lower() -> p.name.lower()
                            fixed_code = re.sub(r"(?<!=\s)\bname\.(lower|upper|startswith|endswith|contains)\(", r"p.name.\1(", fixed_code)
                        
                        if fixed_code != code:
                            logger.info("Attempting auto-fix for NameError: fixing 'name' variable scoping issues")
                            try:
                                stdout_capture = io.StringIO()
                                stderr_capture = io.StringIO()
                                with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                                    exec(fixed_code, exec_namespace, exec_namespace)
                                stdout_output = stdout_capture.getvalue()
                                stderr_output = stderr_capture.getvalue()
                                result_parts = []
                                if stdout_output:
                                    result_parts.append(f"Output (after auto-fix):\n{stdout_output}")
                                if stderr_output:
                                    result_parts.append(f"Warnings/Errors:\n{stderr_output}")
                                if 'result' in exec_namespace:
                                    result_value = exec_namespace['result']
                                    if result_value is not None:
                                        result_parts.append(f"Result: {result_value}")
                                        # Check if result is a file path for tool chaining
                                        if isinstance(result_value, str) and os.path.exists(result_value) and os.path.isfile(result_value):
                                            result_parts.append(f"\n[ACTION REQUIRED: Call send_file_to_telegram with file_path=\"{result_value}\" to send this file]")
                                return "\n".join(result_parts) if result_parts else "Code executed successfully (auto-fixed NameError)"
                            except Exception as fix_err:
                                logger.warning(f"Auto-fix failed: {fix_err}")
                                # Fall through to return error message
                
                # Check for common typos and suggest fixes
                typo_suggestion = _suggest_typo_fix(error_msg)
                if typo_suggestion:
                    error_msg += f"\n💡 Suggestion: {typo_suggestion}"
                
                logger.error(f"Error in execute_code: {error_msg}", exc_info=True)
                # Format error for user-friendly display (will be sent to Telegram if request came from Telegram)
                return f"Error executing code: {error_msg}"
                
            except Exception as e:
                error_msg = f"Error executing code: {type(e).__name__}: {str(e)}"
                logger.error(f"Error in execute_code: {error_msg}", exc_info=True)
                # Format error for user-friendly display (will be sent to Telegram if request came from Telegram)
                return f"Error: {error_msg}"
                
        except Exception as e:
            error_msg = f"Unexpected error setting up code execution: {str(e)}"
            logger.error(f"Unexpected error in execute_code: {error_msg}", exc_info=True)
            # Format error for user-friendly display (will be sent to Telegram if request came from Telegram)
            return f"Error: {error_msg}"
    
    async def _arun(self, code: str = "", description: Optional[str] = None, **kwargs) -> str:
        """Async version of _run."""
        return self._run(code=code, description=description, **kwargs)

