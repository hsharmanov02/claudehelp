. (Join-Path $PSScriptRoot 'LocalSecrets.ps1')
Initialize-LocalSecrets
$node = Get-NodeExecutable
$walletScript = Join-Path $PSScriptRoot 'wallet.cjs'
$secretPath = Join-Path $script:SecretsDirectory 'base-receiver.dpapi'
if (-not (Test-Path -LiteralPath $secretPath)) {
    $record = & $node $walletScript create
    if ($LASTEXITCODE -ne 0) { throw 'Wallet generation failed.' }
    Save-LocalSecret -Name 'base-receiver' -Plaintext $record
    $record = $null
}
$decrypted = Read-LocalSecret -Name 'base-receiver'
$verificationJson = $decrypted | & $node $walletScript inspect
if ($LASTEXITCODE -ne 0) { throw 'Encrypted wallet recovery verification failed.' }
$verification = $verificationJson | ConvertFrom-Json
$decrypted = $null
$public = [ordered]@{
    version = 1
    address = $verification.address
    network = 'eip155:8453'
    chain = 'Base'
    custody = 'Local self-custody; Windows-user DPAPI protection'
    recovery_verified = $verification.recoveryVerified
    portable_backup_created = $false
    api_key_status = 'pending'
    service_status = 'not_deployed'
}
$publicPath = Join-Path $PSScriptRoot 'receiver.json'
if (Test-Path -LiteralPath $publicPath) {
    $previous = Get-Content -LiteralPath $publicPath -Raw | ConvertFrom-Json
    if ($previous.address -ne $public.address) { throw 'Existing receiving address differs; refusing to replace it.' }
    $public.portable_backup_created = $previous.portable_backup_created
    $public.api_key_status = $previous.api_key_status
}
$public | ConvertTo-Json | Set-Content -LiteralPath $publicPath -Encoding utf8
$public | ConvertTo-Json
