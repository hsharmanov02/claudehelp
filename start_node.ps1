param([switch]$Once, [switch]$Pilot, [int]$Duration = 0)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$runtimeCandidates = @(
    (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'),
    (Join-Path $PSScriptRoot '.venv\Scripts\python.exe')
)
$runtimePath = $runtimeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $runtimePath) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) { $runtimePath = $pythonCommand.Source }
}
if (-not $runtimePath) { throw 'Python 3.11 or newer is needed. No packages or paid APIs are required.' }
$nodeArguments = @((Join-Path $PSScriptRoot 'node_engine.py'))
if ($Once) { $nodeArguments += '--once' }
if ($Pilot) { $nodeArguments += '--pilot' }
if ($Duration -gt 0) { $nodeArguments += @('--duration', [string]$Duration) }
Write-Host 'Protocol Buyer Lab — evidence observer. Default mode starts no mining workloads.'
Write-Host 'Dashboard: http://127.0.0.1:8899'
& $runtimePath @nodeArguments
exit $LASTEXITCODE

