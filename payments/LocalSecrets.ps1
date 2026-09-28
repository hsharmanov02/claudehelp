$ErrorActionPreference = 'Stop'
$script:SecretsDirectory = Join-Path (Split-Path -Parent $PSScriptRoot) '.secrets'

function Initialize-LocalSecrets {
    if (-not (Test-Path -LiteralPath $script:SecretsDirectory)) {
        New-Item -ItemType Directory -Path $script:SecretsDirectory | Out-Null
    }
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl = [System.Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($sid)
    $acl.SetAccessRuleProtection($true, $false)
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'
    )
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $script:SecretsDirectory -AclObject $acl
}

function Save-LocalSecret([string]$Name, [string]$Plaintext) {
    if ($Name -notmatch '^[a-z0-9-]+$') { throw 'Invalid secret name.' }
    Initialize-LocalSecrets
    $path = Join-Path $script:SecretsDirectory ($Name + '.dpapi')
    if (Test-Path -LiteralPath $path) { throw 'Secret already exists; refusing to overwrite it.' }
    $secure = ConvertTo-SecureString -String $Plaintext -AsPlainText -Force
    try {
        $encrypted = ConvertFrom-SecureString -SecureString $secure
        [System.IO.File]::WriteAllText($path, $encrypted, [System.Text.Encoding]::ASCII)
    } finally { $secure.Dispose() }
}

function Read-LocalSecret([string]$Name) {
    if ($Name -notmatch '^[a-z0-9-]+$') { throw 'Invalid secret name.' }
    $path = Join-Path $script:SecretsDirectory ($Name + '.dpapi')
    $secure = ConvertTo-SecureString ([System.IO.File]::ReadAllText($path))
    $pointer = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        $secure.Dispose()
    }
}

function Get-NodeExecutable {
    $candidates = @(
        'C:\Program Files\nodejs\node.exe',
        (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    throw 'Node.js was not found.'
}
