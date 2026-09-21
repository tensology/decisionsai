"""Bounded Codex planning proposals, separate from project execution.

No project checkout, callbacks, work packets, or execution records are passed to
the CLI. The caller applies the returned JSON through Plan's atomic validator.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import re
import shutil
import signal
import tempfile
from datetime import datetime, timezone

from .base import BackendTaskResult


DISABLED_FEATURES = (
    'shell_tool', 'apps', 'plugins', 'hooks', 'browser_use', 'browser_use_external',
    'computer_use', 'multi_agent', 'goals', 'code_mode_host', 'workspace_dependencies',
    'skill_search', 'skill_mcp_dependency_install', 'image_generation', 'view_image',
)
OUTPUT_LIMIT = 4 * 1024 * 1024


def available_models():
    """Use Codex's own recent catalog, not guessed API model names."""
    if not shutil.which('codex'):
        return []
    try:
        home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
        path = home / 'models_cache.json'
        if path.stat().st_size > 2 * 1024 * 1024:
            return []
        cache = json.loads(path.read_text())
        fetched = datetime.fromisoformat(cache['fetched_at'].replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc) - fetched).total_seconds()
        if not 0 <= age <= 86400:
            return []
        return list(dict.fromkeys(model['slug'] for model in cache['models']
            if model.get('visibility') == 'list' and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,100}', model.get('slug', ''))))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []


def failure(code):
    return BackendTaskResult(False, 'codex', 'codex_proposal',
        error='Codex did not return a complete planning proposal.',
        diagnostics={'proposal_error': {'code': code, 'stage': 'codex_proposal'}})


async def _run(args, *, cwd, input_bytes=b'', timeout=180):
    process = await asyncio.create_subprocess_exec(*args, cwd=cwd,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
    async def exchange():
        async def write():
            try:
                process.stdin.write(input_bytes)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()
        async def read():
            chunks, size = [], 0
            while chunk := await process.stdout.read(65536):
                size += len(chunk)
                if size > OUTPUT_LIMIT:
                    raise OverflowError('output_limit')
                chunks.append(chunk)
            return b''.join(chunks).decode('utf-8')
        _, output = await asyncio.gather(write(), read())
        return await process.wait(), output
    try:
        return await asyncio.wait_for(exchange(), timeout)
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()


def parse_output(output):
    completed = False
    messages = []
    for line in output.splitlines():
        event = json.loads(line)
        kind = event.get('type')
        if kind in {'error', 'turn.failed'}:
            raise ValueError('model_error')
        if kind == 'turn.completed':
            completed = True
        if kind in {'item.started', 'item.updated', 'item.completed'}:
            item = event.get('item', {})
            if item.get('type') == 'error':
                # Codex emits these nonfatal startup notices as error items,
                # including when it subsequently completes a successful turn.
                benign = ('Ignoring malformed agent role definition:',
                          'Under-development features enabled: skip_host_skill_discovery.',
                          'Skill descriptions were shortened to fit the skills context budget.')
                if kind == 'item.completed' and str(item.get('message', '')).startswith(benign):
                    continue
                raise ValueError('model_error')
            # Never accept a turn which tried to execute tools, even if its final
            # text looks valid. Read-only sandbox remains the execution boundary.
            if item.get('type') not in {'agent_message', 'reasoning'}:
                raise ValueError('unexpected_tool')
            if kind == 'item.completed' and item.get('type') == 'agent_message':
                messages.append(item.get('text', ''))
    if not completed or len(messages) != 1 or not isinstance(messages[0], str):
        raise ValueError('incomplete_stream')
    proposal = json.loads(messages[0])
    if not isinstance(proposal, dict):
        raise ValueError('invalid_json')
    return messages[0]


async def send_codex_proposal(task, on_event=None):
    executable = shutil.which('codex')
    if not executable or not task.model or task.folder or task.required_capabilities or task.ticket_id or task.workflow_id or task.run_id:
        return failure('route_unavailable')
    options = task.adapter_options
    if not options.get('proposal_only'):
        return failure('route_unavailable')
    images = options.get('proposal_images') or []
    if not isinstance(images, list) or len(images) > 8:
        return failure('images_missing')
    instruction = str(options.get('proposal_system') or '') + '\n\n' + task.instruction
    if len(instruction) > 500_000:
        return failure('output_limit')
    try:
        with tempfile.TemporaryDirectory(prefix='decisions-codex-plan-') as folder:
            # Feature/flag checks fail closed on older installations. They do not
            # change user config or install/upgrade anything.
            code, help_text = await _run([executable, 'exec', '--help'], cwd=folder, timeout=10)
            if code or any(flag not in help_text for flag in ('--ignore-user-config', '--ignore-rules', '--ephemeral', '--sandbox', '--json')):
                return failure('isolation_flags')
            code, feature_text = await _run([executable, 'features', 'list'], cwd=folder, timeout=10)
            known = {line.split()[0] for line in feature_text.splitlines() if line.split()}
            if code or not set(DISABLED_FEATURES + ('skip_host_skill_discovery',)) <= known:
                return failure('isolation_flags')
            args = [executable, 'exec', '--ignore-user-config', '--ignore-rules', '--ephemeral',
                    '--skip-git-repo-check', '--sandbox', 'read-only', '--json', '--color', 'never',
                    '--model', task.model, '-c', 'approval_policy="never"',
                    '-c', 'web_search="disabled"', '-c', 'project_doc_max_bytes=0',
                    '--enable', 'skip_host_skill_discovery']
            for feature in DISABLED_FEATURES:
                args.extend(['--disable', feature])
            for index, block in enumerate(images):
                url = block.get('image_url', {}).get('url', '')
                match = re.fullmatch(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)', url)
                if not match or len(url) > 4 * 1024 * 1024:
                    return failure('images_missing')
                data = base64.b64decode(match[2], validate=True)
                path = Path(folder) / f'image-{index}.{match[1]}'
                path.write_bytes(data)
                args.extend(['--image', str(path)])
            args.append('-')
            code, output = await _run(args, cwd=folder, input_bytes=instruction.encode('utf-8'),
                timeout=min(300, max(1, int(options.get('timeout_seconds', 180)))))
            if code:
                return failure('model_error')
            proposal = parse_output(output)
            return BackendTaskResult(True, 'codex', 'codex_proposal', output=proposal,
                evidence={'proposal_only': True, 'sandbox': 'read-only', 'model': task.model,
                          'global_skill_context': 'may_be_loaded_by_codex'})
    except asyncio.TimeoutError:
        return failure('timeout')
    except OverflowError:
        return failure('output_limit')
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError) as exc:
        code = str(exc) if str(exc) in {'unexpected_tool', 'incomplete_stream', 'model_error'} else 'invalid_json'
        return failure(code)
