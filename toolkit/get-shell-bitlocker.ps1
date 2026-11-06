# Read-only Shell observation. Does not unlock, decrypt, enroll, or authorize writes.
param([Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z]:$')][string]$Volume)
$ErrorActionPreference = 'Stop'
$raw = $null
$state = 'Unknown'
$errorType = $null
try {
    $shell = New-Object -ComObject Shell.Application
    $folder = $shell.NameSpace($Volume + '\')
    if ($null -eq $folder -or $null -eq $folder.Self) { throw 'Volume is not available in Shell.' }
    $value = $folder.Self.ExtendedProperty('System.Volume.BitLockerProtection')
    if ($value -is [int] -or $value -is [long]) { $raw = $value }
    # Undocumented values corroborated by the referenced Chromium implementation.
    # Never copy WMI GetProtectionStatus numeric meanings into this switch.
    $state = switch ($raw) {
        1 { 'OnUnlocked' }
        2 { 'Off' }
        3 { 'EncryptionInProgress' }
        4 { 'DecryptionInProgress' }
        5 { 'Suspended' }
        6 { 'OnLocked' }
        default { 'Unknown' }
    }
} catch {
    $errorType = $_.Exception.GetType().Name
}
[pscustomobject][ordered]@{
    volume = $Volume.ToUpperInvariant()
    property = 'System.Volume.BitLockerProtection'
    raw_code = $raw
    shell_state = $state
    off_candidate = ($null -ne $raw -and $raw -eq 2)
    requires_linux_verification = $true
    error_type = $errorType
}
