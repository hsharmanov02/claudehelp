param([Parameter(Mandatory=$true)][string]$KeyFile)
. (Join-Path $PSScriptRoot 'LocalSecrets.ps1')
$source = (Resolve-Path -LiteralPath $KeyFile).Path
$key = Get-Content -LiteralPath $source -Raw | ConvertFrom-Json
$keyId = $key.id
if (-not $keyId) { $keyId = $key.name }
$keySecret = $key.privateKey
if (-not $keySecret) { $keySecret = $key.secret }
if (-not $keyId -or -not $keySecret) { throw 'Downloaded file does not contain a recognized CDP API key.' }
$record = @{
    apiKeyId = [string]$keyId
    apiKeySecret = [string]$keySecret
    nickname = 'income-node-x402-local'
    importedAt = [DateTime]::UtcNow.ToString('o')
} | ConvertTo-Json -Compress
Save-LocalSecret -Name 'cdp-api-key' -Plaintext $record
$roundtrip = Read-LocalSecret -Name 'cdp-api-key'
if ($roundtrip -cne $record) { throw 'Encrypted key recovery verification failed.' }
$key = $null
$keySecret = $null
$record = $null
$roundtrip = $null
Write-Output 'CDP key encrypted locally; recovery verified. Original download is still present.'
