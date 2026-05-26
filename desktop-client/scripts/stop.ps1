<#
.SYNOPSIS
    Stop the running Project Charter Desktop Agent.
#>
Set-StrictMode -Version Latest

$DesktopDir = Split-Path $PSScriptRoot -Parent
$Pythonw    = Join-Path $DesktopDir ".venv\Scripts\pythonw.exe"

function Get-RunningInstance {
    $norm = $Pythonw.ToLower().Replace('/', '\')
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath.ToLower().Replace('/', '\') -eq $norm
        }
}

$instances = @(Get-RunningInstance)
if (-not $instances) {
    Write-Host "Project Charter is not running."
    exit 0
}

foreach ($p in $instances) {
    Write-Host "Stopping PID $($p.ProcessId)..."
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Write-Host "Stopped."
