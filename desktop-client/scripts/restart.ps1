<#
.SYNOPSIS
    Restart the Project Charter Desktop Agent.
#>
& "$PSScriptRoot\stop.ps1"
Start-Sleep -Milliseconds 500
& "$PSScriptRoot\start.ps1" -Force
