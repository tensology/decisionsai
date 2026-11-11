"""Verification receipts come from completed tools, never the model's claims."""
from __future__ import annotations

import re
import shlex
from pathlib import PurePath


_CHECK = re.compile(
    r"\b(?:pytest\b|(?:npm|pnpm|yarn)\s+(?:test\b|run\s+(?:test|check|smoke|lint|typecheck|build)[\w:.-]*)"
    r"|(?:python[\d.]*\s+(?:-m\s+pytest|manage\.py\s+(?:test|check|makemigrations)))"
    r"|node\s+[^\s;|&]*(?:check|test|smoke)[^\s;|&]*|(?:ruff\s+check|mypy|pyright|tsc)\b)"
)


def check_keys(command: str) -> list[str]:
    # Inspect executed argv, not quoted text in echo/grep/cat or a model report.
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>\n')
        lexer.whitespace = ' \t\r'
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token and all(char in ';&|\n' for char in token):
            segments.append([])
        else:
            segments[-1].append(token)
    keys = []
    for args in segments:
        while args:
            if re.match(r'^[A-Za-z_][A-Za-z_0-9]*=', args[0]) or args[0] == 'env':
                args = args[1:]
            elif args[0] == 'timeout' and len(args) > 2 and re.fullmatch(r'[\d.]+[smhd]?', args[1]):
                args = args[2:]
            elif args[:2] == ['rtk', 'proxy']:
                args = args[2:]
            else:
                break
        if not args:
            continue
        args[0] = PurePath(args[0]).name
        if not _CHECK.match(' '.join(args)):
            continue
        # Redirection and output filters do not identify a different check.
        end = next((i for i, arg in enumerate(args) if any(c in arg for c in '<>')), len(args))
        key = shlex.join(args[:end]).removesuffix(' 2')
        if key not in keys:
            keys.append(key)
    return keys


def tool_exit_code(result: dict) -> int | None:
    for value in (result, result.get('structuredContent'), result.get('structured_content'), result.get('details')):
        if isinstance(value, dict):
            for key in ('exit_code', 'exitCode', 'code'):
                if type(value.get(key)) is int:
                    return value[key]
    return None


class VerificationReceipts:
    def __init__(self, instruction: str = ''):
        self.commands: dict[str, str] = {}
        self.checks: dict[str, dict] = {}
        text = instruction.split('\n\nRetained proposal conversation evidence', 1)[0].lower()
        self.required = [name for name, pattern in (
            ('tests', r'\b(?:tests?|pytest|regressions?)\b'),
            ('build', r'\b(?:production build|build check|run\b[^.\n]*\bbuild)\b'), ('lint', r'\blint\b'),
            ('typecheck', r'\b(?:typecheck|typescript checks?|type checks?)\b'),
            ('browser', r'\b(?:browser|playwright|e2e)\b'),
        ) if re.search(pattern, text)]

    @staticmethod
    def categories(key: str) -> set[str]:
        return {name for name, pattern in (
            ('tests', r'pytest|(?:\btest|:test)|smoke|playwright'),
            ('build', r'build'), ('lint', r'lint|ruff'),
            ('typecheck', r'typecheck|tsc|mypy|pyright'),
            ('browser', r'smoke|playwright|e2e|browser'),
        ) if re.search(pattern, key)}

    def observe(self, event) -> None:
        details = event.details
        call_id = str(details.get('call_id') or '')
        command = str(details.get('command') or '')
        if command:
            self.commands[call_id] = command
        if event.kind.value != 'tool_completed':
            return
        command = command or self.commands.get(call_id, '')
        keys = check_keys(command)
        if not keys:
            return
        code = details.get('exit_code')
        status = 'passed' if type(code) is int and code == 0 else 'failed' if (
            type(code) is int or details.get('status') == 'error'
        ) else 'unknown'
        if status == 'passed' and '|' in command and not (
            details.get('pipefail') is True or re.search(r'\bset\s+(?:-e\s+)?-o\s+pipefail\b', command)
        ):
            status = 'unknown'
        if status == 'passed' and ('||' in command or (';' in command and not details.get('errexit'))):
            status = 'unknown'
        # Store only the check identity, excluding environment assignments.
        for key in keys:
            self.checks[key] = {'check': key, 'status': status, 'exit_code': code}

    def report(self) -> dict:
        rows = list(self.checks.values())
        covered = set().union(*(self.categories(r['check']) for r in rows if r['status'] == 'passed'))
        missing = [name for name in self.required if name not in covered]
        status = 'not_run' if not rows else 'failed' if any(r['status'] == 'failed' for r in rows) else (
            'unknown' if any(r['status'] == 'unknown' for r in rows) else 'passed'
        )
        if missing and status == 'passed':
            status = 'incomplete'
        return {'status': status, 'checks': rows, 'required': self.required, 'missing': missing}
