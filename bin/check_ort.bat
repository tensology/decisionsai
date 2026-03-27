@echo off
setlocal enabledelayedexpansion
set "VENV_DIR=%~1"

"%VENV_DIR%\Scripts\python.exe" -c "from onnxruntime.capi import onnxruntime_pybind11_state" >nul 2>&1
if !errorlevel! neq 0 (
    echo [33monnxruntime DLL issue detected. Reinstalling...[0m
    "%VENV_DIR%\Scripts\pip.exe" install --force-reinstall --no-cache-dir onnxruntime --quiet
    "%VENV_DIR%\Scripts\python.exe" -c "from onnxruntime.capi import onnxruntime_pybind11_state" >nul 2>&1
    if !errorlevel! neq 0 (
        echo [33mStandard onnxruntime still failing. Trying onnxruntime-directml...[0m
        "%VENV_DIR%\Scripts\pip.exe" uninstall onnxruntime -y --quiet 2>nul
        "%VENV_DIR%\Scripts\pip.exe" install --no-cache-dir onnxruntime-directml --quiet
        "%VENV_DIR%\Scripts\python.exe" -c "from onnxruntime.capi import onnxruntime_pybind11_state" >nul 2>&1
        if !errorlevel! neq 0 (
            echo.
            echo [31m========================================[0m
            echo [31m  FATAL: onnxruntime cannot load.[0m
            echo [31m========================================[0m
            echo.
            echo [33m  The onnxruntime native DLL failed to initialize.[0m
            echo [33m  This is required for speech ^(Kokoro TTS^) and voice detection ^(Silero VAD^).[0m
            echo.
            echo [33m  Try these fixes:[0m
            echo [33m    1. Install Visual C++ Redistributable:[0m
            echo [33m       https://aka.ms/vs/17/release/vc_redist.x64.exe[0m
            echo [33m    2. Reboot after installing[0m
            echo [33m    3. Re-run this script[0m
            echo.
            echo [33m  If it still fails, open an issue at:[0m
            echo [33m    https://github.com/tensology/decisionsai/issues[0m
            echo.
            pause
            exit /b 1
        ) else (
            echo [32m√[0m onnxruntime-directml OK
        )
    ) else (
        echo [32m√[0m onnxruntime reinstalled OK
    )
) else (
    echo [32m√[0m onnxruntime OK
)
exit /b 0
