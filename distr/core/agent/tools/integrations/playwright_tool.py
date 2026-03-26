"""
Playwright Tool — Generate and execute Playwright (headless Chrome) code from natural language instructions.

Pattern: Plan → Execute → Validate → Repeat
The agent writes Playwright Python code, runs it, checks the result, and the
tool automatically captures a final screenshot and sends it to the vision LLM
for analysis — so the agent can *see* what the browser looks like after execution.

Console logs (errors, warnings, info, failed network requests) are captured
automatically and included in the output alongside the vision analysis.
"""
import json
import logging
import subprocess
import sys
import os
import tempfile
import base64
from typing import Optional, Any

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Temp directory for Playwright screenshots and console logs
_PW_SCREENSHOT_DIR = os.path.join(tempfile.gettempdir(), "pw_screenshots")
_PW_CONSOLE_LOG = os.path.join(_PW_SCREENSHOT_DIR, "console.json")


class PlaywrightInput(BaseModel):
    """Input schema for the Playwright tool."""
    code: str = Field(
        description=(
            "Complete, runnable Python code that uses the Playwright library. "
            "Must include all imports (from playwright.sync_api import sync_playwright). "
            "Use headless=True by default. Print results or assertions to stdout. "
            "IMPORTANT: Always save a full-page screenshot at the end with "
            "page.screenshot(path=os.path.join(tempfile.gettempdir(), 'pw_screenshots', 'result.png'), full_page=True) "
            "so the vision LLM can verify the entire page visually.\n"
            "NOTE: Browser console logs (errors, warnings, failed requests) are "
            "captured automatically — no extra code needed.\n"
            "NOTE: The viewport defaults to 1920x1080. Use full_page=True to capture "
            "content below the fold."
        )
    )
    description: Optional[str] = Field(
        default=None,
        description="What this Playwright script is intended to do (for logging).",
    )
    analyze_screenshot: Optional[bool] = Field(
        default=True,
        description=(
            "If True (default), automatically send the final screenshot to the "
            "vision LLM for analysis after execution. Set to False for scripts "
            "that don't produce visual output (e.g. pure data scraping)."
        ),
    )


class PlaywrightTool(BaseTool):
    """Execute Playwright Python scripts for browser automation and testing.

    After execution, automatically captures a screenshot and sends it to the
    configured vision LLM for analysis, returning both the script output and
    the visual analysis in one response.
    """

    name: str = "playwright_browser"
    description: str = (
        "Run Playwright (headless Chrome) Python code for browser automation and testing. "
        "Use this tool to navigate websites, fill forms, click buttons, take screenshots, "
        "scrape content, and run end-to-end tests — all through generated Python code. "
        "\n\n"
        "WORKFLOW — Plan, Execute, Validate, Repeat:\n"
        "1. PLAN: Decide what browser actions are needed\n"
        "2. EXECUTE: Write and run Playwright Python code\n"
        "3. VALIDATE: The tool auto-captures a screenshot + browser console logs and sends "
        "them to the vision LLM for cross-referenced analysis\n"
        "4. REPEAT: If the visual analysis or console logs show issues, fix and re-run\n"
        "\n"
        "AUTO-CAPTURED DATA:\n"
        "- Screenshot: Always end with page.screenshot(path=os.path.join(tempfile.gettempdir(), 'pw_screenshots', 'result.png'), full_page=True)\n"
        "- Console logs: Automatically captured (errors, warnings, info, failed network requests)\n"
        "- Both are sent to the vision LLM for combined analysis\n"
        "- Viewport is 1920x1080 by default; use full_page=True to capture the entire scrollable page\n"
        "- Set analyze_screenshot=False to skip vision analysis (e.g. for pure data scraping)\n"
        "\n"
        "CODE GUIDELINES:\n"
        "- Always use sync_playwright (not async) unless you have a reason\n"
        "- Use headless=True (default) — set headless=False only if user asks to watch\n"
        "- Use browser.chromium.launch() for Chrome/Chromium\n"
        "- Print results to stdout so the agent can read them\n"
        "- Handle timeouts gracefully with try/except\n"
        "- Console log capture is injected automatically — no extra code needed\n"
        "\n"
        "EXAMPLE:\n"
        "import os, tempfile\n"
        "from playwright.sync_api import sync_playwright\n"
        "pw_dir = os.path.join(tempfile.gettempdir(), 'pw_screenshots')\n"
        "os.makedirs(pw_dir, exist_ok=True)\n"
        "with sync_playwright() as p:\n"
        "    browser = p.chromium.launch(headless=True)\n"
        "    page = browser.new_page(viewport={'width': 1920, 'height': 1080})\n"
        "    page.goto('https://example.com')\n"
        "    print(page.title())\n"
        "    page.screenshot(path=os.path.join(pw_dir, 'result.png'), full_page=True)\n"
        "    browser.close()\n"
    )
    args_schema: type[BaseModel] = PlaywrightInput

    event_queue: Optional[Any] = Field(default=None, exclude=True)
    command_queue: Optional[Any] = Field(default=None, exclude=True)
    confirmation_results_dict: Optional[Any] = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, command_queue=None, confirmation_results_dict=None, **kwargs):
        super().__init__(
            event_queue=event_queue,
            command_queue=command_queue,
            confirmation_results_dict=confirmation_results_dict,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Console log capture
    # ------------------------------------------------------------------

    @staticmethod
    def _build_console_wrapper(user_code: str) -> str:
        """Wrap user code with automatic browser console log capture.

        Injects page.on() listeners for console messages, page errors, and
        failed network requests. Writes captured logs to a JSON file after
        the user's script finishes.
        """
        # We inject a harness that:
        # 1. Monkey-patches browser.new_page() to auto-attach console listeners
        # 2. Runs the user's original code unchanged
        # 3. Writes captured logs to _PW_CONSOLE_LOG
        wrapper = f'''
import json as _json, os as _os, atexit as _atexit

_pw_console_logs = {{"errors": [], "warnings": [], "info": [], "failed_requests": []}}
_PW_CONSOLE_LOG = {repr(_PW_CONSOLE_LOG)}
_os.makedirs({repr(_PW_SCREENSHOT_DIR)}, exist_ok=True)

# Clear previous console log
if _os.path.exists(_PW_CONSOLE_LOG):
    _os.remove(_PW_CONSOLE_LOG)

def _pw_flush_console():
    """Write captured console logs to disk."""
    try:
        with open(_PW_CONSOLE_LOG, "w") as _f:
            _json.dump(_pw_console_logs, _f, indent=2, default=str)
    except Exception:
        pass

_atexit.register(_pw_flush_console)

def _pw_attach_listeners(page):
    """Attach console/error/request-failed listeners to a page."""
    def _on_console(msg):
        entry = {{"type": msg.type, "text": msg.text, "url": msg.location.get("url", "") if hasattr(msg, "location") and msg.location else ""}}
        if msg.type in ("error",):
            _pw_console_logs["errors"].append(entry)
        elif msg.type in ("warning",):
            _pw_console_logs["warnings"].append(entry)
        else:
            _pw_console_logs["info"].append(entry)

    def _on_page_error(exc):
        _pw_console_logs["errors"].append({{"type": "page_error", "text": str(exc)}})

    def _on_request_failed(request):
        failure = request.failure
        _pw_console_logs["failed_requests"].append({{
            "url": request.url,
            "method": request.method,
            "failure": failure if failure else "unknown",
        }})

    page.on("console", _on_console)
    page.on("pageerror", _on_page_error)
    page.on("requestfailed", _on_request_failed)

    # Default page.screenshot() to full_page=True
    _orig_screenshot = page.screenshot
    def _patched_screenshot(**ss_kwargs):
        if "full_page" not in ss_kwargs:
            ss_kwargs["full_page"] = True
        return _orig_screenshot(**ss_kwargs)
    page.screenshot = _patched_screenshot

    return page

# Monkey-patch new_page on BrowserContext and Browser to auto-attach listeners
import playwright.sync_api as _pw_sync
_orig_browser_new_page = _pw_sync.Browser.new_page
_orig_context_new_page = _pw_sync.BrowserContext.new_page

def _patched_browser_new_page(self, **kwargs):
    if "viewport" not in kwargs:
        kwargs["viewport"] = {{"width": 1920, "height": 1080}}
    page = _orig_browser_new_page(self, **kwargs)
    return _pw_attach_listeners(page)

def _patched_context_new_page(self, **kwargs):
    page = _orig_context_new_page(self, **kwargs)
    return _pw_attach_listeners(page)

_pw_sync.Browser.new_page = _patched_browser_new_page
_pw_sync.BrowserContext.new_page = _patched_context_new_page

# ---- User code below ----
'''
        return wrapper + user_code + '\n\n_pw_flush_console()\n'

    @staticmethod
    def _read_console_logs() -> Optional[dict]:
        """Read captured console logs from the JSON file."""
        if not os.path.isfile(_PW_CONSOLE_LOG):
            return None
        try:
            with open(_PW_CONSOLE_LOG, "r") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning("Failed to read console logs: %s", exc)
            return None

    @staticmethod
    def _format_console_logs(logs: dict) -> str:
        """Format console logs into a readable summary string."""
        parts = []
        if logs.get("errors"):
            parts.append(f"ERRORS ({len(logs['errors'])}):")
            for e in logs["errors"][:15]:
                text = e.get("text", "")[:200]
                parts.append(f"  • {e.get('type', 'error')}: {text}")
        if logs.get("warnings"):
            parts.append(f"WARNINGS ({len(logs['warnings'])}):")
            for w in logs["warnings"][:10]:
                parts.append(f"  • {w.get('text', '')[:200]}")
        if logs.get("failed_requests"):
            parts.append(f"FAILED REQUESTS ({len(logs['failed_requests'])}):")
            for r in logs["failed_requests"][:10]:
                parts.append(f"  • {r.get('method', '?')} {r.get('url', '?')[:150]} — {r.get('failure', '?')}")
        if logs.get("info"):
            # Only show first few info logs to avoid noise
            count = len(logs["info"])
            parts.append(f"INFO ({count} messages):")
            for i in logs["info"][:5]:
                parts.append(f"  • {i.get('text', '')[:200]}")
            if count > 5:
                parts.append(f"  ... and {count - 5} more")
        if not parts:
            parts.append("(no console output captured)")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Vision analysis helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_screenshot() -> Optional[str]:
        """Find the most recent screenshot in the Playwright screenshot dir."""
        if not os.path.isdir(_PW_SCREENSHOT_DIR):
            return None
        candidates = []
        for fname in os.listdir(_PW_SCREENSHOT_DIR):
            fpath = os.path.join(_PW_SCREENSHOT_DIR, fname)
            if os.path.isfile(fpath) and fname.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                candidates.append((os.path.getmtime(fpath), fpath))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][1]

    @staticmethod
    def _image_to_base64(image_path: str) -> Optional[str]:
        """Convert an image file to a base64 string, compressing to WebP."""
        try:
            from PIL import Image
            import io
            with Image.open(image_path) as img:
                if img.mode == "RGBA":
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="WEBP", quality=80)
                return base64.b64encode(buf.getvalue()).decode("ascii")
        except ImportError:
            # Pillow not available — fall back to raw base64
            try:
                with open(image_path, "rb") as f:
                    return base64.b64encode(f.read()).decode("ascii")
            except Exception:
                return None
        except Exception as exc:
            logger.warning("Failed to encode screenshot: %s", exc)
            return None

    def _analyze_with_vision(self, screenshot_path: str, description: Optional[str] = None,
                             console_logs: Optional[dict] = None) -> Optional[str]:
        """Send a screenshot + console logs to the configured vision LLM and return the analysis."""
        b64 = self._image_to_base64(screenshot_path)
        if not b64:
            return None

        try:
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()
        except Exception:
            logger.warning("Could not load settings for vision analysis")
            return None

        provider = (settings.get("vision_llm_provider") or "ollama").strip().lower()
        model = settings.get("vision_llm_model") or ""

        # Build console log context for the prompt
        console_section = ""
        if console_logs:
            console_summary = self._format_console_logs(console_logs)
            console_section = (
                "\n\nBROWSER CONSOLE LOGS captured during execution:\n"
                f"{console_summary}\n"
            )

        prompt = (
            "You are analyzing a browser screenshot captured by Playwright after executing an automated script.\n"
            f"{'Script goal: ' + description + chr(10) if description else ''}"
            f"{console_section}"
            "\nAnalyze the screenshot AND the console logs together:\n"
            "1. What page/URL is loaded?\n"
            "2. Is the page fully loaded or are there errors (404, timeouts, blank page)?\n"
            "3. What is the main visible content?\n"
            "4. Are there any error messages, popups, or unexpected states?\n"
            "5. Does the visual state look correct for the intended action?\n"
            "6. Do the console logs reveal any issues not visible on screen "
            "(JS errors, failed API calls, missing resources)?\n"
            "7. Cross-check: Does what you SEE match what the console logs REPORT? "
            "Flag any contradictions (e.g. page looks blank + console shows 500 error).\n"
            "Be concise — focus on what matters for validating the script worked."
        )

        try:
            return self._call_vision(provider, model, b64, prompt, settings)
        except Exception as exc:
            logger.warning("Vision analysis failed: %s", exc)
            return None

    @staticmethod
    def _call_vision(provider: str, model: str, b64_image: str, prompt: str, settings: dict) -> Optional[str]:
        """Call the vision LLM. Supports Ollama, OpenAI, Anthropic, OpenRouter, Groq."""
        import requests as _requests

        content_items = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/webp;base64,{b64_image}"}},
        ]
        messages = [{"role": "user", "content": content_items}]

        # --- Ollama ---
        if provider == "ollama":
            ollama_url = (settings.get("ollama_url") or "http://localhost:11434/").rstrip("/") + "/"
            resp = _requests.post(
                f"{ollama_url}api/chat",
                json={
                    "model": model or "llava",
                    "messages": [{"role": "user", "content": prompt, "images": [b64_image]}],
                    "stream": False,
                },
                timeout=120,
            )
            if resp.status_code == 200:
                return (resp.json().get("message") or {}).get("content", "")
            return None

        # --- OpenAI ---
        if provider == "openai":
            api_key = settings.get("openai_key", "")
            if not api_key:
                return None
            resp = _requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model or "gpt-4o", "messages": messages, "max_tokens": 1000},
                timeout=120,
            )
            if resp.status_code == 200:
                choices = resp.json().get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
            return None

        # --- Anthropic ---
        if provider == "anthropic":
            api_key = settings.get("anthropic_key", "")
            if not api_key:
                return None
            try:
                from anthropic import Anthropic
                client = Anthropic(api_key=api_key)
                resp = client.messages.create(
                    model=model or "claude-3-5-sonnet-20241022",
                    max_tokens=1000,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image", "source": {"type": "base64", "media_type": "image/webp", "data": b64_image}},
                        ],
                    }],
                )
                if resp and resp.content:
                    return resp.content[0].text
            except Exception:
                return None

        # --- OpenRouter / Groq / KiloCode (OpenAI-compatible) ---
        if provider in ("openrouter", "groq", "kilocode"):
            key_map = {"openrouter": "openrouter_key", "groq": "groq_key", "kilocode": "kilo_key"}
            url_map = {
                "openrouter": "https://openrouter.ai/api/v1/chat/completions",
                "groq": "https://api.groq.com/openai/v1/chat/completions",
                "kilocode": (settings.get("kilocode_url") or "https://api.kilo.ai/api/gateway").rstrip("/") + "/chat/completions",
            }
            api_key = settings.get(key_map[provider], "")
            if not api_key:
                return None
            resp = _requests.post(
                url_map[provider],
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model or "openai/gpt-4o", "messages": messages, "max_tokens": 1000},
                timeout=120,
            )
            if resp.status_code == 200:
                choices = resp.json().get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
            return None

        logger.warning("Vision provider '%s' not supported for Playwright analysis", provider)
        return None

    # ------------------------------------------------------------------
    # Main execution
    # ------------------------------------------------------------------

    def _run(self, code: str = "", description: Optional[str] = None, analyze_screenshot: Optional[bool] = True, **kwargs) -> str:
        if not code.strip():
            return "Error: No code provided. Write Playwright Python code to execute."

        label = description or "Playwright script"
        logger.info("PlaywrightTool: executing — %s", label)

        # Ensure screenshot directory exists
        os.makedirs(_PW_SCREENSHOT_DIR, exist_ok=True)

        # Clear previous console log
        if os.path.exists(_PW_CONSOLE_LOG):
            try:
                os.remove(_PW_CONSOLE_LOG)
            except OSError:
                pass

        # Confirm with user if safety gate is on
        if self.event_queue is not None and self.command_queue is not None:
            try:
                from distr.core.settings import load_settings_from_db
                settings = load_settings_from_db()
                if settings.get("always_confirm_file_operations", True):
                    import uuid, time
                    confirmation_id = str(uuid.uuid4())
                    preview = code[:300] + ("..." if len(code) > 300 else "")
                    self.event_queue.put({
                        "type": "confirmation_request",
                        "confirmation_id": confirmation_id,
                        "title": "Run Playwright Script",
                        "message": f"Execute browser automation?\n\n{preview}",
                    })
                    deadline = time.time() + 120
                    while time.time() < deadline:
                        if self.confirmation_results_dict and confirmation_id in self.confirmation_results_dict:
                            result = self.confirmation_results_dict.pop(confirmation_id)
                            if not result.get("approved"):
                                return "Playwright execution cancelled by user."
                            break
                        time.sleep(0.2)
                    else:
                        return "Playwright execution timed out waiting for confirmation."
            except Exception as e:
                logger.warning("Confirmation check failed, proceeding: %s", e)

        # Wrap user code with console log capture harness
        wrapped_code = self._build_console_wrapper(code)

        # Write code to a temp file and execute
        tmp = None
        try:
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", prefix="pw_", delete=False)
            tmp.write(wrapped_code)
            tmp.close()

            result = subprocess.run(
                [sys.executable, tmp.name],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=os.path.expanduser("~"),
            )

            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()

            if result.returncode == 0:
                output = stdout if stdout else "(no output)"
                if stderr:
                    output += f"\n\nWarnings:\n{stderr[-500:]}"
                response = f"✓ Playwright script completed successfully.\n\nOutput:\n{output}"
            else:
                error = stderr if stderr else "(no error output)"
                hint = ""
                if "ModuleNotFoundError" in error and "playwright" in error:
                    hint = "\n\nHint: Playwright is not installed. Run: pip install playwright && playwright install chromium"
                elif "Executable doesn't exist" in error or "browserType.launch" in error:
                    hint = "\n\nHint: Browser not installed. Run: playwright install chromium"
                return f"✗ Playwright script failed (exit code {result.returncode}).\n\nError:\n{error[-1000:]}{hint}"

        except subprocess.TimeoutExpired:
            return "✗ Playwright script timed out after 120 seconds."
        except Exception as e:
            return f"✗ Failed to run Playwright script: {e}"
        finally:
            if tmp and os.path.exists(tmp.name):
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass

        # --- Read captured console logs ---
        console_logs = self._read_console_logs()
        if console_logs:
            console_summary = self._format_console_logs(console_logs)
            response += f"\n\n--- Browser Console Logs ---\n{console_summary}"

        # --- Vision analysis of the screenshot (with console logs for cross-check) ---
        if analyze_screenshot:
            screenshot_path = self._find_screenshot()
            if screenshot_path:
                logger.info("PlaywrightTool: found screenshot at %s, sending to vision LLM", screenshot_path)
                analysis = self._analyze_with_vision(screenshot_path, description, console_logs)
                if analysis:
                    response += f"\n\n--- Browser Screenshot Analysis (Vision LLM) ---\n{analysis}"
                else:
                    response += "\n\n(Screenshot found but vision analysis was unavailable)"
            else:
                response += (
                    "\n\n(No screenshot found. Add "
                    "page.screenshot(path=os.path.join(tempfile.gettempdir(), 'pw_screenshots', 'result.png')) "
                    "to your script for visual validation.)"
                )

        return response

    async def _arun(self, code: str = "", description: Optional[str] = None, analyze_screenshot: Optional[bool] = True, **kwargs) -> str:
        return self._run(code=code, description=description, analyze_screenshot=analyze_screenshot, **kwargs)
