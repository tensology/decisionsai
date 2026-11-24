@echo off
setlocal enabledelayedexpansion
set "VENV_DIR=%~1"

"%VENV_DIR%\Scripts\python.exe" -c "from onnxruntime.capi import onnxruntime_pybind11_state" >nul 2>&1
if !errorlevel! neq 0 (
    echo onnxruntime DLL issue detected. Reinstalling...
    "%VENV_DIR%\Scripts\pip.exe" install --force-reinstall --no-cache-dir onnxruntime --quiet
    "%VENV_DIR%\Scripts\python.exe" -c "from onnxruntime.capi import onnxruntime_pybind11_state" >nul 2>&1
    if !errorlevel! neq 0 (
        echo Standard onnxruntime still failing. Trying onnxruntime-directml...
        "%VENV_DIR%\Scripts\pip.exe" uninstall onnxruntime -y --quiet 2>nul
        "%VENV_DIR%\Scripts\pip.exe" install --no-cache-dir onnxruntime-directml --quiet
        "%VENV_DIR%\Scripts\python.exe" -c "from onnxruntime.capi import onnxruntime_pybind11_state" >nul 2>&1
        if !errorlevel! neq 0 (
            echo.
            echo ========================================
            echo   FATAL: onnxruntime cannot load.
            echo ========================================
            echo.
            echo   The onnxruntime native DLL failed to initialize.
            echo   This is required for speech ^(Kokoro TTS^) and voice detection ^(Silero VAD^).
            echo.
            echo   Try these fixes:
            echo     1. Install Visual C++ Redistributable:
            echo        https://aka.ms/vs/17/release/vc_redist.x64.exe
            echo     2. Reboot after installing
            echo     3. Re-run this script
            echo.
            echo   If it still fails, open an issue at:
            echo     https://github.com/tensology/decisionsai/issues
            echo.
            pause
            exit /b 1
        ) else (
            echo onnxruntime-directml OK
        )
    ) else (
        echo onnxruntime reinstalled OK
    )
) else (
    echo onnxruntime OK
)
exit /b 0
