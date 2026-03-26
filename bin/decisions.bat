@echo off
setlocal enabledelayedexpansion

:: Cross-platform setup and run script for DecisionsAI (Windows)
:: Equivalent of bin/decisions.sh for macOS/Linux

:: Get the project root directory (parent of bin\)
set "SCRIPT_DIR=%~dp0.."
pushd "%SCRIPT_DIR%"
set "SCRIPT_DIR=%CD%"
popd

echo.
echo [32mDecisionsAI Setup ^& Run[0m
echo ================================

:: Check for repository updates
echo [33mChecking for updates...[0m
if exist ".git" (
    git fetch origin >nul 2>&1
    if !errorlevel! equ 0 (
        for /f "tokens=*" %%a in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "CURRENT_BRANCH=%%a"
        for /f "tokens=*" %%a in ('git rev-parse @ 2^>nul') do set "LOCAL=%%a"
        for /f "tokens=*" %%a in ('git rev-parse @{u} 2^>nul') do set "REMOTE=%%a"
        if not "!LOCAL!"=="!REMOTE!" (
            git diff-index --quiet HEAD -- >nul 2>&1
            if !errorlevel! equ 0 (
                echo [33mUpdates available. Pulling latest changes...[0m
                git pull origin !CURRENT_BRANCH! >nul 2>&1
                if !errorlevel! equ 0 (
                    echo [32m√[0m Repository updated successfully
                ) else (
                    echo [33mWarning: Could not pull updates. Continuing with current version...[0m
                )
            ) else (
                echo [33mWarning: Local changes detected. Skipping auto-update.[0m
            )
        ) else (
            echo [32m√[0m Repository is up to date
        )
    ) else (
        echo [33mWarning: Could not check for updates[0m
    )
) else (
    echo [33mNot a git repository. Skipping update check.[0m
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
echo [33mPython 3.12 not found. Attempting to install...[0m
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

echo [31mError: Python 3.12 not found and could not be installed automatically.[0m
echo   Download from: https://www.python.org/downloads/release/python-3120/
echo   Or install via winget: winget install Python.Python.3.12
echo   Make sure to check "Add Python to PATH" during installation.
exit /b 1

:python_found
for /f "tokens=*" %%v in ('!PYTHON_CMD! --version 2^>nul') do echo [32m√[0m Python found: %%v

:: Check system dependencies
echo [33mChecking system dependencies...[0m
where ffmpeg >nul 2>&1
if !errorlevel! neq 0 (
    echo [33mffmpeg not found. Attempting to install...[0m
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements >nul 2>&1
        echo [32m√[0m ffmpeg installed via winget
    ) else (
        where choco >nul 2>&1
        if !errorlevel! equ 0 (
            choco install ffmpeg -y >nul 2>&1
            echo [32m√[0m ffmpeg installed via chocolatey
        ) else (
            echo [33mWarning: ffmpeg not found. Install manually from https://ffmpeg.org/download.html[0m
            echo [33mOr install via: winget install Gyan.FFmpeg[0m
        )
    )
) else (
    echo [32m√[0m ffmpeg found
)

:: Virtual environment — always use %USERPROFILE%\.virtualenvs\decisions
set "VENV_DIR=%USERPROFILE%\.virtualenvs\decisions"

if exist "%VENV_DIR%\Scripts\python.exe" (
    :: Verify existing venv uses python 3.12
    for /f "tokens=2 delims= " %%v in ('"%VENV_DIR%\Scripts\python.exe" --version 2^>nul') do set "VENV_PY_VER=%%v"
    if defined VENV_PY_VER (
        for /f "tokens=1,2 delims=." %%a in ("!VENV_PY_VER!") do set "VENV_MAJOR_MINOR=%%a.%%b"
    )
    if not "!VENV_MAJOR_MINOR!"=="3.12" (
        echo [33mExisting venv uses Python !VENV_MAJOR_MINOR!, recreating with 3.12...[0m
        rmdir /s /q "%VENV_DIR%" 2>nul
        if not exist "%USERPROFILE%\.virtualenvs" mkdir "%USERPROFILE%\.virtualenvs"
        !PYTHON_CMD! -m venv "%VENV_DIR%"
        echo [32m√[0m Virtual environment recreated at %VENV_DIR%
    ) else (
        echo [32m√[0m Using existing virtual environment at %VENV_DIR%
    )
) else (
    echo [33mCreating virtual environment at %VENV_DIR%...[0m
    if not exist "%USERPROFILE%\.virtualenvs" mkdir "%USERPROFILE%\.virtualenvs"
    !PYTHON_CMD! -m venv "%VENV_DIR%"
    echo [32m√[0m Virtual environment created at %VENV_DIR%
)

:: Activate virtual environment
if exist "%VENV_DIR%\Scripts\activate.bat" (
    call "%VENV_DIR%\Scripts\activate.bat"
    echo [32m√[0m Virtual environment activated
) else (
    echo [31mError: Could not activate virtual environment at %VENV_DIR%[0m
    exit /b 1
)

:: Check pip
for /f "tokens=*" %%v in ('"%VENV_DIR%\Scripts\pip.exe" --version 2^>nul') do echo [32m√[0m pip: %%v

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
        echo [33mInstalling dependencies...[0m
    ) else (
        echo [33mDependencies appear incomplete. Reinstalling...[0m
        del /f "%REQUIREMENTS_MARKER%" 2>nul
    )

    "%VENV_DIR%\Scripts\pip.exe" install -r requirements.txt
    if !errorlevel! neq 0 (
        echo [31mError: pip install failed. Please check the output above.[0m
        exit /b 1
    )

    :: Ensure installer directory exists and create marker
    if not exist "installer" mkdir installer
    echo.> "%REQUIREMENTS_MARKER%"
    echo [32m√[0m Dependencies installed
) else (
    echo [32m√[0m Dependencies already installed
)

:: Check if models are installed
set "MODELS_DIR=.\distr\core\agent\models"
set "MODELS_EXIST=1"
if not exist "%MODELS_DIR%\kokoro-v1.0.onnx" set "MODELS_EXIST=0"
if not exist "%MODELS_DIR%\voices-v1.0.bin" set "MODELS_EXIST=0"

if !MODELS_EXIST! equ 0 (
    echo [33mModels not found. Running setup...[0m
    "%VENV_DIR%\Scripts\python.exe" bin\setup.py
    echo [32m√[0m Setup complete
) else (
    echo [32m√[0m Models already installed
)

:: Pre-download Qwen3-TTS model into distr\core\agent\models\qwen3-tts\ if not present
if not exist "%SCRIPT_DIR%\distr\core\agent\models\qwen3-tts\config.json" (
    echo [33mQwen3-TTS model not found locally. Downloading...[0m
    "%VENV_DIR%\Scripts\python.exe" bin\setup.py --setup-qwen3-only
) else (
    echo [32m√[0m Qwen3-TTS model already present
)

:: Check for Ollama
where ollama >nul 2>&1
if !errorlevel! equ 0 (
    echo [32m√[0m Ollama found
    ollama list 2>nul | findstr /i "llama3.1:8b" >nul 2>&1
    if !errorlevel! neq 0 (
        echo [33mOllama model llama3.1:8b not found. Pulling...[0m
        ollama pull llama3.1:8b
    ) else (
        echo [32m√[0m Ollama model llama3.1:8b is available
    )
) else (
    echo [33mNote: Ollama not found. For local LLM support, install from https://ollama.com/download[0m
    echo   Then run: ollama pull llama3.1:8b
)

:: Check NumPy/PyTorch compatibility
"%VENV_DIR%\Scripts\python.exe" -c "import numpy; v=numpy.__version__; exit(0 if v.startswith('2.') else 1)" >nul 2>&1
if !errorlevel! equ 0 (
    for /f "tokens=*" %%v in ('"%VENV_DIR%\Scripts\python.exe" -c "import torch; v=torch.__version__.split('+')[0].split('.'); print(v[0]+'.'+v[1])" 2^>nul') do set "TORCH_VER=%%v"
    if defined TORCH_VER (
        for /f "tokens=1,2 delims=." %%a in ("!TORCH_VER!") do (
            set /a "TORCH_MAJOR=%%a"
            set /a "TORCH_MINOR=%%b"
        )
        if !TORCH_MAJOR! lss 2 (
            echo [33mUpgrading PyTorch for NumPy 2.x compatibility...[0m
            "%VENV_DIR%\Scripts\pip.exe" install "torch>=2.5.0" "torchaudio>=2.5.0" --quiet
        ) else if !TORCH_MAJOR! equ 2 if !TORCH_MINOR! lss 5 (
            echo [33mUpgrading PyTorch for NumPy 2.x compatibility...[0m
            "%VENV_DIR%\Scripts\pip.exe" install "torch>=2.5.0" "torchaudio>=2.5.0" --quiet
        )
    )
)

:: Add project root to user PATH (so 'decisions.bat' works from anywhere)
echo [33mChecking system PATH...[0m
echo %PATH% | findstr /i /c:"%SCRIPT_DIR%" >nul 2>&1
if !errorlevel! neq 0 (
    echo [33mAdding DecisionsAI to user PATH...[0m
    for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USER_PATH=%%b"
    if not defined USER_PATH set "USER_PATH="
    setx PATH "!USER_PATH!;%SCRIPT_DIR%" >nul 2>&1
    if !errorlevel! equ 0 (
        echo [32m√[0m Added to PATH. Restart your terminal to use 'decisions.bat' from anywhere.
    ) else (
        echo [33mWarning: Could not update PATH automatically.[0m
    )
) else (
    echo [32m√[0m Already on PATH
)

:: Clean Python cache
echo [33mCleaning Python cache...[0m
for /d /r "%SCRIPT_DIR%" %%d in (__pycache__) do (
    if exist "%%d" rmdir /s /q "%%d" 2>nul
)
del /s /q "%SCRIPT_DIR%\*.pyc" >nul 2>&1
echo [32m√[0m Cache cleaned

:: Run the application
echo.
echo [32mStarting DecisionsAI...[0m
echo ================================
"%VENV_DIR%\Scripts\python.exe" bin\start.py

endlocal
