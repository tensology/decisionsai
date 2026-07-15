import subprocess
import sys


def test_agent_worker_import_does_not_load_gui_main_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import distr.app.agent_worker; "
                "raise SystemExit(1 if 'distr.app.main' in sys.modules else 0)"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
