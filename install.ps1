#Requires -Version 5.1
<#
.SYNOPSIS
    Installs obsidian-tools on Windows: Python venv, dependencies, .env, Task Scheduler services.
.DESCRIPTION
    Run from the project root: .\install.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Constants ────────────────────────────────────────────────────────────────

$MinMinor = 11
$MaxMinor = 13
$PreferredVersion = "3.12"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

# ── Helpers ──────────────────────────────────────────────────────────────────

function Write-Info  { param($msg) Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Warn  { param($msg) Write-Host "==> $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "==> $msg" -ForegroundColor Red }

function Test-PythonVersion {
    param([string]$PythonPath)
    try {
        $output = & $PythonPath --version 2>&1
        if ($output -match "Python (\d+)\.(\d+)\.(\d+)") {
            $minor = [int]$Matches[2]
            return ($minor -ge $MinMinor -and $minor -le $MaxMinor)
        }
    } catch {}
    return $false
}

function Find-CompatiblePython {
    # Try the py launcher first (most reliable on Windows)
    foreach ($minor in @(12, 13, 11)) {
        try {
            $pyPath = (Get-Command "py" -ErrorAction Stop).Source
            $testOutput = & py "-3.$minor" --version 2>&1
            if ($LASTEXITCODE -eq 0) {
                # py launcher found a matching version; get the real path
                $realPath = & py "-3.$minor" -c "import sys; print(sys.executable)" 2>&1
                if ($LASTEXITCODE -eq 0 -and (Test-Path $realPath)) {
                    return $realPath.Trim()
                }
            }
        } catch {}
    }

    # Fall back to searching PATH
    foreach ($cmd in @("python3", "python")) {
        try {
            $path = (Get-Command $cmd -ErrorAction Stop).Source
            if (Test-PythonVersion $path) { return $path }
        } catch {}
    }

    return $null
}

function Read-Prompt {
    param(
        [string]$Label,
        [string]$Default = "",
        [bool]$Required = $false
    )

    while ($true) {
        if ($Default) {
            $input = Read-Host "==> $Label [$Default]"
        } else {
            $input = Read-Host "==> $Label"
        }

        if (-not $input -and $Default) { $input = $Default }

        if (-not $input -and $Required) {
            Write-Warn "This value is required."
            continue
        }
        return $input
    }
}

function Read-VaultPath {
    while ($true) {
        $vaultPath = Read-Host "==> Path to your Obsidian vault"
        if (-not $vaultPath) {
            Write-Warn "This value is required."
        } elseif (-not (Test-Path $vaultPath -PathType Container)) {
            Write-Warn "Directory not found: $vaultPath"
        } else {
            return $vaultPath
        }
    }
}

function Set-EnvValue {
    param([string]$Key, [string]$Value, [string]$EnvFile)

    $content = Get-Content $EnvFile -Raw
    $pattern = "(?m)^${Key}=.*$"
    if ($content -match $pattern) {
        $content = $content -replace $pattern, "${Key}=${Value}"
    } else {
        $content = $content.TrimEnd() + "`n${Key}=${Value}`n"
    }
    Set-Content -Path $EnvFile -Value $content -NoNewline
}

# ── Service installation ────────────────────────────────────────────────────

function Install-ScheduledTasks {
    param(
        [string]$VenvPython,
        [string]$IntervalMin
    )

    Write-Info "Installing Task Scheduler tasks..."

    $templateDir = Join-Path $ProjectDir "services\taskscheduler"
    $tasks = @(
        @{ Name = "ObsidianToolsAPI";     File = "obsidian-tools-api.xml" },
        @{ Name = "ObsidianToolsIndexer"; File = "obsidian-tools-indexer.xml" }
    )

    foreach ($task in $tasks) {
        $templatePath = Join-Path $templateDir $task.File
        $xml = Get-Content $templatePath -Raw

        $xml = $xml -replace "__VENV_PYTHON__", $VenvPython
        $xml = $xml -replace "__PROJECT_DIR__", $ProjectDir
        $xml = $xml -replace "__INDEX_INTERVAL__", $IntervalMin

        # Write to temp file for registration
        $tempFile = Join-Path $env:TEMP $task.File
        Set-Content -Path $tempFile -Value $xml -Encoding Unicode

        # Unregister if exists
        try { Unregister-ScheduledTask -TaskName $task.Name -Confirm:$false -ErrorAction Stop } catch {}

        Register-ScheduledTask -TaskName $task.Name -Xml $xml | Out-Null
        Remove-Item $tempFile -ErrorAction SilentlyContinue

        Write-Host "  Registered: $($task.Name)"
    }
}

# ── Main ────────────────────────────────────────────────────────────────────

function Main {
    Write-Host ""
    Write-Host "  Obsidian Tools Installer"
    Write-Host "  ========================"
    Write-Host ""

    Write-Info "Detected OS: Windows"

    # ── Step 1: Find Python ──────────────────────────────────────────────

    Write-Info "Looking for a compatible Python (3.${MinMinor}-3.${MaxMinor})..."
    $pythonPath = Find-CompatiblePython

    if (-not $pythonPath) {
        Write-Err "No compatible Python (3.${MinMinor}-3.${MaxMinor}) found."
        Write-Host ""
        Write-Host "  Download Python $PreferredVersion from: https://www.python.org/downloads/"
        Write-Host "  Make sure to check 'Add Python to PATH' during installation."
        Write-Host ""
        exit 1
    }

    $pyVersion = & $pythonPath --version 2>&1
    Write-Info "Found: $pythonPath ($pyVersion)"

    # ── Step 2: Create virtual environment ───────────────────────────────

    $venvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"

    if (Test-Path $venvPython) {
        if (Test-PythonVersion $venvPython) {
            $venvVersion = & $venvPython --version 2>&1
            Write-Info "Existing venv is compatible ($venvVersion), keeping it."
        } else {
            Write-Warn "Existing venv uses an incompatible Python, recreating..."
            Remove-Item (Join-Path $ProjectDir ".venv") -Recurse -Force
            & $pythonPath -m venv (Join-Path $ProjectDir ".venv")
            Write-Info "Created virtual environment."
        }
    } else {
        & $pythonPath -m venv (Join-Path $ProjectDir ".venv")
        Write-Info "Created virtual environment."
    }

    # ── Step 3: Install dependencies ─────────────────────────────────────

    $venvPip = Join-Path $ProjectDir ".venv\Scripts\pip.exe"

    Write-Info "Installing Python dependencies..."
    & $venvPip install --upgrade pip --quiet
    & $venvPip install -r (Join-Path $ProjectDir "requirements.txt") --quiet
    Write-Info "Dependencies installed."

    # ── Step 4: Configure .env ───────────────────────────────────────────

    $envFile = Join-Path $ProjectDir ".env"
    $envExample = Join-Path $ProjectDir ".env.example"

    if (-not (Test-Path $envFile)) {
        Copy-Item $envExample $envFile
        Write-Info "Created .env from .env.example"
    } else {
        Write-Info "Existing .env found, updating values."
    }

    $promptFile = Join-Path $ProjectDir "system_prompt.txt"
    $promptExample = Join-Path $ProjectDir "system_prompt.txt.example"

    if (-not (Test-Path $promptFile)) {
        Copy-Item $promptExample $promptFile
        Write-Info "Created system_prompt.txt from system_prompt.txt.example"
        Write-Warn "Edit system_prompt.txt to match your vault's folder structure and frontmatter conventions."
    }

    Write-Host ""
    Write-Info "Configure your environment:"
    Write-Host ""

    $apiKey         = Read-Prompt "Fireworks API key" -Required $true
    $fireworksModel = Read-Prompt "Fireworks model (default: OpenAI gpt-oss-120b)" -Default "accounts/fireworks/models/gpt-oss-120b"
    $vaultPath      = Read-VaultPath
    $chromaPath     = Read-Prompt "ChromaDB path" -Default "./.chroma_db"
    $apiPort        = Read-Prompt "API server port" -Default "8000"
    $indexInterval  = Read-Prompt "Index interval (minutes)" -Default "60"

    Set-EnvValue "FIREWORKS_API_KEY" $apiKey $envFile
    Set-EnvValue "FIREWORKS_MODEL" $fireworksModel $envFile
    Set-EnvValue "VAULT_PATH" $vaultPath $envFile
    Set-EnvValue "CHROMA_PATH" $chromaPath $envFile
    Set-EnvValue "API_PORT" $apiPort $envFile
    Set-EnvValue "INDEX_INTERVAL" $indexInterval $envFile

    Write-Info ".env configured."

    # ── Step 5: Install services ─────────────────────────────────────────

    Write-Host ""
    $installSvc = Read-Host "==> Install background services (API server + vault indexer)? [Y/n]"
    if (-not $installSvc -or $installSvc -match "^[Yy]") {
        Install-ScheduledTasks -VenvPython $venvPython -IntervalMin $indexInterval
    } else {
        Write-Info "Skipped service installation."
    }

    # ── Step 6: Initial index ────────────────────────────────────────────

    Write-Host ""
    $runIndex = Read-Host "==> Run the vault indexer now? (recommended for first install) [Y/n]"
    if (-not $runIndex -or $runIndex -match "^[Yy]") {
        Write-Info "Indexing vault (this may take a minute)..."
        & $venvPython (Join-Path $ProjectDir "src\index_vault.py")
        Write-Info "Indexing complete."
    }

    # ── Summary ──────────────────────────────────────────────────────────

    Write-Host ""
    Write-Host "  ========================"
    Write-Host "  Installation complete!"
    Write-Host "  ========================"
    Write-Host ""
    Write-Host "  Project:     $ProjectDir"
    Write-Host "  Python:      $pythonPath"
    Write-Host "  Venv:        $ProjectDir\.venv\"
    Write-Host "  Vault:       $vaultPath"
    Write-Host "  API port:    $apiPort"
    Write-Host "  Index every: $indexInterval min"
    Write-Host ""

    if (-not $installSvc -or $installSvc -match "^[Yy]") {
        Write-Host "  Services (Task Scheduler):"
        Write-Host "    View:    Get-ScheduledTask | Where-Object TaskName -like 'ObsidianTools*'"
        Write-Host "    Start:   Start-ScheduledTask -TaskName ObsidianToolsAPI"
        Write-Host "    Stop:    Stop-ScheduledTask -TaskName ObsidianToolsAPI"
        Write-Host "    Remove:  .\uninstall.ps1"
    }

    Write-Host ""
    Write-Host "  Uninstall:   .\uninstall.ps1"
    Write-Host ""
}

Main
