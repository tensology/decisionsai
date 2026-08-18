"""The Dock app must remain DecisionsAI in System Settings, not Homebrew Python."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STUB_SRC = ROOT / "installer" / "macos" / "decisions_python_stub.c"


def test_stub_source_stays_inside_the_app_bundle():
    src = STUB_SRC.read_text(encoding="utf-8")
    assert "_NSGetExecutablePath" in src
    assert "PyConfig_SetBytesString" in src
    assert "addsitedir" in src
    assert "Py_RunMain" in src


def test_dock_launcher_does_not_require_python_in_process_name():
    run = (ROOT / "bin" / "decisions-run.sh").read_text(encoding="utf-8")
    launcher = (ROOT / "bin" / "dock-app-launcher.sh").read_text(encoding="utf-8")
    plist = (ROOT / "installer" / "decisions-app-template" / "Contents" / "Info.plist").read_text(
        encoding="utf-8"
    )
    assert "decisions-python" not in run
    assert "com.tensology.decisionsai" in plist
    assert "[Pp]ython.*" not in launcher
    assert "bin/start.py" in launcher


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS TCC identity")
@pytest.mark.skipif(shutil.which("cc") is None, reason="no C compiler")
def test_stub_executable_path_is_not_python_app(tmp_path):
    include = sysconfig.get_path("include")
    libdir = sysconfig.get_config_var("LIBDIR") or ""
    header = Path(include) / "Python.h"
    if not header.is_file() or not libdir:
        pytest.skip("CPython headers/libs not available")
    out = tmp_path / "decisions-python"
    soname = f"python{sys.version_info.major}.{sys.version_info.minor}"
    subprocess.run(
        [
            "cc",
            "-o",
            str(out),
            f"-I{include}",
            str(STUB_SRC),
            f"-L{libdir}",
            f"-l{soname}",
            "-ldl",
            "-framework",
            "CoreFoundation",
        ],
        check=True,
    )
    env = os.environ.copy()
    env["DECISIONS_PYTHON"] = sys.executable
    probe = (
        "import sys, ctypes; from ctypes import c_uint32, c_char_p, POINTER, create_string_buffer; "
        "libc=ctypes.CDLL(None); NSGet=libc._NSGetExecutablePath; "
        "NSGet.argtypes=[c_char_p, POINTER(c_uint32)]; buf=create_string_buffer(1024); n=c_uint32(1024); "
        "NSGet(buf, ctypes.byref(n)); print(sys.executable); print(buf.value.decode())"
    )
    result = subprocess.run(
        [str(out), "-c", probe],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) >= 2
    assert "Python.app" not in lines[0]
    assert "Python.app" not in lines[1]
    assert "decisions-python" in Path(lines[0]).name
