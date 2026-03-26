# DecisionsAI Setup & Run (Windows PowerShell)
# Equivalent of bin/decisions.sh for macOS/Linux

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Push-Location $ScriptDir

function Write-Status($msg) { Write-Host "√ $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host $msg -ForegroundColor Yellow }
function Write-Err($msg) { Write-Host $msg -ForegroundColor Red }

Write-Host "`nDecisionsAI Setup & Run" -ForegroundColor Green
Write-Host "================================"

# --- Git update check ---
Write-Warn "Checking for updates..."
if (Test-Path ".git") {
    try {
        git fetch origin 2>$null | Out-Null
        $branch = git rev-parse --abbrev-ref HEAD 2>$null
        $local = git rev-parse "@" 2>$null
        $remote = git rev-parse "@{u}" 2>$null
        if ($local -ne $remote) {
            $dirty = git diff-index --quiet HEAD -- 2>$null; $clean = $LASTEXITCODE -eq 0
            if ($clean) {
                Write-Warn "Updates available. Pulling..."
                git pull origin $branch 2>$null | Out-Null
                if ($LASTEXITCODE -eq 0) { Write-Status "Repository updated" }
                else { Write-Warn "Could not pull updates. Continuing..." }
            } else { Write-Warn "Local changes detected. Skipping auto-update." }
        } else { Write-Status "Repository is up to date" }
    } catch { Write-Warn "Could not check for updates" }
} else { Write-Warn "Not a git repository. Skipping update check." }
Write-Host ""

# --- Find Python 3.12 ---
$PythonCmd = $null

# Try py launcher
if (Get-Command py -ErrorAction SilentlyContinue) {
    $ver = py -3.12 --version 2>$null
    if ($ver) { $PythonCmd = "py -3.12" }
}

# Try python3.12
if (-not $PythonCmd -and (Get-Command python3.12 -ErrorAction SilentlyContinue)) {
    $PythonCmd = "python3.12"
}

# Try python and check version
if (-not $PythonCmd -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $ver = python --version 2>$null
    if ($ver -match "Python 3\.12") { $PythonCmd = "python" }
}

if (-not $PythonCmd) {
    Write-Warn "Python 3.12 not found. Attempting to install..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        # Refresh PATH
        $env:PATH = "$env:LOCALAPPDATA\Programs\Python\Python312;$env:LOCALAPPDATA\Programs\Python\Python312\Scripts;$env:PATH"
        if (Get-Command py -ErrorAction SilentlyContinue) { $PythonCmd = "py -3.12" }
        elseif (Get-Command python -ErrorAction SilentlyContinue) { $PythonCmd = "python" }
    }
    if (-not $PythonCmd) {
        Write-Err "Error: Python 3.12 not found. Download from https://www.python.org/downloads/"
        Pop-Location; exit 1
    }
}

$pyVer = & $PythonCmd.Split()[0] $PythonCmd.Split()[1..9] --version 2>$null
Write-Status "Python found: $pyVer"

# Helper to run python commands (handles "py -3.12" as two args)
function Invoke-Python {
    param([string[]]$Args)
    $parts = $PythonCmd.Split()
    & $parts[0] ($parts[1..($parts.Length)] + $Args)
}

function Invoke-Pip {
    param([string[]]$Args)
    & "$VenvDir\Scripts\pip.exe" @Args
}

# --- System dependencies ---
Write-Warn "Checking system dependencies..."
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Warn "ffmpeg not found. Attempting to install..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements 2>$null
        Write-Status "ffmpeg installed via winget"
    } elseif (Get-Command choco -ErrorAction SilentlyContinue) {
        choco install ffmpeg -y 2>$null
        Write-Status "ffmpeg installed via chocolatey"
    } else {
        Write-Warn "ffmpeg not found. Install from https://ffmpeg.org/download.html"
    }
} else { Write-Status "ffmpeg found" }

# --- Virtual environment ---
$VenvDir = "$env:USERPROFILE\.virtualenvs\decisions"

if (Test-Path "$VenvDir\Scripts\python.exe") {
    $venvVer = & "$VenvDir\Scripts\python.exe" --version 2>$null
    if ($venvVer -notmatch "3\.12") {
        Write-Warn "Existing venv uses wrong Python version, recreating..."
        Remove-Item -Recurse -Force $VenvDir -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Path (Split-Path $VenvDir) -Force | Out-Null
        Invoke-Python @("-m", "venv", $VenvDir)
        Write-Status "Virtual environment recreated at $VenvDir"
    } else {
        Write-Status "Using existing virtual environment at $VenvDir"
    }
} else {
    Write-Warn "Creating virtual environment at $VenvDir..."
    New-Item -ItemType Directory -Path (Split-Path $VenvDir) -Force | Out-Null
    Invoke-Python @("-m", "venv", $VenvDir)
    Write-Status "Virtual environment created at $VenvDir"
}

# Activate
& "$VenvDir\Scripts\Activate.ps1"
Write-Status "Virtual environment activated"

# --- Install requirements ---
$reqMarker = "installer\.requirements_installed_external"
$depsOk = $false

if (Test-Path $reqMarker) {
    & "$VenvDir\Scripts\python.exe" "$ScriptDir\bin\check_deps.py" 2>$null
    if ($LASTEXITCODE -eq 0) { $depsOk = $true }
}

if (-not $depsOk) {
    if (-not (Test-Path $reqMarker)) { Write-Warn "Installing dependencies..." }
    else {
        Write-Warn "Dependencies incomplete. Reinstalling..."
        Remove-Item $reqMarker -Force -ErrorAction SilentlyContinue
    }

    # Install main requirements (skip pywhispercpp — it needs C++ build tools on Windows)
    # Create a filtered requirements file without pywhispercpp
    $filteredReqs = "$env:TEMP\requirements_win.txt"
    Get-Content "requirements.txt" | Where-Object { $_ -notmatch "pywhispercpp" } | Set-Content $filteredReqs

    Invoke-Pip @("install", "-r", $filteredReqs)
    if ($LASTEXITCODE -ne 0) {
        Write-Err "pip install failed. Check the output above."
        Pop-Location; exit 1
    }

    # Try to install pywhispercpp separately (optional — needs Visual Studio Build Tools)
    Write-Warn "Attempting to install pywhispercpp (optional, needs C++ build tools)..."
    Invoke-Pip @("install", "pywhispercpp@git+https://github.com/absadiki/pywhispercpp")
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "pywhispercpp could not be installed (requires Visual Studio Build Tools with C++ workload)."
        Write-Warn "Local Whisper transcription will be unavailable. Other STT backends (AssemblyAI, OpenAI) still work."
        Write-Warn "To install later: Install Visual Studio Build Tools, then run:"
        Write-Warn "  pip install pywhispercpp@git+https://github.com/absadiki/pywhispercpp"
    } else {
        Write-Status "pywhispercpp installed"
    }

    New-Item -ItemType Directory -Path "installer" -Force | Out-Null
    "" | Out-File $reqMarker
    Write-Status "Dependencies installed"
} else {
    Write-Status "Dependencies already installed"
}

# --- Models ---
$modelsDir = ".\distr\core\agent\models"
if (-not (Test-Path "$modelsDir\kokoro-v1.0.onnx") -or -not (Test-Path "$modelsDir\voices-v1.0.bin")) {
    Write-Warn "Models not found. Running setup..."
    & "$VenvDir\Scripts\python.exe" bin\setup.py
    Write-Status "Setup complete"
} else { Write-Status "Models already installed" }

# Pre-download Qwen3-TTS into distr\core\agent\models\qwen3-tts\ if not present
$qwen3Local = Join-Path $ScriptDir "distr\core\agent\models\qwen3-tts\config.json"
if (-not (Test-Path $qwen3Local)) {
    Write-Warn "Qwen3-TTS model not found locally. Downloading..."
    & "$VenvDir\Scripts\python.exe" bin\setup.py --setup-qwen3-only
} else { Write-Status "Qwen3-TTS model already present" }

# --- Ollama ---
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    Write-Status "Ollama found"
    $ollamaList = ollama list 2>$null
    if ($ollamaList -notmatch "llama3\.1:8b") {
        Write-Warn "Pulling llama3.1:8b model..."
        ollama pull llama3.1:8b
    } else { Write-Status "Ollama model llama3.1:8b available" }
} else {
    Write-Warn "Ollama not found. Install from https://ollama.com/download"
    Write-Host "  Then run: ollama pull llama3.1:8b"
}

# --- Clean cache ---
Write-Warn "Cleaning Python cache..."
Get-ChildItem -Path $ScriptDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path $ScriptDir -Recurse -Filter "*.pyc" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue
Write-Status "Cache cleaned"

# --- Run ---
Write-Host ""
Write-Host "Starting DecisionsAI..." -ForegroundColor Green
Write-Host "================================"
& "$VenvDir\Scripts\python.exe" bin\start.py

Pop-Location
