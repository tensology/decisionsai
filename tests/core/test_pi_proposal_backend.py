"""Proposal transport tests: all CLI/model subprocesses are fakes, no live DB."""
import asyncio
import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from distr.core.project_cli_backends import registry as r
from distr.core.project_cli_backends.base import ProjectTask


def task(**changes):
    values = dict(project_id=13, project_name="Private project", folder="/never/read/project",
                  instruction='@/private/file\n{"context":"supplied only"}', model="exact-model",
                  adapter_options={"proposal_only": True, "model_provider": "ollama"})
    values.update(changes)
    return ProjectTask(**values)


def transcript(output, **message):
    return [{"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
             "content": [{"type": "text", "text": output}], **message}}, {"type": "agent_end"}]


@pytest.fixture
def transport(monkeypatch):
    from distr.core.pi_rpc import PiRpcSession
    monkeypatch.setattr(PiRpcSession, "find_pi", staticmethod(lambda: "/fake/pi"))
    seen = {"launches": [], "events": transcript('{"reply":"ok","edits":[]}'), "real_preflight": r._proposal_preflight}

    def preflight(command, *, cwd, env, require_images=False):
        assert list(Path(cwd).iterdir()) == []
        assert cwd != "/never/read/project"
        assert env["PI_OFFLINE"] == "1" and env["PI_TELEMETRY"] == "0"
        seen["preflight"] = command
        seen["require_images"] = require_images
    monkeypatch.setattr(r, "_proposal_preflight", preflight)

    class Pipe:
        def write(self, data):
            seen["input"] = data
        async def drain(self):
            pass
        def close(self):
            pass

    class Process:
        returncode = None
        stdin = Pipe()
        def __init__(self):
            self.stdout = self
            self.lines = iter([json.dumps(event).encode() + b"\n" for event in seen["events"]])
        async def readline(self):
            if seen.get("stall"):
                await asyncio.Event().wait()
            return next(self.lines, b"")
        async def wait(self):
            self.returncode = seen.get("exit_code", 0)
            return self.returncode
        def terminate(self):
            seen["terminated"] = True
            self.returncode = -15
        def kill(self):
            self.returncode = -9

    async def launch(*args, **kwargs):
        seen["launches"].append((args, kwargs))
        seen["image_files"] = [(arg[1:], Path(arg[1:]).read_bytes()) for arg in args if arg.startswith("@")]
        return Process()
    monkeypatch.setattr(r.asyncio, "create_subprocess_exec", launch)
    return seen


def test_complete_large_json_and_isolated_context(transport):
    output = json.dumps({"reply": "x" * 20000, "edits": []})
    transport["events"] = transcript(output)
    result = asyncio.run(r.PiBackend().send_task(task(origin="cli")))
    assert result.success and result.output == output
    assert result.engine == "pi_proposal"
    args, kwargs = transport["launches"][0]
    assert all(flag in args for flag in r._PROPOSAL_FLAGS)
    assert not any(flag in args for flag in ("--session-id", "--extension", "--append-system-prompt", "--tools"))
    assert task().instruction not in args
    assert transport["input"].decode() == task().instruction
    assert not Path(kwargs["cwd"]).exists()
    assert r._ONE_SHOT_PROCESSES == {}


@pytest.mark.parametrize("events", [
    transcript("not JSON"), transcript('{"reply":"a","reply":"b"}'),
    transcript('{"reply":NaN}'), transcript("[]"),
    transcript('{"reply":"partial"}', stopReason="length"),
    transcript('{"reply":"x"}', errorMessage="sk-secret"),
    [{"type": "tool_execution_start", "toolName": "bash"}],
    transcript('{"reply":"x"}')[:1],
    transcript('{"reply":"x"}')[:1] + transcript('{"reply":"y"}'),
])
def test_invalid_or_incomplete_output_fails_closed(transport, events):
    transport["events"] = events
    result = asyncio.run(r.PiBackend().send_task(task()))
    assert not result.success and not result.output
    assert "sk-secret" not in result.error
    assert transport["terminated"]


def test_output_limit_never_truncates(transport, monkeypatch):
    monkeypatch.setattr(r, "_PROPOSAL_MAX_OUTPUT", 20)
    transport["events"] = transcript(json.dumps({"reply": "x" * 30}))
    result = asyncio.run(r.PiBackend().send_task(task()))
    assert not result.success and result.output == ""


def test_repeated_progress_snapshots_do_not_count_as_new_output(transport):
    output = json.dumps({"reply": "x" * 5000, "edits": []})
    transport["events"] = [
        {"type": "message_update", "message": {"role": "assistant", "content": [{"type": "text", "text": output}]},
         "assistantMessageEvent": {"type": "text_delta", "delta": "x"}}
        for _ in range(2000)
    ] + transcript(output)
    result = asyncio.run(r.PiBackend().send_task(task()))
    assert result.success and result.output == output


@pytest.mark.parametrize("options", [
    {"images": ["/private/image.png"]}, {"attachments": [{"path": "/private/image.png"}]},
    {"required_capabilities": ["images"]}, {"fallback_backend": "codex"},
    {"model_provider": ""}, {"timeout_seconds": 601},
])
def test_unsupported_options_never_launch(transport, options):
    options = {**task().adapter_options, **options}
    result = asyncio.run(r.PiBackend().send_task(task(adapter_options=options,
                        required_capabilities=options.get("required_capabilities", []))))
    assert not result.success and not transport["launches"]


@pytest.mark.parametrize("backend", ["cursor", "cursor_ide", "unknown", "openai"])
def test_registry_rejects_unsupported_backend_before_any_project_access(transport, monkeypatch, backend):
    monkeypatch.setattr(r, "_git_status_short", lambda *a: pytest.fail("Project snapshot"))
    result = asyncio.run(r.run_project_task(SimpleNamespace(id=13, name="Private", coding_backend=backend),
                        "context", backend_id_override=backend, model_override="exact-model",
                        adapter_options=task().adapter_options))
    assert not result.success and not transport["launches"]
    assert result.diagnostics["proposal_error"]["code"] == "route_unavailable"


def test_registry_proposal_bypasses_project_snapshots_and_enrichment(transport, monkeypatch):
    monkeypatch.setattr(r, "_git_status_short", lambda *a: pytest.fail("Project snapshot"))
    monkeypatch.setattr(r, "_workspace_state_snapshot", lambda *a: pytest.fail("Workspace snapshot"))
    result = asyncio.run(r.run_project_task(SimpleNamespace(id=13, name="Private", coding_backend="pi"),
                        "bounded context", backend_id_override="pi", model_override="exact-model",
                        adapter_options=task().adapter_options))
    assert result.success
    assert transport["input"] == b"bounded context"


@pytest.mark.parametrize("backend", [r.CodexBackend, r.CursorBackend, r.CursorIdeBackend])
def test_direct_unsupported_adapter_rejects_before_probe(transport, monkeypatch, backend):
    monkeypatch.setattr(backend, "setup_status", lambda *a: pytest.fail("Backend probe"))
    result = asyncio.run(backend().send_task(task()))
    assert not result.success
    assert result.diagnostics["proposal_error"]["code"] == "route_unavailable"
    assert not transport["launches"]


def test_missing_pi_reports_safe_route_error(transport, monkeypatch):
    from distr.core.pi_rpc import PiRpcSession
    monkeypatch.setattr(PiRpcSession, "find_pi", lambda: None)
    result = asyncio.run(r.PiBackend().send_task(task()))
    assert not result.success and not transport["launches"]
    assert result.diagnostics["proposal_error"]["code"] == "route_unavailable"


def test_local_preflight_requires_all_flags_and_exact_model(monkeypatch, tmp_path):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=" ".join(r._PROPOSAL_FLAGS) if "--help" in command else
                               "provider model context max-out thinking images\nollama exact-model 128K 8K no no\n")
    monkeypatch.setattr(r.subprocess, "run", run)
    command = r._proposal_command("/fake/pi", task())
    r._proposal_preflight(command, cwd=str(tmp_path), env={})
    assert "--list-models" in calls[-1]
    command[command.index("--model") + 1] = "exact"
    with pytest.raises(ValueError, match="exact available"):
        r._proposal_preflight(command, cwd=str(tmp_path), env={})
    monkeypatch.setattr(r.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="--no-tools"))
    with pytest.raises(ValueError, match="isolation flags"):
        r._proposal_preflight(command, cwd=str(tmp_path), env={})


def test_regular_pi_command_stays_unrestricted():
    regular = task(adapter_options={"model_provider": "ollama"})
    command = r._pi_print_command("pi", regular)
    assert "--no-tools" not in command
    assert command[-1] == regular.instruction


def test_proposal_system_is_bounded_literal_text(transport):
    system = "@/private/system.md\nReturn the canonical Plan schema and DSL."
    options = {**task().adapter_options, "proposal_system": system}
    assert asyncio.run(r.PiBackend().send_task(task(adapter_options=options))).success
    command = transport["launches"][0][0]
    assert command[command.index("--system-prompt") + 1] == "Plan proposal system instructions (literal text):\n" + system
    assert transport["input"].decode() == task().instruction
    for invalid in (123, "x" * 32001, "a\x00b"):
        with pytest.raises(r.ProposalValidationError):
            r._proposal_command("pi", task(adapter_options={**options, "proposal_system": invalid}))


@pytest.mark.parametrize("events,code,stage", [
    (transcript("not JSON"), "invalid_json", "json_validation"),
    (transcript('{"reply":"partial"}', stopReason="length"), "model_length", "stream"),
    (transcript('{"reply":"x"}')[:1], "incomplete_stream", "stream"),
    ([{"type": "error", "errorMessage": "Authorization: sk-secret"}], "model_error", "stream"),
])
def test_safe_failure_stage_and_code(transport, events, code, stage):
    transport["events"] = events
    result = asyncio.run(r.PiBackend().send_task(task()))
    assert result.diagnostics["proposal_error"]["code"] == code
    assert result.diagnostics["proposal_error"]["stage"] == stage
    assert "sk-secret" not in json.dumps(result.to_dict())
    assert not result.output


def test_timeout_terminates_worker_without_retry(transport):
    transport["stall"] = True
    options = {**task().adapter_options, "timeout_seconds": 1}
    result = asyncio.run(r.PiBackend().send_task(task(adapter_options=options)))
    assert not result.success and not result.output
    assert result.diagnostics["proposal_error"]["code"] == "timeout"
    assert transport["terminated"]
    assert len(transport["launches"]) == 1
    assert not r._ONE_SHOT_PROCESSES


def test_cancellation_terminates_worker(transport):
    transport["stall"] = True
    async def cancel():
        worker = asyncio.create_task(r.PiBackend().send_task(task()))
        while not transport["launches"]:
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
    asyncio.run(cancel())
    assert transport["terminated"]
    assert not r._ONE_SHOT_PROCESSES


def test_unsuccessful_exit_does_not_return_valid_looking_json(transport):
    transport["exit_code"] = 1
    result = asyncio.run(r.PiBackend().send_task(task()))
    assert not result.success and not result.output


def inline_image(kind="png"):
    from PIL import Image
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(output, format={"jpeg": "JPEG"}.get(kind, kind.upper()))
    return {"type": "image_url", "image_url": {"url": f"data:image/{kind};base64," + base64.b64encode(output.getvalue()).decode()}}


@pytest.mark.parametrize("kind,extension", [("png", ".png"), ("jpeg", ".jpg"), ("webp", ".webp"), ("gif", ".gif")])
def test_inline_images_are_isolated_argv_files_not_prompt_paths(transport, kind, extension):
    image = inline_image(kind)
    transport["events"].insert(0, {"type": "message_start", "message": {"role": "user", "content": [
        {"type": "image", "data": "processed-image", "mimeType": "image/" + kind}]}})
    options = {**task().adapter_options, "proposal_images": [image], "required_capabilities": ["images"]}
    result = asyncio.run(r.PiBackend().send_task(task(adapter_options=options)))
    assert result.success and transport["require_images"]
    args, kwargs = transport["launches"][0]
    filename, data = transport["image_files"][0]
    assert Path(filename).parent == Path(kwargs["cwd"])
    assert Path(filename).suffix == extension
    assert data == base64.b64decode(image["image_url"]["url"].split(",", 1)[1])
    assert not Path(filename).exists()
    assert transport["input"].decode() == task().instruction
    assert "@/private/file" not in args
    assert all(flag in args for flag in r._PROPOSAL_FLAGS)


@pytest.mark.parametrize("images", [
    [{"type": "image_url", "image_url": {"url": "https://example.com/screenshot.png"}}],
    [{"type": "image_url", "image_url": {"url": "file:///private/image.png"}}],
    [{"type": "image_url", "image_url": {"url": "data:image/png;base64,!!!!"}}],
    [{"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}],
    [{"path": "/private/image.png"}], None, "@/private/image.png", [{}] * 9,
])
def test_invalid_images_never_launch(transport, images):
    result = asyncio.run(r.PiBackend().send_task(task(adapter_options={**task().adapter_options, "proposal_images": images})))
    assert not result.success and not transport["launches"]


def test_image_byte_and_aggregate_limits(transport, monkeypatch):
    image = inline_image()
    monkeypatch.setattr(r, "_PROPOSAL_MAX_IMAGE_BYTES", 1)
    options = {**task().adapter_options, "proposal_images": [image]}
    assert not asyncio.run(r.PiBackend().send_task(task(adapter_options=options))).success
    monkeypatch.setattr(r, "_PROPOSAL_MAX_IMAGE_BYTES", 4 * 1024 * 1024)
    monkeypatch.setattr(r, "_PROPOSAL_MAX_TOTAL_IMAGE_BYTES", 1)
    assert not asyncio.run(r.PiBackend().send_task(task(adapter_options=options))).success
    assert not transport["launches"]


def test_declared_mime_must_match_pixels(transport):
    image = inline_image()
    image["image_url"]["url"] = image["image_url"]["url"].replace("image/png", "image/jpeg")
    options = {**task().adapter_options, "proposal_images": [image]}
    assert not asyncio.run(r.PiBackend().send_task(task(adapter_options=options))).success
    assert not transport["launches"]


def test_omitted_cli_image_input_cannot_report_success(transport):
    options = {**task().adapter_options, "proposal_images": [inline_image()]}
    result = asyncio.run(r.PiBackend().send_task(task(adapter_options=options)))
    assert not result.success and not result.output
    assert all(not Path(path).exists() for path, _ in transport["image_files"])


def test_excessive_pixel_count_rejected(transport, monkeypatch):
    monkeypatch.setattr(r, "_PROPOSAL_MAX_IMAGE_PIXELS", 63)
    options = {**task().adapter_options, "proposal_images": [inline_image()]}
    assert not asyncio.run(r.PiBackend().send_task(task(adapter_options=options))).success
    assert not transport["launches"]


def test_animated_gif_rejected(transport):
    from PIL import Image
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(output, format="GIF", save_all=True,
        append_images=[Image.new("RGB", (8, 8), "red")])
    image = {"type": "image_url", "image_url": {"url": "data:image/gif;base64," + base64.b64encode(output.getvalue()).decode()}}
    options = {**task().adapter_options, "proposal_images": [image]}
    assert not asyncio.run(r.PiBackend().send_task(task(adapter_options=options))).success
    assert not transport["launches"]


def test_image_preflight_requires_catalog_vision(monkeypatch, tmp_path):
    def run(command, **kwargs):
        return SimpleNamespace(stdout="@files " + " ".join(r._PROPOSAL_FLAGS) if "--help" in command else
                               "provider model context max-out thinking images\nollama exact-model 128K 8K no no\n")
    monkeypatch.setattr(r.subprocess, "run", run)
    with pytest.raises(ValueError, match="image input support"):
        r._proposal_preflight(r._proposal_command("pi", task()), cwd=str(tmp_path), env={}, require_images=True)


@pytest.mark.parametrize("vision,blocked", [("no", False), ("yes", True)])
def test_image_preflight_rejects_before_model_launch(transport, monkeypatch, tmp_path, vision, blocked):
    (tmp_path / "settings.json").write_text(json.dumps({"images": {"blockImages": blocked}}))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    monkeypatch.setattr(r, "_proposal_preflight", transport["real_preflight"])
    def run(command, **kwargs):
        return SimpleNamespace(stdout="@files " + " ".join(r._PROPOSAL_FLAGS) if "--help" in command else
                               f"provider model context max-out thinking images\nollama exact-model 128K 8K no {vision}\n")
    monkeypatch.setattr(r.subprocess, "run", run)
    options = {**task().adapter_options, "proposal_images": [inline_image()]}
    result = asyncio.run(r.PiBackend().send_task(task(adapter_options=options)))
    assert not result.success and not transport["launches"]


def test_installed_pi_local_image_argument_semantics(tmp_path):
    """Runs only installed parser/file-loader modules, not Pi startup or a model."""
    import shutil
    import subprocess
    pi = shutil.which("pi")
    node = shutil.which("node")
    if not pi or not node:
        pytest.skip("Installed Pi/Node required for local CLI semantics check")
    cli_dir = Path(pi).resolve().parent / "cli"
    if not (cli_dir / "file-processor.js").exists():
        pytest.skip("Installed Pi does not expose JS CLI modules")
    files = []
    for kind in ("png", "jpeg", "webp", "gif"):
        path = tmp_path / ("image." + kind)
        path.write_bytes(base64.b64decode(inline_image(kind)["image_url"]["url"].split(",", 1)[1]))
        files.append(str(path))
    program = """
import fs from 'node:fs';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const {parseArgs} = await import(input.argsModule);
const {processFileArguments} = await import(input.filesModule);
const {buildInitialMessage} = await import(input.messageModule);
const {transformMessages} = await import(input.transformModule);
const parsed = parseArgs(input.argv);
const loaded = await processFileArguments(parsed.fileArgs);
const initial = buildInitialMessage({parsed, fileText:loaded.text, fileImages:loaded.images, stdinContent:input.text});
process.stdout.write(JSON.stringify({files:parsed.fileArgs, messages:parsed.messages,
 text:initial.initialMessage, images:initial.initialImages.map(i => ({type:i.type, mimeType:i.mimeType, bytes:i.data.length})),
 downgraded:transformMessages([{role:'user',content:initial.initialImages}], {input:['text']}),
 noTools:parsed.noTools, noExtensions:parsed.noExtensions, noSession:parsed.noSession, noContextFiles:parsed.noContextFiles}));
"""
    payload = {"argsModule": (cli_dir / "args.js").as_uri(), "filesModule": (cli_dir / "file-processor.js").as_uri(),
               "messageModule": (cli_dir / "initial-message.js").as_uri(), "text": task().instruction,
               "transformModule": (cli_dir.parent.parent / "node_modules/@earendil-works/pi-ai/dist/api/transform-messages.js").as_uri(),
               "argv": r._proposal_command(pi, task())[1:] + ["@" + path for path in files]}
    completed = subprocess.run([node, "--input-type=module", "-e", program], input=json.dumps(payload),
                               text=True, capture_output=True, check=True, timeout=20, cwd=tmp_path)
    result = json.loads(completed.stdout)
    assert result["files"] == files and result["messages"] == []
    assert result["text"].startswith(task().instruction)
    assert len(result["images"]) == 4
    assert all(image["type"] == "image" and image["bytes"] > 0 for image in result["images"])
    assert all(block["type"] == "text" for block in result["downgraded"][0]["content"])
    assert "model does not support images" in result["downgraded"][0]["content"][0]["text"]
    assert result["noTools"] and result["noExtensions"] and result["noSession"] and result["noContextFiles"]
