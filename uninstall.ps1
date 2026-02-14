#Requires -Version 5.1
<#
.SYNOPSIS
    Uninstalls obsidian-tools services and optionally the virtual environment.
.DESCRIPTION
    Run from the project root: .\uninstall.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

function Write-Info { param($msg) Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Warn { param($msg) Write-Host "==> $msg" -ForegroundColor Yellow }

function Remove-ObsidianTasks {
    Write-Info "Removing Task Scheduler tasks..."
    $removed = 0

    foreach ($name in @("ObsidianToolsAPI", "ObsidianToolsIndexer")) {
        try {
            $task = Get-ScheduledTask -TaskName $name -ErrorAction Stop
            Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "  Removed: $name"
            $removed++
        } catch {
            # Task doesn't exist, skip
        }
    }

    if ($removed -eq 0) {
        Write-Info "No scheduled tasks found."
    } else {
        Write-Info "Removed $removed scheduled task(s)."
    }
}

function Main {
    Write-Host ""
    Write-Host "  Obsidian Tools Uninstaller"
    Write-Host "  =========================="
    Write-Host ""

    # ── Remove services ──────────────────────────────────────────────────

    Remove-ObsidianTasks

    # ── Remove venv ──────────────────────────────────────────────────────

    $venvDir = Join-Path $ProjectDir ".venv"
    if (Test-Path $venvDir) {
        Write-Host ""
        $answer = Read-Host "==> Remove virtual environment (.venv)? [y/N]"
        if ($answer -match "^[Yy]") {
            Remove-Item $venvDir -Recurse -Force
            Write-Info "Removed .venv\"
        } else {
            Write-Info "Kept .venv\"
        }
    }

    # ── Summary ──────────────────────────────────────────────────────────

    Write-Host ""
    Write-Info "Uninstall complete."
    Write-Host ""
    Write-Host "  Preserved:"
    Write-Host "    .env          (your configuration)"
    Write-Host "    .chroma_db\   (your search index)"
    Write-Host ""
    Write-Host "  To remove everything, delete the project directory:"
    Write-Host "    Remove-Item -Recurse -Force $ProjectDir"
    Write-Host ""
}

Main
