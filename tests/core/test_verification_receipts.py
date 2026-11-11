import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from distr.core.turn_runtime import TurnEvent, TurnEventKind, TurnStatus
from distr.core.turn_runtime.verification import VerificationReceipts, check_keys, tool_exit_code


def observe(receipts, command, code, **details):
    receipts.observe(TurnEvent(kind=TurnEventKind.TOOL_COMPLETED, status=TurnStatus.UPDATING,
        summary='Finished', runtime_id='fixture', tool_name='bash',
        details={'command': command, 'exit_code': code, **details}))


def test_font_error_cannot_be_hidden_by_successful_tail():
    receipts = VerificationReceipts('Run the browser tests')
    observe(receipts, 'cd frontend && npm run smoke:schedule 2>&1 | tail -14', 0)
    assert receipts.report()['status'] == 'unknown'
    observe(receipts, 'npm run smoke:schedule', 0)
    assert receipts.report()['status'] == 'passed'
    assert len(receipts.report()['checks']) == 1


def test_failed_check_must_be_rerun_not_cleared_by_unrelated_success():
    receipts = VerificationReceipts('Run tests, lint and the production build')
    observe(receipts, 'npm test', 1)
    observe(receipts, 'npm run lint', 0)
    assert receipts.report()['status'] == 'failed'
    observe(receipts, 'npm test', 0)
    assert receipts.report()['status'] == 'incomplete'
    assert receipts.report()['missing'] == ['build']
    observe(receipts, 'npm run build', 0)
    assert receipts.report()['status'] == 'passed'


def test_reading_or_echoing_a_success_report_is_not_verification():
    for command in ['echo "pytest passed"', 'cat build.log', 'grep "npm test" log.txt', 'node -e "console.log(\'pytest\')"']:
        assert check_keys(command) == []
    assert VerificationReceipts().report()['status'] == 'not_run'
    assert VerificationReceipts('Fix billing\n\nRetained proposal conversation evidence: old browser tests and build').required == []


def test_grouped_checks_and_virtualenv_commands_have_distinct_receipts():
    assert check_keys('cd frontend && npm run lint && npm run build') == ['npm run lint', 'npm run build']
    assert check_keys('TOKEN=private /home/user/.virtualenvs/project/bin/python -m pytest tests/test_a.py') == ['python -m pytest tests/test_a.py']
    assert tool_exit_code({'structuredContent': {'exit_code': 7}}) == 7
    assert tool_exit_code({'details': {'exitCode': 3}}) == 3
    assert check_keys('set -e\nnpm test\nnpm run build') == ['npm test', 'npm run build']


@pytest.mark.parametrize('command', ['npm test || true', 'npm test; true'])
def test_explicit_failure_mask_is_not_passing_evidence(command):
    receipts = VerificationReceipts('Run tests')
    observe(receipts, command, 0, pipefail=True)
    assert receipts.report()['status'] == 'unknown'


@pytest.mark.parametrize('command,code', [
    ('printf regression-failure; false | tail -1', 1),
    ('false | tail -1; printf should-not-run', 1),
    ('printf success | tail -1', 0),
])
def test_real_pi_sdk_shell_preserves_pipeline_failure(tmp_path, command, code):
    pi = shutil.which('pi'); bun = shutil.which('bun')
    if not pi or not bun:
        pytest.skip('Installed Pi SDK and Bun required for extension integration')
    package = Path(pi).resolve().parents[2]
    extension = Path(__file__).parents[2] / 'distr/core/project_cli_backends/pi_extensions/checked_bash.ts'
    script = '''
import extension from EXTENSION;
let tool;
extension({registerTool: value => tool = value});
const result = await tool.execute('regression', {command: COMMAND}, new AbortController().signal);
console.log(JSON.stringify({error: result.isError || false, code: result.structuredContent.exit_code,
    output: result.structuredContent.output, pipefail: result.details.pipefail}));
'''.replace('EXTENSION', json.dumps(str(extension))).replace('COMMAND', json.dumps(command))
    result = subprocess.run([bun, '-e', script], cwd=tmp_path, capture_output=True, text=True, timeout=30,
        env={**os.environ, 'NODE_PATH': str(package.parent.parent)})
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['code'] == code and receipt['error'] == bool(code)
    assert receipt['pipefail'] is True
    assert 'should-not-run' not in receipt['output']
