@echo off
setlocal enabledelayedexpansion

:: Cross-platform setup and run script for DecisionsAI (Windows)
:: Equivalent of bin/decisions.sh for macOS/Linux

:: Check if DecisionsAI is already running
tasklist /FI "IMAGENAME eq pythonw.exe" /FO CSV 2>nul | findstr /i "pythonw" >nul 2>&1
if !errorlevel! equ 0 (
    echo DecisionsAI is already running.
    echo To restart, right-click the oracle and choose Quit first.
    timeout /t 3 >nul
    exit /b 0
)

:: Get the project root directory (parent of bin\)
set "SCRIPT_DIR=%~dp0.."
pushd "%SCRIPT_DIR%"
set "SCRIPT_DIR=%CD%"
set "LEAN_SETUP=0"
set "PROFILE_ARGS="
if not exist "installer\.requirements_installed_external" set "LEAN_SETUP=1"
if exist "installer\.requirements_installed_profile" set "LEAN_SETUP=1"
for %%a in (%*) do if /I "%%~a"=="--setup-providers" (
    set "LEAN_SETUP=1"
    set "PROFILE_ARGS=--configure"
)
:: Stay in the project root — all relative paths (requirements.txt, bin\, etc.) depend on this

echo.
echo DecisionsAI Setup ^& Run
echo ================================

:: Enable long paths (fixes MAX_PATH 260 char limit for pip/cmake builds)
reg query "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled 2>nul | findstr "0x1" >nul 2>&1
if !errorlevel! neq 0 (
    echo Enabling Windows long path support...
    reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 1 /f >nul 2>&1
    if !errorlevel! equ 0 (
        echo Long paths enabled
    ) else (
        echo Warning: Could not enable long paths. Run as Administrator if builds fail.
        echo   Or manually: reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 1 /f
    )
) else (
    echo Long paths already enabled
)

:: Check for Git
where git >nul 2>&1
if !errorlevel! neq 0 (
    echo Git not found. Attempting to install...
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        echo Installing Git via winget...
        winget install Git.Git --accept-package-agreements --accept-source-agreements
        :: Refresh PATH to pick up newly installed git
        set "PATH=%LOCALAPPDATA%\Programs\Git\cmd;%ProgramFiles%\Git\cmd;%PATH%"
        where git >nul 2>&1
        if !errorlevel! equ 0 (
            echo Git installed successfully
        ) else (
            echo Error: Git installation failed. Install manually from https://git-scm.com/download/win
            echo   Or run: winget install Git.Git
            pause
            exit /b 1
        )
    ) else (
        echo Error: Git not found and winget not available.
        echo   Install Git from: https://git-scm.com/download/win
        echo   Or install winget from: https://aka.ms/getwinget
        pause
        exit /b 1
    )
) else (
    echo Git found
)

:: Enable git long paths
git config --global core.longpaths true >nul 2>&1

:: Check for repository updates
echo Checking for updates...
if exist ".git" (
    git fetch origin >nul 2>&1
    if !errorlevel! equ 0 (
        for /f "tokens=*" %%a in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "CURRENT_BRANCH=%%a"
        for /f "tokens=*" %%a in ('git rev-parse @ 2^>nul') do set "LOCAL=%%a"
        for /f "tokens=*" %%a in ('git rev-parse @{u} 2^>nul') do set "REMOTE=%%a"
        if not "!LOCAL!"=="!REMOTE!" (
            git diff-index --quiet HEAD -- >nul 2>&1
            if !errorlevel! equ 0 (
                echo Updates available. Pulling latest changes...
                git pull origin !CURRENT_BRANCH! >nul 2>&1
                if !errorlevel! equ 0 (
                    echo Repository updated successfully
                ) else (
                    echo Warning: Could not pull updates. Continuing with current version...
                )
            ) else (
                echo Warning: Local changes detected. Skipping auto-update.
            )
        ) else (
            echo Repository is up to date
        )
    ) else (
        echo Warning: Could not check for updates
    )
) else (
    echo Not a git repository. Skipping update check.
)

:: Show version
for /f "tokens=*" %%h in ('git rev-parse --short HEAD 2^>nul') do set "GIT_HASH=%%h"
for /f "tokens=*" %%d in ('git log -1 --format^=%%ci 2^>nul') do set "GIT_DATE=%%d"
if defined GIT_HASH (
    echo Version: !GIT_HASH! ^(!GIT_DATE!^)
)
echo.

:: Require python 3.12
set "PYTHON_CMD="

:: Try py launcher first (most reliable on Windows)
where py >nul 2>&1
if !errorlevel! equ 0 (
    for /f "tokens=*" %%v in ('py -3.12 --version 2^>nul') do set "PY_VER=%%v"
    if defined PY_VER (
        set "PYTHON_CMD=py -3.12"
        goto :python_found
    )
)

:: Try python3.12 directly
where python3.12 >nul 2>&1
if !errorlevel! equ 0 (
    set "PYTHON_CMD=python3.12"
    goto :python_found
)

:: Try python and check version
where python >nul 2>&1
if !errorlevel! equ 0 (
    for /f "tokens=2 delims= " %%v in ('python --version 2^>nul') do set "PY_FULL_VER=%%v"
    if defined PY_FULL_VER (
        for /f "tokens=1,2 delims=." %%a in ("!PY_FULL_VER!") do (
            if "%%a.%%b"=="3.12" (
                set "PYTHON_CMD=python"
                goto :python_found
            )
        )
    )
)

:: Python 3.12 not found — try to install via winget
echo Python 3.12 not found. Attempting to install...
where winget >nul 2>&1
if !errorlevel! equ 0 (
    echo Installing Python 3.12 via winget...
    winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    :: Refresh PATH
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
    where py >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYTHON_CMD=py -3.12"
        goto :python_found
    )
    where python >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYTHON_CMD=python"
        goto :python_found
    )
)

echo Error: Python 3.12 not found and could not be installed automatically.
echo   Download from: https://www.python.org/downloads/release/python-3120/
echo   Or install via winget: winget install Python.Python.3.12
echo   Make sure to check "Add Python to PATH" during installation.
exit /b 1

:python_found
for /f "tokens=*" %%v in ('!PYTHON_CMD! --version 2^>nul') do echo Python found: %%v

:: Check system dependencies
echo Checking system dependencies...
where ffmpeg >nul 2>&1
if !errorlevel! neq 0 (
    echo ffmpeg not found. Attempting to install...
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements >nul 2>&1
        echo ffmpeg installed via winget
    ) else (
        where choco >nul 2>&1
        if !errorlevel! equ 0 (
            choco install ffmpeg -y >nul 2>&1
            echo ffmpeg installed via chocolatey
        ) else (
            echo Warning: ffmpeg not found. Install manually from https://ffmpeg.org/download.html
            echo Or install via: winget install Gyan.FFmpeg
        )
    )
) else (
    echo ffmpeg found
)

:: Check for C++ Build Tools (needed for pywhispercpp / whisper.cpp)
echo Checking C++ build tools...

:: Ensure Visual C++ Redistributable is installed (needed for onnxruntime DLLs)
if not exist "%SystemRoot%\System32\msvcp140.dll" (
    echo Installing Visual C++ Redistributable...
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        winget install Microsoft.VCRedist.2015+.x64 --accept-package-agreements --accept-source-agreements >nul 2>&1
        echo Visual C++ Redistributable installed
    ) else (
        echo Warning: Install Visual C++ Redistributable from https://aka.ms/vs/17/release/vc_redist.x64.exe
    )
) else (
    echo Visual C++ Redistributable found
)

if "!LEAN_SETUP!"=="1" goto :virtual_environment

set "HAS_CPP_TOOLS=0"
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if exist "%VSWHERE%" (
    for /f "tokens=*" %%p in ('"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2^>nul') do (
        if not "%%p"=="" set "HAS_CPP_TOOLS=1"
    )
)
if !HAS_CPP_TOOLS! equ 0 (
    where cl >nul 2>&1
    if !errorlevel! equ 0 set "HAS_CPP_TOOLS=1"
)
if !HAS_CPP_TOOLS! equ 0 (
    echo C++ Build Tools not found. Installing Visual Studio Build Tools...
    echo   ^(Required for local Whisper transcription. ~2-4 GB download^)
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        winget install Microsoft.VisualStudio.2022.BuildTools --accept-package-agreements --accept-source-agreements --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended" 2>nul
        if !errorlevel! equ 0 (
            echo Visual Studio Build Tools installed
        ) else (
            echo Warning: Could not auto-install Build Tools ^(may need admin privileges^).
            echo   Run as Administrator: winget install Microsoft.VisualStudio.2022.BuildTools --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
        )
    ) else (
        echo Warning: winget not available. Install Visual Studio Build Tools manually:
        echo   https://visualstudio.microsoft.com/visual-cpp-build-tools/
        echo   Select 'Desktop development with C++' workload.
    )
) else (
    echo C++ Build Tools found
)

:: Check for CMake (needed for pywhispercpp build)
where cmake >nul 2>&1
if !errorlevel! neq 0 (
    echo CMake not found. Installing...
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        winget install Kitware.CMake --accept-package-agreements --accept-source-agreements >nul 2>&1
        set "PATH=%ProgramFiles%\CMake\bin;%PATH%"
        echo CMake installed
    ) else (
        echo Warning: CMake not found. Install from https://cmake.org/download/
    )
) else (
    echo CMake found
)

:virtual_environment
:: Virtual environment — always use %USERPROFILE%\.virtualenvs\decisions
set "VENV_DIR=%USERPROFILE%\.virtualenvs\decisions"

if exist "%VENV_DIR%\Scripts\python.exe" (
    :: Verify existing venv uses python 3.12
    for /f "tokens=2 delims= " %%v in ('"%VENV_DIR%\Scripts\python.exe" --version 2^>nul') do set "VENV_PY_VER=%%v"
    if defined VENV_PY_VER (
        for /f "tokens=1,2 delims=." %%a in ("!VENV_PY_VER!") do set "VENV_MAJOR_MINOR=%%a.%%b"
    )
    if not "!VENV_MAJOR_MINOR!"=="3.12" (
        echo Existing venv uses Python !VENV_MAJOR_MINOR!, recreating with 3.12...
        rmdir /s /q "%VENV_DIR%" 2>nul
        if not exist "%USERPROFILE%\.virtualenvs" mkdir "%USERPROFILE%\.virtualenvs"
        !PYTHON_CMD! -m venv "%VENV_DIR%"
        echo Virtual environment recreated at %VENV_DIR%
    ) else (
        echo Using existing virtual environment at %VENV_DIR%
    )
) else (
    echo Creating virtual environment at %VENV_DIR%...
    if not exist "%USERPROFILE%\.virtualenvs" mkdir "%USERPROFILE%\.virtualenvs"
    !PYTHON_CMD! -m venv "%VENV_DIR%"
    echo Virtual environment created at %VENV_DIR%
)

:: Activate virtual environment
if exist "%VENV_DIR%\Scripts\activate.bat" (
    call "%VENV_DIR%\Scripts\activate.bat"
    echo Virtual environment activated
) else (
    echo Error: Could not activate virtual environment at %VENV_DIR%
    exit /b 1
)

if "!LEAN_SETUP!"=="1" (
    "%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%\bin\first_run.py" !PROFILE_ARGS!
    if errorlevel 1 exit /b 1
    goto :qt_runtime_check
)

:: Check pip and upgrade to latest
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip --quiet 2>nul
for /f "tokens=*" %%v in ('"%VENV_DIR%\Scripts\pip.exe" --version 2^>nul') do echo pip: %%v

:: Install requirements if needed
set "REQUIREMENTS_MARKER=installer\.requirements_installed_external"

:: Check dependencies using check_deps.py
set "DEPS_OK=0"
if exist "%REQUIREMENTS_MARKER%" (
    "%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%\bin\check_deps.py" >nul 2>&1
    if !errorlevel! equ 0 set "DEPS_OK=1"
)

if !DEPS_OK! equ 0 (
    if not exist "%REQUIREMENTS_MARKER%" (
        echo Installing dependencies...
    ) else (
        echo Dependencies appear incomplete. Reinstalling...
        del /f "%REQUIREMENTS_MARKER%" 2>nul
    )

    :: Use short temp path to avoid MAX_PATH 260 char limit during C++ builds
    if not exist "C:\tmp" mkdir "C:\tmp" 2>nul
    set "TEMP=C:\tmp"
    set "TMP=C:\tmp"

    "%VENV_DIR%\Scripts\pip.exe" install --no-cache-dir -r requirements.txt
    if !errorlevel! neq 0 (
        :: Check if pywhispercpp was the problem — retry with VS dev environment
        echo Retrying pip install with VS developer environment...
        set "VSDEVCMD="
        if exist "%VSWHERE%" (
            for /f "tokens=*" %%p in ('"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2^>nul') do (
                if exist "%%p\Common7\Tools\VsDevCmd.bat" set "VSDEVCMD=%%p\Common7\Tools\VsDevCmd.bat"
            )
        )
        if defined VSDEVCMD (
            echo Using VS developer environment: !VSDEVCMD!
            :: Write to temp file to avoid (x86) in VSDEVCMD path breaking cmd /c quoting
            echo call "!VSDEVCMD!" -arch=amd64 ^>nul 2^>^&1 ^&^& "%VENV_DIR%\Scripts\pip.exe" install --no-cache-dir -r requirements.txt > "%TEMP%\vsdev_pip.bat"
            cmd /c "%TEMP%\vsdev_pip.bat"
            if !errorlevel! neq 0 (
                :: Final fallback: skip pywhispercpp
                echo Retrying without pywhispercpp...
                "%VENV_DIR%\Scripts\python.exe" -c "lines=open('requirements.txt').readlines(); f=open('requirements_win.txt','w'); [f.write(l) for l in lines if 'pywhispercpp' not in l]; f.close()"
                "%VENV_DIR%\Scripts\pip.exe" install --no-cache-dir -r requirements_win.txt
                if !errorlevel! neq 0 (
                    echo Error: pip install failed. Clearing pip cache and retrying...
                    "%VENV_DIR%\Scripts\pip.exe" cache purge 2>nul
                    "%VENV_DIR%\Scripts\pip.exe" install --no-cache-dir -r requirements_win.txt
                    if !errorlevel! neq 0 (
                        echo Error: pip install failed. Please check the output above.
                        exit /b 1
                    )
                )
                del /f requirements_win.txt 2>nul
                echo Note: pywhispercpp skipped. Local Whisper transcription unavailable.
                echo   Install VS Build Tools with C++ workload, then re-run this script.
            )
        ) else (
            :: No VS dev env — skip pywhispercpp
            echo No VS developer environment found. Retrying without pywhispercpp...
            "%VENV_DIR%\Scripts\python.exe" -c "lines=open('requirements.txt').readlines(); f=open('requirements_win.txt','w'); [f.write(l) for l in lines if 'pywhispercpp' not in l]; f.close()"
            "%VENV_DIR%\Scripts\pip.exe" install --no-cache-dir -r requirements_win.txt
            if !errorlevel! neq 0 (
                echo Error: pip install failed. Clearing pip cache and retrying...
                "%VENV_DIR%\Scripts\pip.exe" cache purge 2>nul
                "%VENV_DIR%\Scripts\pip.exe" install --no-cache-dir -r requirements_win.txt
                if !errorlevel! neq 0 (
                    echo Error: pip install failed. Please check the output above.
                    exit /b 1
                )
            )
            del /f requirements_win.txt 2>nul
            echo Note: pywhispercpp skipped. Install VS Build Tools with C++ workload to enable.
        )
    )

    :: Ensure installer directory exists and create marker
    if not exist "installer" mkdir installer
    echo.> "%REQUIREMENTS_MARKER%"
    echo Dependencies installed
) else (
    echo Dependencies already installed
)

:qt_runtime_check
:: Fix PyQt6 bundled msvcp140.dll version conflict
:: PyQt6 ships an old msvcp140.dll (14.26.x) that crashes on modern Windows.
:: Replace it with the system copy if the system version is newer.
set "PYQT6_DLL=%VENV_DIR%\Lib\site-packages\PyQt6\Qt6\bin\msvcp140.dll"
set "SYSTEM_DLL=%SystemRoot%\System32\msvcp140.dll"
if exist "%PYQT6_DLL%" (
    if exist "%SYSTEM_DLL%" (
        echo Checking PyQt6 msvcp140.dll...
        set "NEEDS_UPDATE=0"
        for /f "tokens=*" %%v in ('powershell -NoProfile -Command "(Get-Item '%SYSTEM_DLL%').VersionInfo.FileVersionRaw -gt (Get-Item '%PYQT6_DLL%').VersionInfo.FileVersionRaw" 2^>nul') do (
            if /i "%%v"=="True" set "NEEDS_UPDATE=1"
        )
        if "!NEEDS_UPDATE!"=="1" (
            echo Fixing PyQt6 bundled msvcp140.dll ^(old version causes crash^)...
            copy /y "%SYSTEM_DLL%" "%PYQT6_DLL%" >nul 2>&1
            echo PyQt6 msvcp140.dll updated to system version
        ) else (
            echo PyQt6 msvcp140.dll OK
        )
    )
)

if "!LEAN_SETUP!"=="1" goto :launch_setup

:: Check if models are installed
set "MODELS_DIR=.\distr\core\agent\models"
set "MODELS_EXIST=1"
if not exist "%MODELS_DIR%\kokoro-v1.0.onnx" set "MODELS_EXIST=0"
if not exist "%MODELS_DIR%\voices-v1.0.bin" set "MODELS_EXIST=0"

if !MODELS_EXIST! equ 0 (
    echo Models not found. Running setup...
    "%VENV_DIR%\Scripts\python.exe" bin\setup.py
    echo Setup complete
) else (
    echo Models already installed
)

:: Check for Ollama and ensure RAM-appropriate models are pulled
where ollama >nul 2>&1
if !errorlevel! equ 0 (
    echo Ollama found
    echo Ensuring Ollama models are available...
    "%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%\bin\ensure_models.py"
) else (
    echo Ollama not found. Installing...
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        winget install Ollama.Ollama --accept-package-agreements --accept-source-agreements >nul 2>&1
        if !errorlevel! equ 0 (
            echo Ollama installed. Restart your terminal, then re-run this script.
        ) else (
            echo Could not auto-install Ollama. Download from https://ollama.com/download
        )
    ) else (
        echo Note: Install Ollama from https://ollama.com/download for local LLM support
    )
)

:: Check NumPy/PyTorch compatibility
"%VENV_DIR%\Scripts\python.exe" -c "import numpy; v=numpy.__version__; exit(0 if v.startswith('2.') else 1)" >nul 2>&1
if !errorlevel! equ 0 (
    for /f "tokens=*" %%v in ('"%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%\bin\get_torch_ver.py" 2^>nul') do set "TORCH_VER=%%v"
    if defined TORCH_VER (
        for /f "tokens=1,2 delims=." %%a in ("!TORCH_VER!") do (
            set /a "TORCH_MAJOR=%%a"
            set /a "TORCH_MINOR=%%b"
        )
        if !TORCH_MAJOR! lss 2 (
            echo Upgrading PyTorch for NumPy 2.x compatibility...
            echo "%VENV_DIR%\Scripts\pip.exe" install "torch>=2.5.0" "torchaudio>=2.5.0" --quiet > "%TEMP%\upgrade_torch.bat"
            call "%TEMP%\upgrade_torch.bat"
        ) else if !TORCH_MAJOR! equ 2 if !TORCH_MINOR! lss 5 (
            echo Upgrading PyTorch for NumPy 2.x compatibility...
            echo "%VENV_DIR%\Scripts\pip.exe" install "torch>=2.5.0" "torchaudio>=2.5.0" --quiet > "%TEMP%\upgrade_torch.bat"
            call "%TEMP%\upgrade_torch.bat"
        )
    )
)

:: Verify PyTorch loads correctly
echo Verifying PyTorch...
for /f "tokens=*" %%r in ('"%VENV_DIR%\Scripts\python.exe" -c "try: import torch; print('ok ' + torch.__version__)" ^& "except: print('error')" 2^>nul') do set "TORCH_CHECK=%%r"
if "!TORCH_CHECK:~0,2!"=="ok" (
    echo PyTorch !TORCH_CHECK:~3! loaded successfully
) else (
    echo PyTorch could not load. F5-TTS and sentence-transformers may be unavailable.
    echo    Tool retrieval will use TF-IDF fallback.
)

:launch_setup
:: Clean Python cache (project files only — skip venv to avoid paths with parentheses breaking for /d /r)
echo Cleaning Python cache...
del /s /q "%SCRIPT_DIR%\*.pyc" >nul 2>&1
echo Cache cleaned

:: Verify onnxruntime native extension loads (needed for Pipecat/Kokoro TTS)
echo Verifying onnxruntime...
call "%SCRIPT_DIR%\bin\check_ort.bat" "%VENV_DIR%"
if !errorlevel! neq 0 exit /b 1

:: Set up Windows launcher PATH, Start Menu shortcuts, desktop shortcut, and app icon
echo Checking Windows launcher integration...
"%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%\bin\setup_shortcuts.py"

:: Run the application
echo.
echo Starting DecisionsAI...
echo ================================

:: ── Start sidecar (machine control agent) ────────────────────────────────────
set "SIDECAR_BIN=%SCRIPT_DIR%\sidecar\dist\decisionsai-sidecar.exe"
set "SIDECAR_PID="

if not exist "!SIDECAR_BIN!" (
    where go >nul 2>&1
    if !errorlevel! equ 0 (
        if exist "%SCRIPT_DIR%\sidecar" (
            echo Building sidecar...
            if not exist "%SCRIPT_DIR%\sidecar\dist" mkdir "%SCRIPT_DIR%\sidecar\dist"
            pushd "%SCRIPT_DIR%\sidecar"
            go mod tidy >nul 2>&1
            go build -ldflags="-s -w" -o dist\decisionsai-sidecar.exe . >nul 2>&1
            popd
            if exist "!SIDECAR_BIN!" (
                echo Sidecar built
            ) else (
                echo Sidecar build failed — accessibility tree tools unavailable
            )
        )
    )
)

if exist "!SIDECAR_BIN!" (
    if not exist "%USERPROFILE%\.decisions\logs" mkdir "%USERPROFILE%\.decisions\logs"
    :: Kill any stale sidecar from a previous run before starting a new one
    taskkill /f /im decisionsai-sidecar.exe >nul 2>&1
    start /b "" "!SIDECAR_BIN!" --local > "%USERPROFILE%\.decisions\logs\sidecar.log" 2>&1
    :: Capture PID so we can kill it when the app exits
    for /f "tokens=2" %%p in ('tasklist /fi "imagename eq decisionsai-sidecar.exe" /fo list 2^>nul ^| findstr /i "PID:"') do set "SIDECAR_PID=%%p"
    echo Sidecar started ^(HTTP port: 11435^)
)

"%VENV_DIR%\Scripts\pythonw.exe" bin\start.py
set "APP_EXIT_CODE=!errorlevel!"

:: App has exited — shut down the sidecar
if defined SIDECAR_PID (
    taskkill /pid !SIDECAR_PID! /f >nul 2>&1
) else if exist "!SIDECAR_BIN!" (
    taskkill /f /im decisionsai-sidecar.exe >nul 2>&1
)
exit /b !APP_EXIT_CODE!
