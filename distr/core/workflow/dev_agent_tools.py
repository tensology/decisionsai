"""Media/vision/computer-use tools for the Development agent (development threads).

Each tool reuses an existing app capability and its existing settings row:
  image_generate -> agent/tools/vision/image_generator.py   (image_llm_provider/model)
  vision_analyze -> agent/services/llm/image_utils.py       (vision_llm_provider/model)
  video_generate -> agent/tools/vision/video_generator.py   (video_llm_provider/model, Pixazo)
                    + pixazo_frames.py for start/end-frame mode
  computer_use   -> DISABLED scaffold (no live control; see docs/dev-agent-tools.md)

Results are JSON dicts: {"ok", "tool", "summary", "artifacts": [{"path","mime","kind"}]}.
Generated files land in <project>/.decisions/dev-artifacts/ so the thread's
change manifest and attachment renderer can pick them up.

CLI (used by the Pi extension pi_extensions/dev_media_tools.ts):
  python -m distr.core.workflow.dev_agent_tools <tool> '<json args>'
Never prints API keys; only key presence is reported.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "image_generate": {
        "description": "Generate an image from a text prompt with the configured Image model (Settings > LLMs > Image).",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "filename": {"type": "string", "description": "Optional file name (png)"},
                "style": {"type": "string"},
            },
            "required": ["prompt"],
        },
    },
    "vision_analyze": {
        "description": "Describe or answer a question about an image file with the configured Vision model.",
        "parameters": {
            "type": "object",
            "properties": {
                "image_path": {"type": "string", "description": "Path inside the project or a thread attachment"},
                "question": {"type": "string"},
            },
            "required": ["image_path"],
        },
    },
    "video_generate": {
        "description": (
            "Generate a short video with the configured Pixazo video model. Text-to-video by default; "
            "pass start_image and end_image for first/last-frame interpolation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "start_image": {"type": "string", "description": "Opening frame: local path or https URL"},
                "end_image": {"type": "string", "description": "Closing frame: local path or https URL"},
                "duration": {"type": "integer", "minimum": 2, "maximum": 30, "default": 5},
                "frame_model": {"type": "string", "description": "seedance-2.5-frames | wan-3.0-frames | veo-3.1-fast-frames"},
                "filename": {"type": "string"},
            },
            "required": ["prompt"],
        },
    },
    "computer_use": {
        "description": "Desktop control. Disabled by default; requires an explicit per-run approval in the thread.",
        "parameters": {
            "type": "object",
            "properties": {"goal": {"type": "string"}, "approval_id": {"type": "string"}},
            "required": ["goal"],
        },
    },
}


def _settings() -> dict[str, Any]:
    from distr.core.settings import load_settings_from_db

    return load_settings_from_db() or {}


def artifacts_dir(project_folder: Optional[str] = None) -> Path:
    base = Path(project_folder or os.getcwd()).expanduser().resolve()
    out = base / ".decisions" / "dev-artifacts"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _safe_name(text: str, ext: str, filename: Optional[str] = None) -> str:
    if filename:
        stem = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).stem)[:60] or "artifact"
    else:
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", (text or "")[:40]).strip("_") or "artifact"
    return f"{stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _result(tool: str, ok: bool, summary: str, artifacts: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    return {"ok": ok, "tool": tool, "summary": summary[:2000], "artifacts": artifacts or [], **extra}


def _artifact(path: Path, kind: str) -> dict[str, Any]:
    return {"path": str(path), "kind": kind, "mime": mimetypes.guess_type(str(path))[0] or "application/octet-stream",
            "bytes": path.stat().st_size if path.exists() else 0}


def tools_status(settings: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Configuration presence per tool (never returns key values)."""
    s = settings if settings is not None else _settings()
    try:
        from distr.core.third_party_keys import pixazo_api_key
        pixazo = bool(pixazo_api_key())
    except Exception:
        pixazo = False
    try:
        from distr.core.agent.services.llm.image_utils import resolve_vision_model
        vision = resolve_vision_model(s)
    except Exception:
        vision = None
    return {
        "image_generate": {"provider": s.get("image_llm_provider") or "", "model": s.get("image_llm_model") or "",
                           "configured": bool((s.get("image_llm_model") or "").strip())},
        "vision_analyze": {"provider": vision[0] if vision else "", "model": vision[1] if vision else "",
                           "configured": bool(vision)},
        "video_generate": {"provider": s.get("video_llm_provider") or "", "model": s.get("video_llm_model") or "",
                           "configured": bool((s.get("video_llm_model") or "").strip()) and pixazo,
                           "pixazo_key_present": pixazo},
        "computer_use": {"provider": s.get("computer_use_provider") or "", "model": s.get("computer_use_model") or "",
                         "configured": False, "enabled": False, "reason": "disabled by design (scaffold only)"},
    }


def image_generate(prompt: str, filename: str | None = None, style: str | None = None, *, project_folder: str | None = None,
                   _tool_factory: Callable[[], Any] | None = None) -> dict[str, Any]:
    if not (prompt or "").strip():
        return _result("image_generate", False, "prompt is required")
    out = artifacts_dir(project_folder) / _safe_name(prompt, "png", filename)
    if _tool_factory is None:
        from distr.core.agent.tools.vision.image_generator import ImageGeneratorTool
        _tool_factory = ImageGeneratorTool
    message = str(_tool_factory()._run(prompt=prompt.strip(), output_path=str(out), style=style) or "")
    if message.startswith("Error") or not out.exists():
        return _result("image_generate", False, message or "image generation failed")
    return _result("image_generate", True, f"Image saved to {out}", [_artifact(out, "image")])


def vision_analyze(image_path: str, question: str = "", *, project_folder: str | None = None,
                   _analyze: Callable[..., Any] | None = None) -> dict[str, Any]:
    p = Path(image_path or "").expanduser()
    if not p.is_absolute():
        p = Path(project_folder or os.getcwd()) / p
    if not p.is_file():
        return _result("vision_analyze", False, f"image not found: {image_path}")
    if not (mimetypes.guess_type(str(p))[0] or "").startswith("image/"):
        return _result("vision_analyze", False, "file is not an image")
    if _analyze is None:
        from distr.core.agent.services.llm.image_utils import analyze_image_with_vision_llm
        _analyze = analyze_image_with_vision_llm
    text = _analyze(str(p), question or "Describe this image in detail.")
    if asyncio.iscoroutine(text):
        text = asyncio.run(text)
    text = str(text or "").strip()
    if not text:
        return _result("vision_analyze", False, "No vision-capable model configured (Settings > LLMs > Vision).")
    return _result("vision_analyze", True, text, image_path=str(p))


def video_generate(prompt: str, start_image: str | None = None, end_image: str | None = None, duration: int | None = 5,
                   frame_model: str | None = None, filename: str | None = None, *, project_folder: str | None = None,
                   _text_tool_factory: Callable[[], Any] | None = None,
                   _frames_fn: Callable[..., list[str]] | None = None,
                   _download: Callable[..., bytes] | None = None) -> dict[str, Any]:
    if bool(start_image) != bool(end_image):
        return _result("video_generate", False, "start_image and end_image must be given together")
    out = artifacts_dir(project_folder) / _safe_name(prompt or "video", "mp4", filename)
    if start_image and end_image:
        from distr.core.pixazo_frames import DEFAULT_FRAME_MODEL, FRAME_MODELS
        from distr.core.third_party_keys import pixazo_api_key
        model = frame_model or (_settings().get("video_frames_model") or DEFAULT_FRAME_MODEL)
        if model not in FRAME_MODELS:
            return _result("video_generate", False, f"unknown frame_model {model}")
        key = pixazo_api_key()
        if not key:
            return _result("video_generate", False, "Pixazo API key not configured (Settings > API Keys).")
        root = Path(project_folder or os.getcwd())
        resolved = []
        for img in (start_image, end_image):
            if img.startswith(("https://", "data:")):
                resolved.append(img)
            else:
                ip = Path(img).expanduser()
                ip = ip if ip.is_absolute() else root / ip
                resolved.append(str(ip))
        if _frames_fn is None:
            from distr.core.pixazo_frames import pixazo_frames_to_video_urls
            _frames_fn = pixazo_frames_to_video_urls
        try:
            urls = _frames_fn(key, model, prompt or "", resolved[0], resolved[1], duration)
            if _download is None:
                from distr.core.pixazo_client import download_url_to_bytes
                _download = download_url_to_bytes
            out.write_bytes(_download(urls[0], timeout=300))
        except Exception as exc:
            return _result("video_generate", False, f"frame video failed: {exc}")
        return _result("video_generate", True, f"Video saved to {out} ({model}, start/end frames)", [_artifact(out, "video")])
    if _text_tool_factory is None:
        from distr.core.agent.tools.vision.video_generator import VideoGeneratorTool
        _text_tool_factory = VideoGeneratorTool
    message = str(_text_tool_factory()._run(prompt=prompt, output_path=str(out), duration_sec=duration) or "")
    if message.startswith("Error") or not out.exists():
        return _result("video_generate", False, message or "video generation failed")
    return _result("video_generate", True, f"Video saved to {out}", [_artifact(out, "video")])


def computer_use(goal: str, approval_id: str | None = None, **_: Any) -> dict[str, Any]:
    """Scaffold only. Never drives the desktop from a development thread.

    Planned gate (docs/dev-agent-tools.md): setting dev_computer_use_enabled (default off) AND
    a one-shot approval record for this chat/turn created by the user clicking Approve in the
    thread; then delegate to agent/tools/input/computer_use.py behind computer_use_guard.py.
    """
    return _result("computer_use", False, "computer_use is disabled for the Development agent (scaffold, no live control).",
                   disabled=True, goal=goal[:500])


DISPATCH: dict[str, Callable[..., dict[str, Any]]] = {
    "image_generate": image_generate,
    "vision_analyze": vision_analyze,
    "video_generate": video_generate,
    "computer_use": computer_use,
}

# Tools added in their own modules register here so every harness picks them up.
from distr.core.workflow import dev_approvals as _apr  # noqa: E402
from distr.core.workflow import dev_browser_tools as _brw  # noqa: E402
from distr.core.workflow import dev_calendar_tools as _cal  # noqa: E402
from distr.core.workflow import dev_file_tools as _fil  # noqa: E402
from distr.core.workflow import dev_gmail_tools as _gmail  # noqa: E402

for _name, _schema, _fn in (
    ("calendar", _cal.SCHEMA, _cal.calendar),
    ("gmail_search", _gmail.SCHEMA, _gmail.gmail_search),
    ("files", _fil.SCHEMA, _fil.files),
    ("browser", _brw.SCHEMA, _brw.browser),
    ("approval_wait", _apr.APPROVAL_WAIT_SCHEMA, _apr.approval_wait),
):
    TOOL_SCHEMAS[_name] = _schema
    DISPATCH[_name] = _fn


# 2026-10-22 selthread: web UI thread selection (shared with the Oracle tools).
_THREAD_SCHEMA_PROPS = {
    "thread": {"type": "string", "description": "'latest' (most recently updated), a numeric thread id, or words from the title"},
    "surface": {"type": "string", "enum": ["development", "chat"], "default": "development"},
}


def _thread_tool(name: str, fn_name: str):
    def run(thread: str = "latest", surface: str | None = "development", **_: Any) -> dict[str, Any]:
        from distr.core import ui_thread_select as uts
        res = getattr(uts, fn_name)(thread, surface)
        return _result(name, bool(res.get("ok")), str(res.get("summary") or ""), **{k: v for k, v in res.items() if k not in {"ok", "summary"}})
    run.__name__ = name
    return run


TOOL_SCHEMAS["select_thread"] = {
    "description": (
        "Select and highlight a thread in the Decisions web UI left sidebar (Development threads by default, or Chat) "
        "in every open tab, live. If no tab acknowledges within ~1.5s it opens the thread in the default browser. "
        "Result path is 'selected' or 'opened'. Use for 'select the latest thread' / 'select thread 1798'."
    ),
    "parameters": {"type": "object", "properties": dict(_THREAD_SCHEMA_PROPS), "required": ["thread"]},
}
TOOL_SCHEMAS["open_thread"] = {
    "description": (
        "Open a Decisions thread page in the default browser (Development by default, or Chat). Reuses an open tab "
        "(select + focus) instead of a duplicate. Use for 'open my most recent thread' or when the web UI is not open; "
        "use select_thread when the user says select and the UI is open."
    ),
    "parameters": {"type": "object", "properties": dict(_THREAD_SCHEMA_PROPS), "required": ["thread"]},
}
DISPATCH["select_thread"] = _thread_tool("select_thread", "select_thread")
DISPATCH["open_thread"] = _thread_tool("open_thread", "open_thread")

# Never exposed to agents (computer_use is a disabled scaffold).
HIDDEN_TOOLS = {"computer_use"}


def exposed_tools() -> dict[str, dict[str, Any]]:
    """Schemas every harness (Pi extension, MCP bridge) exposes: TOOL_SCHEMAS minus
    hidden tools and anything the Decisions MCP deny-list blocks (speech)."""
    from distr.core.mcp_server.tools import is_denied

    return {n: s for n, s in TOOL_SCHEMAS.items()
            if n in DISPATCH and n not in HIDDEN_TOOLS and not is_denied(n)}


def call_tool(name: str, args: dict[str, Any], *, project_folder: str | None = None) -> dict[str, Any]:
    fn = DISPATCH.get(name)
    try:
        from distr.core.mcp_server.tools import is_denied
        if is_denied(name):
            fn = None
    except Exception:
        fn = None
    if fn is None:
        return _result(name, False, f"unknown tool {name}")
    allowed = set(TOOL_SCHEMAS[name]["parameters"]["properties"])
    clean = {k: v for k, v in (args or {}).items() if k in allowed}
    try:
        if name == "computer_use":
            return fn(**clean)
        return fn(**clean, project_folder=project_folder)
    except TypeError as exc:
        return _result(name, False, f"bad arguments: {exc}")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in {"-h", "--help"}:
        print(json.dumps({"tools": TOOL_SCHEMAS}, indent=1))
        return 0
    if argv[1] == "schemas":
        print(json.dumps(exposed_tools()))
        return 0
    if argv[1] == "status":
        print(json.dumps(tools_status()))
        return 0
    args = json.loads(argv[2]) if len(argv) > 2 else {}
    result = call_tool(argv[1], args, project_folder=os.environ.get("DECISIONS_PROJECT_FOLDER") or os.getcwd())
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
