param([switch]$Uninstall, [switch]$StartNow)
# Registers a per-user Scheduled Task that starts watchdog.py (hidden) at logon, so the node
# comes back after a reboot. No administrator rights; runs only while you are logged in.
# Remove with: .\install_watchdog.ps1 -Uninstall
$ErrorActionPreference = 'Stop'
$taskName = 'ProtocolBuyerWatchdog'

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "Removed scheduled task $taskName."
    } else {
        Write-Host "Scheduled task $taskName is not installed."
    }
    Write-Host 'If a watchdog is still running, stop it with: python watchdog.py --stop'
    exit 0
}

$candidates = @(
    (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
    (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
)
$python = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { $python = $command.Source }
}
if (-not $python) { throw 'Python 3.11 or newer is needed.' }
# pythonw runs without a console window; the watchdog launches the node with python.exe.
$pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { $pythonw = $python }

$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument ('"' + (Join-Path $PSScriptRoot 'watchdog.py') + '"') -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description 'Keeps the Protocol Buyer node running (watchdog.py).' -Force | Out-Null
Write-Host "Installed scheduled task $taskName (starts at logon) using $pythonw."
if ($StartNow) {
    Start-ScheduledTask -TaskName $taskName
    Write-Host 'Watchdog started. Status: python watchdog.py --status'
}
