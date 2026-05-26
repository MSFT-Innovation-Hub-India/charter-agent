<#
.SYNOPSIS
    Start the Project Charter Desktop Agent in the background (no console window).
.PARAMETER Force
    Kill any running instance before starting a fresh one.
.EXAMPLE
    .\start.ps1
    .\start.ps1 -Force
#>
param([switch]$Force)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DesktopDir = Split-Path $PSScriptRoot -Parent
$AppPy      = Join-Path $DesktopDir "app.py"
$Pythonw    = Join-Path $DesktopDir ".venv\Scripts\pythonw.exe"

if (-not (Test-Path $Pythonw)) {
    Write-Error "pythonw.exe not found at:`n  $Pythonw`n`nSet up the virtual environment first:`n  cd `"$DesktopDir`"`n  python -m venv .venv`n  .venv\Scripts\pip install -r requirements.txt"
    exit 1
}
if (-not (Test-Path $AppPy)) {
    Write-Error "app.py not found at $AppPy"
    exit 1
}

# Detect an instance launched from this project's venv (exact exe path match).
function Get-RunningInstance {
    $norm = $Pythonw.ToLower().Replace('/', '\')
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath.ToLower().Replace('/', '\') -eq $norm
        }
}

$running = Get-RunningInstance

if ($running) {
    if ($Force) {
        Write-Host "Stopping existing instance (PID $($running.ProcessId))..."
        Stop-Process -Id $running.ProcessId -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 800
    } else {
        Write-Host "Project Charter is already running (PID $($running.ProcessId))."
        Write-Host "Look for the icon in the system tray, or run with -Force to restart."
        exit 0
    }
}

Write-Host "Starting Project Charter Desktop Agent..."
$proc = Start-Process `
    -FilePath         $Pythonw `
    -ArgumentList     "`"$AppPy`"" `
    -WorkingDirectory $DesktopDir `
    -WindowStyle      Hidden `
    -PassThru
Write-Host "Started  (PID $($proc.Id)) - check the system tray."
