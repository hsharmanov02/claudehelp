. (Join-Path $PSScriptRoot 'LocalSecrets.ps1')
$node = Get-NodeExecutable
$securePassword = Read-Host 'New wallet backup passphrase (at least 16 characters; keep it separately)' -AsSecureString
$confirmation = Read-Host 'Confirm backup passphrase' -AsSecureString
$password = [System.Net.NetworkCredential]::new('', $securePassword).Password
$confirmationText = [System.Net.NetworkCredential]::new('', $confirmation).Password
try {
    if ($password.Length -lt 16 -or $password -cne $confirmationText) { throw 'Passphrase is too short or does not match.' }
    $wallet = Read-LocalSecret -Name 'base-receiver' | ConvertFrom-Json
    $payload = @{ wallet = $wallet; password = $password } | ConvertTo-Json -Compress
    $encrypted = $payload | & $node (Join-Path $PSScriptRoot 'wallet.cjs') backup
    if ($LASTEXITCODE -ne 0) { throw 'Portable backup failed.' }
    $backupPath = Join-Path $script:SecretsDirectory 'base-receiver-keystore.json'
    if (Test-Path -LiteralPath $backupPath) { throw 'Portable backup already exists; refusing to overwrite it.' }
    [System.IO.File]::WriteAllText($backupPath, $encrypted, [System.Text.Encoding]::UTF8)
    $publicPath = Join-Path $PSScriptRoot 'receiver.json'
    $public = Get-Content -LiteralPath $publicPath -Raw | ConvertFrom-Json
    $public.portable_backup_created = $true
    $public | ConvertTo-Json | Set-Content -LiteralPath $publicPath -Encoding utf8
    Write-Output "Encrypted portable wallet backup saved to $backupPath. Copy it offline and keep its passphrase separately."
} finally {
    $securePassword.Dispose()
    $confirmation.Dispose()
    $password = $null
    $confirmationText = $null
    $payload = $null
    $wallet = $null
}
