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

:: Enable long paths (fixes MAX_PATH 260 char limit for pip/cmake builds)
reg query "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled 2>nul | findstr "0x1" >nul 2>&1
if !errorlevel! neq 0 (
    echo [33mEnabling Windows long path support...[0m
    reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 1 /f >nul 2>&1
    if !errorlevel! equ 0 (
        echo [32m√[0m Long paths enabled
    ) else (
        echo [33mWarning: Could not enable long paths. Run as Administrator if builds fail.[0m
        echo   Or manually: reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 1 /f
    )
) else (
    echo [32m√[0m Long paths already enabled
)

:: Check for Git
where git >nul 2>&1
if !errorlevel! neq 0 (
    echo [33mGit not found. Attempting to install...[0m
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        echo Installing Git via winget...
        winget install Git.Git --accept-package-agreements --accept-source-agreements
        :: Refresh PATH to pick up newly installed git
        set "PATH=%LOCALAPPDATA%\Programs\Git\cmd;%ProgramFiles%\Git\cmd;%PATH%"
        where git >nul 2>&1
        if !errorlevel! equ 0 (
            echo [32m√[0m Git installed successfully
        ) else (
            echo [31mError: Git installation failed. Install manually from https://git-scm.com/download/win[0m
            echo   Or run: winget install Git.Git
            pause
            exit /b 1
        )
    ) else (
        echo [31mError: Git not found and winget not available.[0m
        echo   Install Git from: https://git-scm.com/download/win
        echo   Or install winget from: https://aka.ms/getwinget
        pause
        exit /b 1
    )
) else (
    echo [32m√[0m Git found
)

:: Enable git long paths
git config --global core.longpaths true >nul 2>&1

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

:: Show version
for /f "tokens=*" %%h in ('git rev-parse --short HEAD 2^>nul') do set "GIT_HASH=%%h"
for /f "tokens=*" %%d in ('git log -1 --format^=%%ci 2^>nul') do set "GIT_DATE=%%d"
if defined GIT_HASH (
    echo [36mVersion: !GIT_HASH! ^(!GIT_DATE!^)[0m
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

:: Check for C++ Build Tools (needed for pywhispercpp / whisper.cpp)
echo [33mChecking C++ build tools...[0m

:: Ensure Visual C++ Redistributable is installed (needed for onnxruntime DLLs)
if not exist "%SystemRoot%\System32\msvcp140.dll" (
    echo [33mInstalling Visual C++ Redistributable...[0m
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        winget install Microsoft.VCRedist.2015+.x64 --accept-package-agreements --accept-source-agreements >nul 2>&1
        echo [32m√[0m Visual C++ Redistributable installed
    ) else (
        echo [33mWarning: Install Visual C++ Redistributable from https://aka.ms/vs/17/release/vc_redist.x64.exe[0m
    )
) else (
    echo [32m√[0m Visual C++ Redistributable found
)

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
    echo [33mC++ Build Tools not found. Installing Visual Studio Build Tools...[0m
    echo [33m  ^(Required for local Whisper transcription. ~2-4 GB download^)[0m
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        winget install Microsoft.VisualStudio.2022.BuildTools --accept-package-agreements --accept-source-agreements --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended" 2>nul
        if !errorlevel! equ 0 (
            echo [32m√[0m Visual Studio Build Tools installed
        ) else (
            echo [33mWarning: Could not auto-install Build Tools ^(may need admin privileges^).[0m
            echo [33m  Run as Administrator: winget install Microsoft.VisualStudio.2022.BuildTools --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"[0m
        )
    ) else (
        echo [33mWarning: winget not available. Install Visual Studio Build Tools manually:[0m
        echo [33m  https://visualstudio.microsoft.com/visual-cpp-build-tools/[0m
        echo [33m  Select 'Desktop development with C++' workload.[0m
    )
) else (
    echo [32m√[0m C++ Build Tools found
)

:: Check for CMake (needed for pywhispercpp build)
where cmake >nul 2>&1
if !errorlevel! neq 0 (
    echo [33mCMake not found. Installing...[0m
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        winget install Kitware.CMake --accept-package-agreements --accept-source-agreements >nul 2>&1
        set "PATH=%ProgramFiles%\CMake\bin;%PATH%"
        echo [32m√[0m CMake installed
    ) else (
        echo [33mWarning: CMake not found. Install from https://cmake.org/download/[0m
    )
) else (
    echo [32m√[0m CMake found
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

    :: Use short temp path to avoid MAX_PATH 260 char limit during C++ builds
    if not exist "C:\tmp" mkdir "C:\tmp" 2>nul
    set "TEMP=C:\tmp"
    set "TMP=C:\tmp"

    "%VENV_DIR%\Scripts\pip.exe" install -r requirements.txt
    if !errorlevel! neq 0 (
        :: Check if pywhispercpp was the problem — retry with VS dev environment
        echo [33mRetrying pip install with VS developer environment...[0m
        set "VSDEVCMD="
        if exist "%VSWHERE%" (
            for /f "tokens=*" %%p in ('"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2^>nul') do (
                if exist "%%p\Common7\Tools\VsDevCmd.bat" set "VSDEVCMD=%%p\Common7\Tools\VsDevCmd.bat"
            )
        )
        if defined VSDEVCMD (
            echo [33mUsing VS developer environment: !VSDEVCMD![0m
            cmd /c "call "!VSDEVCMD!" -arch=amd64 >nul 2>&1 && "%VENV_DIR%\Scripts\pip.exe" install -r requirements.txt"
            if !errorlevel! neq 0 (
                :: Final fallback: skip pywhispercpp
                echo [33mRetrying without pywhispercpp...[0m
                "%VENV_DIR%\Scripts\python.exe" -c "lines=open('requirements.txt').readlines(); f=open('requirements_win.txt','w'); [f.write(l) for l in lines if 'pywhispercpp' not in l]; f.close()"
                "%VENV_DIR%\Scripts\pip.exe" install -r requirements_win.txt
                if !errorlevel! neq 0 (
                    echo [31mError: pip install failed. Please check the output above.[0m
                    exit /b 1
                )
                del /f requirements_win.txt 2>nul
                echo [33mNote: pywhispercpp skipped. Local Whisper transcription unavailable.[0m
                echo [33m  Install VS Build Tools with C++ workload, then re-run this script.[0m
            )
        ) else (
            :: No VS dev env — skip pywhispercpp
            echo [33mNo VS developer environment found. Retrying without pywhispercpp...[0m
            "%VENV_DIR%\Scripts\python.exe" -c "lines=open('requirements.txt').readlines(); f=open('requirements_win.txt','w'); [f.write(l) for l in lines if 'pywhispercpp' not in l]; f.close()"
            "%VENV_DIR%\Scripts\pip.exe" install -r requirements_win.txt
            if !errorlevel! neq 0 (
                echo [31mError: pip install failed. Please check the output above.[0m
                exit /b 1
            )
            del /f requirements_win.txt 2>nul
            echo [33mNote: pywhispercpp skipped. Install VS Build Tools with C++ workload to enable.[0m
        )
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
    echo [33mOllama not found. Installing...[0m
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        winget install Ollama.Ollama --accept-package-agreements --accept-source-agreements >nul 2>&1
        if !errorlevel! equ 0 (
            echo [32m√[0m Ollama installed. It will be available after restarting your terminal.
            echo [33m  Then run: ollama pull llama3.1:8b[0m
        ) else (
            echo [33mCould not auto-install Ollama. Download from https://ollama.com/download[0m
        )
    ) else (
        echo [33mNote: Install Ollama from https://ollama.com/download for local LLM support[0m
        echo   Then run: ollama pull llama3.1:8b
    )
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

:: On Windows, prefer onnxruntime-directml (avoids DLL issues with standard onnxruntime)
:: Must remove regular onnxruntime even if directml is present (pip may have reinstalled it)
"%VENV_DIR%\Scripts\pip.exe" show onnxruntime >nul 2>&1
if !errorlevel! equ 0 (
    "%VENV_DIR%\Scripts\pip.exe" show onnxruntime-directml >nul 2>&1
    if !errorlevel! equ 0 (
        echo [33mRemoving conflicting regular onnxruntime...[0m
        "%VENV_DIR%\Scripts\pip.exe" uninstall onnxruntime -y --quiet 2>nul
    ) else (
        echo [33mSwitching to onnxruntime-directml...[0m
        "%VENV_DIR%\Scripts\pip.exe" uninstall onnxruntime -y --quiet 2>nul
        "%VENV_DIR%\Scripts\pip.exe" install onnxruntime-directml --quiet 2>nul
    )
)
"%VENV_DIR%\Scripts\pip.exe" show onnxruntime-directml >nul 2>&1
if !errorlevel! neq 0 (
    echo [33mInstalling onnxruntime-directml...[0m
    "%VENV_DIR%\Scripts\pip.exe" install onnxruntime-directml --quiet 2>nul
)
echo [32m√[0m onnxruntime-directml OK

:: Clean Python cache again (clear any cached onnxruntime imports)
for /d /r "%VENV_DIR%" %%d in (__pycache__) do (
    if exist "%%d" rmdir /s /q "%%d" 2>nul
)

:: Run the application
echo.
echo [32mStarting DecisionsAI...[0m
echo ================================
"%VENV_DIR%\Scripts\python.exe" bin\start.py

endlocal
